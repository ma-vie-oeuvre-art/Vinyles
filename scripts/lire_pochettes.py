#!/usr/bin/env python3
"""OCR conservateur des pochettes Airtable : aucune identification automatique."""
import json
import os
import re
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import urllib.parse
import urllib.request
from PIL import Image, ImageOps

BASE = "apporVr2Na6l5HGh2"
TABLE = "tblcqsKKwNW28bMSE"
API = f"https://api.airtable.com/v0/{BASE}/{TABLE}"
LIMIT = max(1, min(100, int(os.environ.get("OCR_BATCH_SIZE", "20"))))
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
WORKERS = max(1, min(2, int(os.environ.get('OCR_WORKERS', '2'))))
# Tesseract otherwise starts several OpenMP threads per pass on a two-core runner.
OCR_ENV = {**os.environ, 'OMP_THREAD_LIMIT': '1'}

def bounded(image, maximum=1600):
    image = image.copy()
    image.thumbnail((maximum, maximum), Image.Resampling.LANCZOS)
    return image

def request_json(url, token, fields=None):
    payload = json.dumps({"fields": fields}, ensure_ascii=False).encode() if fields else None
    request = urllib.request.Request(url, data=payload, method="PATCH" if fields else "GET",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=35) as resp:
        return json.load(resp)

def records(token):
    offset = None
    while True:
        args = {"pageSize": 100, "filterByFormula": "OR({Statut}='Dicté',AND({Photos personnelles},NOT({Discogs release_id})))"}
        if offset:
            args["offset"] = offset
        page = request_json(API + "?" + urllib.parse.urlencode(args), token)
        yield from page.get("records", [])
        offset = page.get("offset")
        if not offset:
            return

def scan(url):
    if isinstance(url, Path):
        data = url.read_bytes()
    else:
        req = urllib.request.Request(url, headers={"User-Agent": "VinylesInventaire/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            mime = resp.headers.get("Content-Type", "")
            if not mime.startswith("image/"):
                raise ValueError("attachment is not an image")
            data = resp.read(5_000_001)
    if len(data) > 5_000_000:
        raise ValueError("image too large")
    with tempfile.NamedTemporaryFile(suffix=".img") as f:
        # Normalize orientation and bound pixels before all OCR passes.
        with Image.open(__import__('io').BytesIO(data)) as original:
            prepared = bounded(ImageOps.exif_transpose(original).convert('RGB'))
            prepared.save(f, format='PNG')
        f.flush()
        outputs=[]
        for mode in ('3','11'):
            p = subprocess.run(["tesseract", f.name, "stdout", "-l", "eng+fra", "--psm", mode],
                text=True, capture_output=True, timeout=35, env=OCR_ENV)
            if p.returncode: raise RuntimeError("OCR failed")
            outputs.append(p.stdout)
        # Isolate broad cover zones: full-photo OCR misses large, thin lettering.
        with Image.open(f.name) as original:
            im = ImageOps.exif_transpose(original).convert('RGB')
            w, h = im.size
            for top, bottom in ((.18, .38), (.30, .65), (.60, .90)):
                crop = im.crop((0, int(h*top), w, int(h*bottom)))
                crop = ImageOps.autocontrast(ImageOps.grayscale(crop))
                crop = bounded(crop.resize((w*2, crop.height*2)))
                with tempfile.NamedTemporaryFile(suffix='.png') as zone:
                    crop.save(zone.name)
                    for mode in ('6', '11'):
                        p = subprocess.run(['tesseract', zone.name, 'stdout', '-l', 'eng', '--psm', mode],
                            text=True, capture_output=True, timeout=35, env=OCR_ENV)
                        if p.returncode: raise RuntimeError('OCR zone failed')
                        outputs.append(p.stdout)
    lines = list(dict.fromkeys(re.sub(r"\s+", " ", x).strip() for output in outputs for x in output.splitlines()))
    return " | ".join(x for x in lines if len(x) >= 3)[:16000]

def main():
    token = os.environ["AIRTABLE_TOKEN"]
    checked = 0
    recognized = 0
    errors = 0
    selected = []
    # Read a stable snapshot before updating fields used by the Airtable filter.
    rows = list(records(token))
    rows.sort(key=lambda row: (
        '[OCR pochette]' in row.get('fields', {}).get('Transcription source', ''),
        row.get('fields', {}).get('Statut') != 'Dicté',
        row.get('fields', {}).get('Bac', ''),
        row.get('fields', {}).get('Ordre dans le bac', 0)))
    for row in rows:
        f = row.get("fields", {})
        image = f.get("Photos personnelles") or f.get("Front Cover") or []
        source_match = re.search(r"fichier source: ([^\n]+)", f.get("Transcription source", ""))
        root = Path(__file__).resolve().parents[1]
        local = (root / source_match.group(1)).resolve() if source_match else None
        if local and (root not in local.parents or not local.is_file()):
            local = None
        if (not image and not local) or f.get("Discogs release_id") or f.get("Ne pas retraiter"):
            continue
        existing = f.get("Transcription source", "")
        if "[OCR zones v3]" in existing:
            continue
        if len(selected) >= LIMIT:
            break
        selected.append((row, local or image[0]['url']))

    print(f'OCR: {len(selected)} photos à lire, {WORKERS} lecteurs; nouvelles photos prioritaires.', flush=True)

    def read(item):
        row, source = item
        started = time.monotonic()
        try:
            return row, scan(source), None, time.monotonic() - started
        except Exception as exc:
            return row, None, exc, time.monotonic() - started

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
      for row, text, failure, elapsed in pool.map(read, selected):
        checked += 1
        existing = row.get('fields', {}).get('Transcription source', '')
        try:
            if failure:
                raise failure
            if not text:
                print(f"{row['id']}: texte illisible; fiche inchangée")
                continue
            recognized += 1
            # Keep prior transcriptions; OCR is a clue, never a verified identification.
            marker = '[OCR zones v3]' if '[OCR pochette]' in existing else '[OCR pochette]'
            source = (existing + "\n" if existing else "") + marker + " " + text
            if marker == '[OCR pochette]': source += '\n[OCR zones v3] lecture complète effectuée'
            source = source[:90000]
            if not DRY_RUN:
                request_json(API + "/" + row["id"], token, {
                    "Transcription source": source,
                    "Diagnostic": "Texte de pochette extrait automatiquement; identification Discogs à confirmer."
                })
            print(f"{row['id']}: OCR enregistré en {elapsed:.1f}s" + (" (simulation)" if DRY_RUN else ""), flush=True)
        except Exception as exc:
            errors += 1
            print(f"{row['id']}: OCR non disponible ({type(exc).__name__}); fiche inchangée")
    print(f"OCR: {checked} photo(s) examinée(s), {recognized} texte(s) extrait(s).")

    if errors:
        raise SystemExit(f"{errors} lecture(s) OCR en échec technique")

if __name__ == "__main__":
    main()


