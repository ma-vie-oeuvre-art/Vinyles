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
    text = unicodedata.normalize('NFKD', str(text)).encode('ascii', 'ignore').decode().upper()
    return re.findall(r'[A-Z0-9]+', text)

def supported(ocr, credit, title):
    visible = set(words(ocr))
    title = re.sub(r'\s*\([^)]*\)\s*$', '', title)
    title_words = [w for w in words(title) if len(w) >= 3 and w not in {'THE', 'AND', 'VOL'}]
    artist_words = [w for w in words(credit) if len(w) >= 3 and w not in {'THE', 'AND', 'HIS', 'HER', 'ORCHESTRA', 'ORCHESTRE', 'WITH'}]
    if not title_words or not all(w in visible for w in title_words):
        return False
    if credit.upper() in {'VARIOUS', 'NO ARTIST'}:
        return len(title_words) >= 3
    return bool(artist_words) and sum(w in visible for w in artist_words) / len(artist_words) >= 0.65

def recognition_stamp(ocr):
    return '[recognition-v1:' + hashlib.sha256(ocr.encode()).hexdigest()[:12] + ']'

def should_search(ocr, diagnostic, today):
    if recognition_stamp(ocr) not in diagnostic:
        return True
    retry = re.search(r'Recherche album suivante : (\d{4}-\d{2}-\d{2})', diagnostic)
    return not retry or retry[1] <= today.isoformat()

def main():
    at = {'Authorization': 'Bearer ' + os.environ['AIRTABLE_TOKEN'], 'Content-Type': 'application/json'}
    dg = {'Authorization': 'Discogs token=' + os.environ['DISCOGS_TOKEN'], 'User-Agent': 'VinylesInventaire/1.0 (personal catalog)'}
    endpoint = f'{API}/{BASE}/{TABLE}'
    cursor = None
    rows = []
    while True:
        args = {'pageSize': 100, 'filterByFormula': "AND(OR({Statut}='Dicté',AND({Statut}='À revoir',FIND('[OCR zones v3]',{Transcription source})>0)),OR(NOT({Titre}),NOT({Artiste})))"}
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
    for row in rows:
        f = row['fields']
        if f.get('Ne pas retraiter') or f.get('Discogs release_id'):
            continue
        ocr = f.get('Transcription source', '').partition('[OCR pochette]')[2].strip()
        if not ocr:
            continue
        if not should_search(ocr, f.get('Diagnostic', ''), today):
            continue
        if checked >= 20:
            break
        checked += 1
        started = time.monotonic()
        lines = [s.strip() for s in ocr.replace('\n', ' | ').split(' | ') if 1 <= len(words(s)) <= 10 and sum(len(w) >= 4 for w in words(s)) >= 1]
        lines.sort(key=lambda s: (sum(len(w) >= 4 for w in words(s)) >= 2, sum(len(w) for w in words(s))/len(words(s))), reverse=True)
        queries = list(dict.fromkeys(lines[:6] + [' '.join(lines[i:i+2]) for i in range(min(4, len(lines)-1))]))
        albums = {}
        candidates = {}
        for query in queries[:10]:
            params = {'q': query[:100], 'type': 'release', 'format': 'Vinyl', 'per_page': 30}
            url = 'https://api.discogs.com/database/search?' + urllib.parse.urlencode(params)
            if url not in cache:
                cache[url] = request_json(url, dg)
                time.sleep(2.2)
            result = cache[url]
            for item in result.get('results', []):
                credit, sep, title = str(item.get('title', '')).rpartition(' - ')
                if not sep or not supported(ocr, credit, title):
                    continue
                title = re.sub(r'\s*\([^)]*\)\s*$', '', title)
                key = (tuple(words(credit)), tuple(words(title)))
                albums[key] = (credit, title)
                candidates[item['id']] = {'release_id': item['id'], 'titre': item['title'], 'catno': item.get('catno'), 'label': item.get('label'), 'pays': item.get('country'), 'annee': item.get('year')}
        updates = {'Candidats Discogs': json.dumps(list(candidates.values()), ensure_ascii=False)}
        if len(albums) == 1:
            artist, title = next(iter(albums.values()))
            updates.update({'Artiste': artist, 'Titre': title, 'Diagnostic': 'Album reconnu par GitHub à partir du texte de la photo; pressage précis reporté au besoin.'})
            print(f'{row["id"]}: album reconnu')
        else:
            updates.update({'Statut': 'À revoir', 'Diagnostic': f'Lecture et recherche automatiques effectuées : {len(albums)} album(s) concordant(s). Photo conservée; identification encore incertaine.'})
            print(f'{row["id"]}: identification incertaine')
            updates['Diagnostic'] += '\n' + recognition_stamp(ocr) + '\nRecherche album suivante : ' + (today + timedelta(days=1)).isoformat()
        request_json(endpoint + '/' + row['id'], at, {'fields': updates})
        print(f'{row["id"]}: reconnaissance terminée en {time.monotonic()-started:.1f}s', flush=True)

if __name__ == '__main__':
    main()

