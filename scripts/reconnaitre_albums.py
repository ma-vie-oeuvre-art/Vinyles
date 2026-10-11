"""GitHub recherche l'album à partir du texte de la photo, sans inventer un pressage."""
import json
import os
import re
import time
import unicodedata
import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import urllib.parse
from enrichir_vinyles import request_json, API, BASE, TABLE

def words(text):
    # Keep Cyrillic evidence instead of silently dropping every title letter.
    alphabet = dict(zip('АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЬЮЯ',
                        ['A','B','V','G','D','E','ZH','Z','I','Y','K','L','M','N','O','P','R','S','T','U','F','H','TS','CH','SH','SHT','A','','YU','YA']))
    text = ''.join(alphabet.get(c, c) for c in str(text).upper())
    text = unicodedata.normalize('NFKD', str(text)).encode('ascii', 'ignore').decode().upper()
    return re.findall(r'[A-Z0-9]+', text)

def album_title(title):
    return re.sub(r'\s*\(([^)]*)\)\s*$', lambda match: match[0] if re.search(r'\b(?:vol\.?|volume|part|[IVX]+|[0-9]+)\b', match[1], re.I) else '', title).strip()

def self_titled(credit, title):
    credit = re.sub(r'\s*\(\d+\)\s*$', '', str(credit)).strip('* ')
    return bool(words(credit)) and words(credit) == words(album_title(title))

def supported(ocr, credit, title):
    visible = set(words(ocr))
    title = album_title(title)
    title_words = [w for w in words(title) if (len(w) >= 3 or w.isdigit() or w in {'I','II','III','IV','V','VI','VII','VIII','IX','X'}) and w not in {'THE', 'AND', 'VOL'}]
    artist_words = [w for w in words(credit) if len(w) >= 3 and w not in {'THE', 'AND', 'HIS', 'HER', 'ORCHESTRA', 'ORCHESTRE', 'WITH'}]
    if not title_words or not all(w in visible for w in title_words):
        return False
    if credit.upper() in {'VARIOUS', 'NO ARTIST'}:
        return len(title_words) >= 3
    return bool(artist_words) and sum(w in visible for w in artist_words) / len(artist_words) >= 0.65

def recognition_stamp(ocr):
    return '[recognition-v4:' + hashlib.sha256(ocr.encode()).hexdigest()[:12] + ']'

def should_search(ocr, diagnostic, today):
    if recognition_stamp(ocr) not in diagnostic:
        return True
    retry = re.search(r'Recherche album suivante : (\d{4}-\d{2}-\d{2})', diagnostic)
    return not retry or retry[1] <= today.isoformat()

def catalogues(ocr):
    # Search hints only: never assign a physical catalogue from this extraction.
    known = {'CL','CS','CRL','DL','UAS','UAL','MS','LS','LP','ST','SF','PS','PAS','SD','SP','SR','LSP','LM','CPS','SPC','RS','SPS','SPFL'}
    return list(dict.fromkeys(prefix + digits for prefix, digits in
        re.findall(r'\b([A-Z]{1,4})[ -]*(\d{3,7})\b', ocr.upper()) if prefix in known))[:3]

def capture_queries(fields):
    """Use captured descriptions only as search hints; image evidence still decides."""
    source = fields.get('Transcription source', '').split('\nPhoto personnelle')[0].split('[OCR pochette]')[0]
    source = re.split(r'Source photo:|IMG_\d+|\bPochette\b|\bNuméro de catalogue\b', source, flags=re.I)[0].strip(' .;\n')
    if not source or source.startswith(('Photo personnelle', 'Pochette illisible')):
        return []
    return [source[:100]]

def collect(result, ocr, albums, candidates, catalogue=None, corroborated_catalogue=False):
    for item in result.get('results', []):
        if catalogue and re.sub(r'[^A-Z0-9]', '', str(item.get('catno', '')).upper()) != catalogue:
            continue
        credit, sep, title = str(item.get('title', '')).rpartition(' - ')
        if not sep or not (corroborated_catalogue or supported(ocr, credit, title)):
            continue
        title = album_title(title)
        master = item.get('master_id')
        key = ('master', int(master)) if master else (tuple(words(credit)), tuple(words(title)))
        albums[key] = (credit, title)
        candidates[item['id']] = {'release_id': item['id'], 'master_id': item.get('master_id'), 'titre': item['title'], 'catno': item.get('catno'), 'label': item.get('label'), 'pays': item.get('country'), 'annee': item.get('year')}

def neural_evidence(source):
    match = re.search(r'\[OCR neural regions\] ([^\n]+)', source)
    if not match:
        return [], ''
    try:
        regions = json.loads(match[1])
    except (ValueError, TypeError):
        return [], ''
    regions = [r for r in regions if r.get('confidence', 0) >= .35 and words(r.get('text', ''))]
    if not regions:
        return [], ''
    maximum = max(r['height'] for r in regions)
    prominent = [r for r in regions if r['height'] >= maximum * .45][:8]
    ordered = sorted(prominent, key=lambda r: r['y'])
    texts = [r['text'] for r in prominent]
    queries = texts[:5] + [' '.join(r['text'] for r in ordered[i:i+2]) for i in range(min(4, len(ordered)-1))]
    # The full OCR remains the corroboration source; large lettering breaks ties.
    return list(dict.fromkeys(queries)), ' | '.join(texts)

def reset_reference(fields):
    if 'metadata-v2' not in fields.get('Enrichissement automatique', ''):
        return {}
    keys = ['Discogs référence release_id', 'Discogs référence album', 'Version de référence — détails', 'Tracklist JSON', 'Nombre de plages', 'Titres — index normalisé', 'Famille', 'Familles', "Année de l'album", 'Possédé par (Discogs)', 'Recherché par (Discogs)', 'Note Discogs (sur 5)', 'Nombre de notes (Discogs)', 'Exemplaires en vente (Discogs)', 'Valeur minimale', 'Prix suggéré VG+ (Discogs)', 'Prix suggéré Mint (Discogs)', "Source de l'estimation", "Date de l'estimation", "Qualité d'enregistrement (source)", 'Informations historiques', 'Enrichissement automatique']
    updates = {key: None for key in keys if key in fields}
    if not fields.get('Images verrouillées'):
        updates.update({key: [] for key in ['Front Cover', 'Back Cover'] if key in fields})
        if 'Images Discogs — URLs sources' in fields:
            updates['Images Discogs — URLs sources'] = None
    return updates

def main():
    at = {'Authorization': 'Bearer ' + os.environ['AIRTABLE_TOKEN'], 'Content-Type': 'application/json'}
    dg = {'Authorization': 'Discogs token=' + os.environ['DISCOGS_TOKEN'], 'User-Agent': 'VinylesInventaire/1.0 (personal catalog)'}
    endpoint = f'{API}/{BASE}/{TABLE}'
    cursor = None
    rows = []
    while True:
        args = {'pageSize': 100, 'filterByFormula': '{Photos personnelles}'}
        if cursor:
            args['offset'] = cursor
        page = request_json(endpoint + '?' + urllib.parse.urlencode(args), at)
        rows += page.get('records', [])
        cursor = page.get('offset')
        if not cursor:
            break
    today = datetime.now(ZoneInfo('America/Toronto')).date()
    cache = {}
    checked = 0
    last_call = 0.0
    rows.sort(key=lambda row: (row['fields'].get('Statut') != 'Dicté', row['fields'].get('Bac', ''), row['fields'].get('Ordre dans le bac', 0)))
    for row in rows:
        f = row['fields']
        if f.get('Ne pas retraiter') or f.get('Discogs release_id') or f.get("Preuve d'identification") == 'Validation humaine':
            continue
        existing_self_title = self_titled(f.get('Artiste', ''), f.get('Titre', ''))
        if f.get('Artiste') and f.get('Titre') and not existing_self_title:
            continue
        source = f.get('Transcription source', '')
        if existing_self_title and '[Identité visuelle vérifiée]' in source:
            continue
        ocr = (source.partition('[OCR pochette]')[2] or source.partition('[OCR neural texte]')[2]).strip()
        neural_queries, prominent = neural_evidence(source)
        checking_title = '[Vérification du titre automatique]' in source or existing_self_title
        ocr = re.sub(r'\[OCR neural regions\] [^\n]+', '', ocr)
        if not ocr:
            continue
        # Re-evaluate recorded evidence before making more network requests.
        # This also applies fixes immediately to a result scheduled for tomorrow.
        prior_albums, prior_candidates = {}, {}
        try:
            prior = json.loads(f.get('Candidats Discogs') or '[]')
        except (ValueError, TypeError):
            prior = []
        collect({'results': [{'id': item['release_id'], 'title': item['titre'], 'master_id': item.get('master_id'), 'catno': item.get('catno')} for item in prior if isinstance(item, dict) and item.get('release_id') and item.get('titre')]}, ocr, prior_albums, prior_candidates)
        primary = {key: value for key, value in prior_albums.items() if prominent and supported(prominent, *value) and not self_titled(*value)}
        specific = {key: value for key, value in primary.items() if set(words(value[1])) - set(words(value[0])) - {'THE', 'AND', 'VOL'}}
        if specific:
            primary = specific
        if len(primary) == 1:
            artist, title = next(iter(primary.values()))
            retained = [item for item in prior if supported(prominent, *str(item.get('titre', '')).rpartition(' - ')[::2])]
            reset = reset_reference(f) if checking_title and (words(artist), words(title)) != (words(f.get('Artiste','')), words(f.get('Titre',''))) else {}
            request_json(endpoint + '/' + row['id'], at, {'fields': {**reset, 'Transcription source': source if '[Titre vérifié automatiquement]' in source else source + '\n[Titre vérifié automatiquement]', 'Artiste': artist, 'Titre': title.strip(), 'Candidats Discogs': json.dumps(retained, ensure_ascii=False), 'Diagnostic': 'Album reconnu automatiquement par GitHub après vérification des textes principaux; pressage précis reporté au besoin. [Titre vérifié automatiquement]'}})
            print(f'{row["id"]}: album reconnu depuis les preuves conservées, sans nouvelle recherche', flush=True)
            continue
        if not should_search(ocr, f.get('Diagnostic', ''), today):
            continue
        if checked >= 20:
            break
        checked += 1
        started = time.monotonic()
        lines = [s.strip() for s in ocr.replace('\n', ' | ').split(' | ') if 1 <= len(words(s)) <= 10 and sum(len(w) >= 4 for w in words(s)) >= 1]
        lines.sort(key=lambda s: (sum(len(w) >= 4 for w in words(s)) >= 2, sum(len(w) for w in words(s))/len(words(s))), reverse=True)
        queries = list(dict.fromkeys(capture_queries(f) + neural_queries + lines[:6] + [' '.join(lines[i:i+2]) for i in range(min(4, len(lines)-1))]))
        albums = {}
        candidates = {}
        visual_candidates = {}
        def search(params):
            nonlocal last_call
            url = 'https://api.discogs.com/database/search?' + urllib.parse.urlencode(params)
            if url not in cache:
                time.sleep(max(0, 2.2 - (time.monotonic() - last_call)))
                last_call = time.monotonic()
                cache[url] = request_json(url, dg)
            for item in cache[url].get('results', []):
                if item.get('type') == 'release' and item.get('cover_image') and ' - ' in item.get('title', ''):
                    visual_candidates[item['id']] = {'release_id': item['id'], 'master_id': item.get('master_id'), 'titre': item['title'], 'catno': item.get('catno'), 'label': item.get('label'), 'pays': item.get('country'), 'annee': item.get('year'), 'cover_url': item['cover_image']}
            return cache[url]
        physical_hint = re.sub(r'[^A-Z0-9]', '', str(f.get('N° catalogue', '')).upper())
        hints = list(dict.fromkeys(([physical_hint] if physical_hint else []) + catalogues(ocr)))[:4]
        initial_text = ocr.split('[OCR neural v1:')[0]
        neural_text = source.partition('[OCR neural texte]')[2].split('[OCR neural regions]')[0]
        corroborated = set(catalogues(initial_text)) & set(catalogues(neural_text))
        corroborated = {hint for hint in corroborated if len(re.sub(r'[^0-9]', '', hint)) >= 5}
        catalogue_verified = False
        complete = True
        for hint in hints:
            result = search({'catno': hint, 'type': 'release', 'format': 'Vinyl', 'per_page': 100})
            complete = complete and int(result.get('pagination', {}).get('pages', 1)) <= 1
            collect(result, ocr, albums, candidates, hint, hint in corroborated)
        physical_match = physical_hint and any(re.sub(r'[^A-Z0-9]', '', str(c.get('catno', '')).upper()) == physical_hint for c in candidates.values())
        catalogue_verified = bool((corroborated or physical_match) and complete and len(albums) == 1)
        # Stop only when all exact catalogue results support a single visible album.
        # Ambiguous, partial or unsuccessful catalogue searches keep the full fallback.
        if not (hints and complete and len(albums) == 1):
            for query in queries[:10]:
                result = search({'q': query[:100], 'type': 'release', 'format': 'Vinyl', 'per_page': 100})
                collect(result, ocr, albums, candidates)
        if len(albums) > 1 and prominent:
            strong = {key: value for key, value in albums.items() if supported(prominent, *value)}
            # An artist's name also matches every self-titled search result.
            # Prefer an independently printed album title when one is visible.
            specific = {key: value for key, value in strong.items() if set(words(value[1])) - set(words(value[0])) - {'THE', 'AND', 'VOL'}}
            if specific:
                strong = specific
            if len(strong) == 1:
                albums = strong
                artist, title = next(iter(strong.values()))
                candidates = {key: value for key, value in candidates.items() if value['titre'].rpartition(' - ')[0] == artist and album_title(value['titre'].rpartition(' - ')[2]) == title}
        if not catalogue_verified:
            albums = {key: value for key, value in albums.items() if not self_titled(*value)}
        if checking_title and not catalogue_verified:
            albums = {key: value for key, value in albums.items() if supported(prominent, *value)}
        updates = {'Candidats Discogs': json.dumps(list(candidates.values()), ensure_ascii=False)}
        if len(albums) == 1:
            artist, title = next(iter(albums.values()))
            if checking_title and (words(artist), words(title)) != (words(f.get('Artiste','')), words(f.get('Titre',''))):
                updates.update(reset_reference(f))
            updates.update({'Transcription source': source if '[Titre vérifié automatiquement]' in source else source + '\n[Titre vérifié automatiquement]', 'Artiste': artist, 'Titre': title, 'Diagnostic': ('Album reconnu par catalogue concordant dans les deux lectures automatiques; aucune édition physique attribuée.' if catalogue_verified else 'Album reconnu par GitHub à partir du texte de la photo; pressage précis reporté au besoin.') + ' [Titre vérifié automatiquement]'})
            print(f'{row["id"]}: album reconnu')
        else:
            updates.update({'Statut': 'À revoir', 'Diagnostic': f'Lecture et recherche automatiques effectuées : {len(albums)} album(s) concordant(s). Photo conservée; identification encore incertaine.'})
            print(f'{row["id"]}: identification incertaine')
            if checking_title and f.get('Artiste') and f.get('Titre') and (existing_self_title or not supported(prominent, f['Artiste'], f['Titre'])):
                # A small track name must not retain an unrelated album reference.
                updates.update({'Artiste': None, 'Titre': None, 'Candidats Discogs': '[]'})
                updates.update(reset_reference(f))
                updates['Diagnostic'] = 'Ancienne attribution automatique retirée: les textes seuls ne confirmaient pas ce titre; comparaison des pochettes nécessaire. Recherche automatique poursuivie.'
            visible = set(words(ocr))
            def visual_priority(item):
                tokens = set(w for w in words(item['titre']) if len(w) >= 4)
                return sum(w in visible or any(len(v) >= 6 and w.startswith(v) for v in visible) for w in tokens)
            pool = sorted(visual_candidates.values(), key=visual_priority, reverse=True)[:120]
            while len(json.dumps(pool, ensure_ascii=False)) > 85000:
                pool.pop()
            if pool:
                updates['Candidats Discogs'] = json.dumps(pool, ensure_ascii=False)
            updates['Diagnostic'] += '\n' + recognition_stamp(ocr) + '\nRecherche album suivante : ' + (today + timedelta(days=1)).isoformat()
        request_json(endpoint + '/' + row['id'], at, {'fields': updates})
        print(f'{row["id"]}: reconnaissance terminée en {time.monotonic()-started:.1f}s', flush=True)

if __name__ == '__main__':
    main()

