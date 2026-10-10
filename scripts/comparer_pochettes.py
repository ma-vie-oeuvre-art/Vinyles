"""Comparer la photo aux pochettes candidates; identifier l'album, jamais le pressage."""
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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from enrichir_vinyles import request_json, API, BASE, TABLE
from reconnaitre_albums import words, album_title, neural_evidence

ROOT = Path(__file__).resolve().parents[1]
SELECTION = Path(os.environ.get('RUNNER_TEMP', '/tmp')) / 'vinyles-visual-selection.json'

def album_key(candidate):
    master = candidate.get('master_id')
    if str(master).isdigit() and int(master) > 0:
        return ('master', int(master))
    credit, _, title = candidate.get('titre', '').rpartition(' - ')
    return (tuple(words(credit)), tuple(words(album_title(title))))

def fingerprint(fields):
    candidates = json.loads(fields.get('Candidats Discogs') or '[]')
    match = re.search(r'fichier source: ([^\n]+)', fields.get('Transcription source', ''))
    evidence = [match[1] if match else fields.get('Clé'), sorted((str(c.get('release_id')), c.get('cover_url', '')) for c in candidates)]
    return '[comparaison-visuelle-v2:' + hashlib.sha256(json.dumps(evidence).encode()).hexdigest()[:16] + ']'

def should_compare(fields):
    if fingerprint(fields) not in fields.get('Diagnostic', ''):
        return True
    retry = re.search(r'Recherche visuelle suivante : (\d{4}-\d{2}-\d{2})', fields.get('Diagnostic', ''))
    today = datetime.now(ZoneInfo('America/Toronto')).date().isoformat()
    return not retry or retry[1] <= today

def headers():
    return {'Authorization': 'Bearer ' + os.environ['AIRTABLE_TOKEN'], 'Content-Type': 'application/json'}

def select():
    rows = []
    offset = None
    while True:
        args = {'pageSize': 100, 'filterByFormula': 'AND({Photos personnelles},OR(NOT({Artiste}),NOT({Titre})),NOT({Ne pas retraiter}),NOT({Discogs release_id}))'}
        if offset:
            args['offset'] = offset
        page = request_json(f'{API}/{BASE}/{TABLE}?' + urllib.parse.urlencode(args), headers())
        for row in page.get('records', []):
            f = row['fields']
            if f.get("Preuve d'identification") == 'Validation humaine':
                continue
            try:
                candidates = json.loads(f.get('Candidats Discogs') or '[]')
                if any(c.get('cover_url') for c in candidates) and should_compare(f):
                    rows.append(row)
            except (ValueError, TypeError):
                continue
        offset = page.get('offset')
        if not offset:
            break
    rows.sort(key=lambda row: (row['fields'].get('Bac', ''), row['fields'].get('Ordre dans le bac', 0)))
    rows = rows[:20]
    SELECTION.write_text(json.dumps(rows), encoding='utf-8')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write(f'count={len(rows)}\n')
    print(f'Comparaison visuelle: {len(rows)} photo(s) à comparer.', flush=True)

def features(data):
    import cv2
    import numpy as np
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(data)) as original:
        image = ImageOps.exif_transpose(original).convert('L')
        image.thumbnail((1100, 1100), Image.Resampling.LANCZOS)
        pixels = np.asarray(image)
    points, descriptors = cv2.SIFT_create(nfeatures=2500).detectAndCompute(pixels, None)
    return pixels.shape, points, descriptors

def compare(photo, reference):
    import cv2
    import numpy as np
    if photo[2] is None or reference[2] is None:
        return 0
    matches = cv2.BFMatcher().knnMatch(reference[2], photo[2], k=2)
    good = [pair[0] for pair in matches if len(pair) == 2 and pair[0].distance < .68 * pair[1].distance]
    if len(good) < 14:
        return 0
    src = np.float32([reference[1][m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([photo[1][m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    matrix, mask = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
    if matrix is None or mask is None:
        return 0
    inliers = int(mask.sum())
    if inliers < 14 or inliers / len(good) < .55:
        return 0
    selected = src[mask.ravel().astype(bool)]
    coverage = cv2.contourArea(cv2.convexHull(selected)) / (reference[0][0] * reference[0][1])
    if coverage < .20:
        return 0
    h, w = reference[0]
    corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
    projected = cv2.perspectiveTransform(corners, matrix)
    ph, pw = photo[0]
    area = abs(cv2.contourArea(projected)) / (ph * pw)
    if not .025 < area < 1.4 or not np.isfinite(projected).all():
        return 0
    return inliers

def download(url):
    if not str(url).startswith('https://'):
        raise ValueError('URL de pochette invalide')
    proxy = 'https://images.weserv.nl/?url=' + urllib.parse.quote(url, safe='') + '&output=jpg&w=1100'
    with urllib.request.urlopen(proxy, timeout=20) as response:
        data = response.read(5_000_001)
    if len(data) > 5_000_000:
        raise ValueError('Pochette trop volumineuse')
    return data

def run():
    import cv2
    cv2.setNumThreads(2)
    rows = json.loads(SELECTION.read_text(encoding='utf-8'))
    cache = {}
    errors = 0
    for row in rows:
        started = time.monotonic()
        f = request_json(f'{API}/{BASE}/{TABLE}/' + row['id'], headers())['fields']
        if f.get("Preuve d'identification") == 'Validation humaine' or f.get('Ne pas retraiter') or f.get('Discogs release_id') or (f.get('Artiste') and f.get('Titre')):
            continue
        candidates = json.loads(f.get('Candidats Discogs') or '[]')
        stamp = fingerprint(f)
        if not should_compare(f):
            continue
        match = re.search(r'fichier source: ([^\n]+)', f.get('Transcription source', ''))
        path = (ROOT / match[1]).resolve() if match else None
        if not path or ROOT not in path.parents or not path.is_file():
            errors += 1
            print(f"{row['id']}: photo source indisponible; reprise automatique nécessaire", flush=True)
            continue
        photo = features(path.read_bytes())
        chosen = []
        album_counts = {}
        urls = set()
        for c in candidates:
            credit, sep, title = c.get('titre', '').rpartition(' - ')
            key = album_key(c)
            url = c.get('cover_url')
            if not sep or not url or url in urls or album_counts.get(key, 0) >= 8:
                continue
            chosen.append(c)
            urls.add(url)
            album_counts[key] = album_counts.get(key, 0) + 1
            if len(chosen) >= 120:
                break

        def load(c):
            url = c['cover_url']
            try:
                if url not in cache:
                    cache[url] = features(download(url))
                return c, cache[url], None
            except Exception as exc:
                return c, None, type(exc).__name__

        scores = []
        unavailable = 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            for c, reference, failure in pool.map(load, chosen):
                if failure:
                    unavailable += 1
                    continue
                score = compare(photo, reference)
                if score:
                    scores.append((score, c))
        if not scores:
            # A release search can be monopolized by variants of popular albums.
            # Search masters as well: one entry per album exposes rarer artists.
            source = f.get('Transcription source', '')
            queries, _ = neural_evidence(source)
            text = source.partition('[OCR neural texte]')[2].split('[OCR neural regions]')[0]
            variants = text.replace('@', 'O')
            variants = re.sub(r'\bZ[Oo0]{2}1', '2001', variants, flags=re.I)
            individual = [w for w in words(variants) if len(w) >= 4 and w not in {'STEREO','MONO','RECORDS','VOLUME','POLYDOR','CORAL','DECCA'}]
            queries = list(dict.fromkeys(queries[:4] + individual[:8]))[:10]
            dg = {'Authorization': 'Discogs token=' + os.environ['DISCOGS_TOKEN'], 'User-Agent': 'VinylesInventaire/1.0 (personal catalog)'}
            masters = {}
            for query in queries:
                result = request_json('https://api.discogs.com/database/search?' + urllib.parse.urlencode({'q': query[:100], 'type': 'master', 'format': 'Vinyl', 'per_page': 100}), dg)
                time.sleep(1.2)
                for item in result.get('results', []):
                    if item.get('cover_image') and ' - ' in item.get('title', ''):
                        masters[item['id']] = {'master_id': item['id'], 'master_candidate': True, 'titre': item['title'], 'cover_url': item['cover_image']}
            visible = set(words(source + ' ' + variants))
            def priority(item):
                return sum(w in visible or any(len(v) >= 6 and w.startswith(v) for v in visible) for w in set(words(item['titre'])) if len(w) >= 4)
            master_candidates = sorted(masters.values(), key=priority, reverse=True)[:100]
            master_candidates = [c for c in master_candidates if c['cover_url'] not in urls]
            with ThreadPoolExecutor(max_workers=4) as pool:
                for c, reference, failure in pool.map(load, master_candidates):
                    if failure:
                        unavailable += 1
                        continue
                    score = compare(photo, reference)
                    if score:
                        scores.append((score, c))
            print(f"{row['id']}: {len(chosen)} versions et {len(master_candidates)} albums comparés", flush=True)
        scores.sort(key=lambda item: item[0], reverse=True)
        groups = {}
        for score, c in scores:
            credit, _, title = c['titre'].rpartition(' - ')
            key = album_key(c)
            groups.setdefault(key, []).append((score, c))
        updates = {}
        if len(groups) == 1:
            best = scores[0][1]
            artist, _, title = best['titre'].rpartition(' - ')
            matches = [c for _, c in scores]
            resolved = []
            for candidate in matches:
                if candidate.get('master_candidate'):
                    dg = {'Authorization': 'Discogs token=' + os.environ['DISCOGS_TOKEN'], 'User-Agent': 'VinylesInventaire/1.0 (personal catalog)'}
                    master = request_json('https://api.discogs.com/masters/' + str(candidate['master_id']), dg)
                    time.sleep(1.2)
                    if not master.get('main_release'):
                        raise ValueError('Album sans version de référence')
                    candidate = {**candidate, 'release_id': master['main_release']}
                resolved.append(candidate)
            matches = resolved
            updates.update({'Transcription source': f.get('Transcription source', '') if '[Identité visuelle vérifiée]' in f.get('Transcription source', '') else f.get('Transcription source', '') + '\n[Identité visuelle vérifiée]', 'Artiste': artist, 'Titre': album_title(title), 'Candidats Discogs': json.dumps(matches, ensure_ascii=False),
                'Diagnostic': f'Album reconnu automatiquement par correspondances géométriques des pochettes ({scores[0][0]} points concordants); pressage précis non attribué.'})
            print(f"{row['id']}: album reconnu par image ({scores[0][0]} points)", flush=True)
        elif unavailable:
            errors += 1
            print(f"{row['id']}: {unavailable} pochette(s) inaccessible(s); comparaison à reprendre automatiquement", flush=True)
            continue
        else:
            updates['Diagnostic'] = f.get('Diagnostic', '') + f'\nComparaison automatique: {len(chosen)} visuels examinés, {len(groups)} album(s) visuellement concordant(s).\n' + stamp + '\nRecherche visuelle suivante : ' + (datetime.now(ZoneInfo('America/Toronto')).date() + timedelta(days=1)).isoformat()
        request_json(f'{API}/{BASE}/{TABLE}/' + row['id'], headers(), {'fields': updates})
        print(f"{row['id']}: comparaison terminée en {time.monotonic()-started:.1f}s", flush=True)
    if errors:
        raise SystemExit(f'{errors} comparaison(s) en échec technique; reprise automatique au prochain passage')

if __name__ == '__main__':
    select() if '--select' in sys.argv else run()
