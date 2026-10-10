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
from enrichir_vinyles import request_json, API, BASE, TABLE
from reconnaitre_albums import words

ROOT = Path(__file__).resolve().parents[1]
SELECTION = Path(os.environ.get('RUNNER_TEMP', '/tmp')) / 'vinyles-neural-selection.json'

def marker(source):
    match = re.search(r'fichier source: ([^\n]+)', source)
    path = (ROOT / match[1]).resolve() if match else None
    if path and ROOT in path.parents and path.is_file():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    else:
        digest = hashlib.sha256((match[1].strip() if match else source.split('[OCR pochette]')[0].strip()).encode()).hexdigest()[:16]
    return '[OCR neural v1:' + digest + ']'

def needs_review(fields):
    if fields.get('Ne pas retraiter') or fields.get('Discogs release_id') or fields.get("Preuve d'identification") == 'Validation humaine':
        return False
    if not fields.get('Artiste') or not fields.get('Titre'):
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
    selected.sort(key=lambda row: (row['fields'].get('Bac', ''), row['fields'].get('Ordre dans le bac', 0)))
    selected = selected[:int(os.environ.get('NEURAL_BATCH_SIZE', '20'))]
    SELECTION.write_text(json.dumps(selected), encoding='utf-8')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
            out.write(f'count={len(selected)}\n')
    print(f'Second lecteur: {len(selected)} pochette(s) en attente.', flush=True)
    return selected

def image_bytes(fields):
    match = re.search(r'fichier source: ([^\n]+)', fields.get('Transcription source', ''))
    path = (ROOT / match[1]).resolve() if match else None
    if path and ROOT in path.parents and path.is_file():
        return path.read_bytes()
    url = fields['Photos personnelles'][0]['url']
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

def read():
    import easyocr
    import numpy as np
    import torch
    from PIL import Image, ImageOps
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
            with Image.open(io.BytesIO(image_bytes(fields))) as original:
                image = ImageOps.exif_transpose(original).convert('RGB')
                image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
                results = reader.readtext(np.asarray(image), decoder='beamsearch', batch_size=1, workers=0)
            lines = structured_lines(results)
            text = ' | '.join(line['text'] for line in lines)
            addition = ('\n[Vérification du titre automatique]' if fields.get('Artiste') and fields.get('Titre') else '') + '\n' + stamp + '\n[OCR neural texte] ' + text + '\n[OCR neural regions] ' + json.dumps(lines, ensure_ascii=False)
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
