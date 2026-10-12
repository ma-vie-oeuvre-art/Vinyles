"""Second lecteur automatique, utilisé uniquement après une reconnaissance incertaine."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.parse
import urllib.request
import csv
import subprocess
import tempfile
from enrichir_vinyles import request_json, API, BASE, TABLE
from reconnaitre_albums import words

ROOT = Path(__file__).resolve().parents[1]
SELECTION = Path(os.environ.get('RUNNER_TEMP', '/tmp')) / 'vinyles-neural-selection.json'

def marker(source):
    paths = re.findall(r'fichier source: ([^\n]+)', source)
    evidence = []
    for name in paths:
        path = (ROOT / name.strip()).resolve()
        evidence.append(hashlib.sha256(path.read_bytes()).hexdigest() if ROOT in path.parents and path.is_file() else name.strip())
    digest = hashlib.sha256('|'.join(evidence or [source.split('[OCR pochette]')[0].strip()]).encode()).hexdigest()[:16]
    return '[OCR neural v2:' + digest + ']'

def needs_review(fields):
    if fields.get('Ne pas retraiter') or fields.get('Discogs release_id') or fields.get("Preuve d'identification") == 'Validation humaine':
        return False
    if not fields.get('Artiste') or not fields.get('Titre'):
        return True
    if not str(fields.get('Discogs référence release_id','')).isdigit():
        return True
    artist = re.sub(r'\s*\(\d+\)\s*$', '', fields['Artiste']).strip('* ')
    return '[OCR pochette]' in fields.get('Transcription source', '') and words(artist) == words(fields['Titre'])

def headers():
    return {'Authorization': 'Bearer ' + os.environ['AIRTABLE_TOKEN'], 'Content-Type': 'application/json'}

def select():
    selected = []
    offset = None
    while True:
        args = {'pageSize': 100, 'filterByFormula': "{Photos personnelles}"}
        if offset:
            args['offset'] = offset
        page = request_json(f'{API}/{BASE}/{TABLE}?' + urllib.parse.urlencode(args), headers())
        for row in page.get('records', []):
            source = row['fields'].get('Transcription source', '')
            if needs_review(row['fields']) and marker(source) not in source:
                selected.append(row)
        offset = page.get('offset')
        if not offset:
            break
    selected.sort(key=lambda row: row['fields'].get('Ordre dans le bac', 0))
    selected.sort(key=lambda row: row['fields'].get('Bac', ''), reverse=True)
    selected = selected[:int(os.environ.get('NEURAL_BATCH_SIZE', '8'))]
    SELECTION.write_text(json.dumps(selected), encoding='utf-8')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
            out.write(f'count={len(selected)}\n')
    print(f'Second lecteur: {len(selected)} pochette(s) en attente.', flush=True)
    return selected

def image_bytes(fields, index=0):
    paths = re.findall(r'fichier source: ([^\n]+)', fields.get('Transcription source', ''))
    path = (ROOT / paths[index].strip()).resolve() if index < len(paths) else None
    if path and ROOT in path.parents and path.is_file():
        return path.read_bytes()
    url = fields['Photos personnelles'][index]['url']
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(20_000_001)
    if len(data) > 20_000_000:
        raise ValueError('Photo trop volumineuse')
    return data

def structured_lines(results):
    lines = []
    for box, text, confidence in results:
        text = re.sub(r'\s+', ' ', str(text)).strip()
        if confidence < .20 or len(text) < 2:
            continue
        height = max(p[1] for p in box) - min(p[1] for p in box)
        lines.append({'text': text, 'confidence': round(float(confidence), 3),
                      'height': round(float(height), 1), 'y': round(float(min(p[1] for p in box)), 1)})
    return sorted(lines, key=lambda line: (-line['height'], line['y']))

def complementary_lines(pixels):
    """Retain independent high-confidence reading of stylised large lettering."""
    from PIL import Image
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'cover.png'
        Image.fromarray(pixels).save(path)
        result = subprocess.run(['tesseract', str(path), 'stdout', '-l', 'eng+fra', '--psm', '11', 'tsv'],
                                capture_output=True, text=True, timeout=45, check=True)
    groups = {}
    for row in csv.DictReader(io.StringIO(result.stdout), delimiter='\t'):
        if not row.get('text', '').strip() or float(row.get('conf') or -1) < 45:
            continue
        key = (row['block_num'], row['par_num'], row['line_num'])
        groups.setdefault(key, []).append(row)
    return [{'text':' '.join(row['text'] for row in rows),
             'confidence':round(sum(float(row['conf']) for row in rows) / len(rows) / 100, 3),
             'height':max(float(row['height']) for row in rows),
             'y':min(float(row['top']) for row in rows)} for rows in groups.values()]

def read():
    import easyocr
    import numpy as np
    import torch
    from PIL import Image, ImageOps
    from lecture_images import cover_pixels, useful_region
    torch.set_num_threads(2)
    rows = json.loads(SELECTION.read_text(encoding='utf-8'))
    if not rows:
        return
    reader = easyocr.Reader(['fr', 'en'], gpu=False, verbose=False)
    errors = 0
    for row in rows:
        started = time.monotonic()
        try:
            # Refresh before writing: never replace a concurrent transcription.
            current = request_json(f'{API}/{BASE}/{TABLE}/' + row['id'], headers())
            fields = current['fields']
            source = fields.get('Transcription source', '')
            stamp = marker(source)
            if stamp in source or fields.get('Ne pas retraiter') or fields.get('Discogs release_id') or not needs_review(fields):
                continue
            lines = []
            for index in range(min(4, len(fields.get('Photos personnelles') or []))):
                with Image.open(io.BytesIO(image_bytes(fields, index))) as original:
                    image = ImageOps.exif_transpose(original).convert('RGB')
                    image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
                    pixels = cover_pixels(image)
                # Native colour and contrast keep stylised or red lettering readable.
                gray = np.min(pixels, axis=2).astype(np.uint8)
                variants = [pixels, np.asarray(ImageOps.autocontrast(Image.fromarray(gray)))]
                for variant in variants:
                    results = reader.readtext(variant, decoder='beamsearch', batch_size=1, workers=0,
                                              rotation_info=[180], text_threshold=.5, low_text=.25)
                    for line in structured_lines(results):
                        if useful_region(line):
                            line['photo'] = index
                            lines.append(line)
                for line in complementary_lines(variants[1]):
                    if useful_region(line):
                        line['photo'] = index
                        lines.append(line)
            # Only the foreground recto determines the main heading; other photos
            # still contribute catalogue and label evidence in the complete text.
            principal = [line for line in lines if line.get('photo', 0) == 0]
            text = ' | '.join(line['text'] for line in lines)
            source = re.sub(r'\n\[OCR neural (?:texte|regions)\] [^\n]*', '', source)
            addition = ('\n[Vérification du titre automatique]' if fields.get('Artiste') and fields.get('Titre') else '') + '\n' + stamp + '\n[OCR neural texte] ' + text + '\n[OCR neural regions] ' + json.dumps(principal, ensure_ascii=False)
            request_json(f'{API}/{BASE}/{TABLE}/' + row['id'], headers(), {'fields': {
                'Transcription source': source[:65000] + addition[:24000],
                'Diagnostic': 'Deuxième lecture automatique effectuée dans GitHub; recherche Discogs en cours.'}})
            print(f"{row['id']}: {len(lines)} zones lues par le second moteur en {time.monotonic()-started:.1f}s", flush=True)
        except Exception as exc:
            errors += 1
            print(f"{row['id']}: échec technique du second lecteur ({type(exc).__name__})", flush=True)
    if errors:
        raise SystemExit(f'{errors} lecture(s) à reprendre automatiquement au prochain passage')

if __name__ == '__main__':
    select() if '--select' in sys.argv else read()
