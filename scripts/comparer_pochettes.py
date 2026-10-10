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
from enrichir_vinyles import request_json as _request_json, API, BASE, TABLE
import urllib.error
from reconnaitre_albums import words, album_title, neural_evidence, supported, reset_reference

ROOT = Path(__file__).resolve().parents[1]
SELECTION = Path(os.environ.get('RUNNER_TEMP', '/tmp')) / 'vinyles-visual-selection.json'

def request_json(url,headers,data=None):
    for attempt in range(4):
        try:
            return _request_json(url,headers,data)
        except urllib.error.HTTPError as exc:
            if data is not None or exc.code not in (429,500,502,503,504) or attempt==3:
                raise
            time.sleep(min(30,int(exc.headers.get('Retry-After','0')) or 2**(attempt+1)))
        except (urllib.error.URLError,TimeoutError):
            if data is not None or attempt==3:
                raise
            time.sleep(2**(attempt+1))

def near_word(a, b):
    if a == b:
        return True
    if a.isdigit() or b.isdigit() or abs(len(a)-len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    short, long = sorted((a, b), key=len)
    return any(short == long[:i] + long[i+1:] for i in range(len(long)))

def expanded_words(text):
    text = str(text).replace('@', 'O')
    text = re.sub(r'\b([A-Z])(?=[A-Z][a-z])', r'\1 ', text)
    text = re.sub(r'\bZ[Oo0]{2}1', '2001', text, flags=re.I)
    text = re.sub(r'(?<=\d)(?=[A-Za-z])|(?<=[A-Za-z])(?=\d)', ' ', text)
    result = words(text)
    for token in list(result):
        if token.startswith('AS') and len(token) > 5:
            result.append(token[1:])
        if token.startswith('THE') and len(token) > 7:
            tail = token[3:]
            result.append(tail[:-2] if tail.endswith('OF') else tail)
    return result

def significant_title(title):
    names = {'1':'ONE','I':'ONE','2':'TWO','II':'TWO','3':'THREE','III':'THREE'}
    text = ' '.join(expanded_words(album_title(title)))
    text = re.sub(r'\bVOL(?:UME)?\s+(1|2|3|I|II|III)\b', lambda m: 'VOLUME ' + names[m[1]], text)
    return tuple(w for w in words(text) if (len(w) >= 3 or w.isdigit()) and w not in {'THE','AND','WITH','FROM'})

def main_heading(fields, candidates):
    """Require every printed title word and a dominant main heading."""
    source = fields.get('Transcription source', '')
    match = re.search(r'\[OCR neural regions\] ([^\n]+)', source)
    if not match:
        return None
    regions = [r for r in json.loads(match[1]) if r.get('confidence', 0) >= .35]
    if not regions:
        return None
    maximum = max(r['height'] for r in regions)
    printed = [(w, r['height']) for r in regions for w in expanded_words(r['text'])]
    groups = {}
    generic = {'VOLUME','SONGS','STEREO','MONO','HITS','LATEST','GREATEST','ORIGINAL','RECORDS','SOUNDTRACK','FIDELITY'}
    for candidate in candidates:
        parts = candidate.get('titre','').split(' - ')
        # Both classical credits and a real title may contain this separator.
        for split in range(1,len(parts)):
            credit, title = ' - '.join(parts[:split]), ' - '.join(parts[split:])
            tokens = significant_title(title)
            if len(tokens)<2 or tuple(words(re.sub(r'\s*\(\d+\)\s*$', '', credit))) == tuple(words(album_title(title))):
                continue
            heights = []
            for token in tokens:
                values = [height for w,height in printed if w==token or ((len(token)>=5 or len(token)>=3 and len(tokens)>=6 or token in {'ONE','TWO','THREE'} and 'VOLUME' in tokens) and near_word(w,token))]
                if not values:
                    break
                heights.append(max(values))
            if len(heights)!=len(tokens) or sum(height>=maximum*.35 for height in heights)<2:
                continue
            artist_terms = [(height,w) for w,height in printed if w in words(credit) and len(w)>=5 and not w.isdigit()]
            artist_height,primary_name = max(artist_terms,default=(0,''))
            anonymous_specific = len(tokens)>=4 and any(w.isdigit() and len(w)==4 for w in tokens) and sum(len(w)>=5 and w not in generic for w in tokens)>=2
            if not anonymous_specific and artist_height<maximum*.65:
                continue
            # Small subtitle lettering must not dilute a clear large main title.
            score = sum(sorted(heights,reverse=True)[:2])/2
            if score<maximum*.45:
                continue
            artist_tokens = [w for w in words(credit) if w not in {'THE','AND','WITH','FEAT','FEATURING'}]
            index = artist_tokens.index(primary_name) if primary_name in artist_tokens else 0
            identity = ('ANONYMOUS',) if anonymous_specific else tuple(artist_tokens[max(0,index-1):index+1])
            key = (identity,tokens)
            if key not in groups or score>groups[key][0]:
                groups[key]=(score,candidate)
    # A matching printed subtitle refines the same artist's shorter title.
    remove = set()
    for key in groups:
        for other in groups:
            if key!=other and key[0]==other[0] and set(key[1])<set(other[1]) and groups[other][0]>=groups[key][0]*.95:
                remove.add(key)
    ordered=sorted((value for key,value in groups.items() if key not in remove),key=lambda item:item[0],reverse=True)
    print('Titres principaux admissibles: ' + json.dumps([(round(score,1),candidate.get('titre')) for score,candidate in ordered[:10]],ensure_ascii=False),flush=True)
    if not ordered or (len(ordered)>1 and ordered[0][0]<ordered[1][0]*1.25):
        return None
    return ordered[0][1]

def printed_number_matches(fields,candidate):
    """A repeated series design cannot override its printed volume number."""
    source=fields.get('Transcription source','')
    match=re.search(r'\[OCR neural texte\] ([^\n]+)',source)
    if not match:
        return True
    pattern=r"\b(?:N[°'’O]?|NO|VOL(?:UME)?|NUMBER)\s*(\d{1,2})\b"
    printed=set(re.findall(pattern,match[1],re.I))
    title_numbers=set(re.findall(pattern,candidate.get('titre','').partition(' - ')[2],re.I))
    if len(printed)!=1 or not title_numbers:
        return True
    if not printed & title_numbers:
        return False
    title_words=set(significant_title(candidate.get('titre','').partition(' - ')[2]))
    title_words={w for w in title_words if len(w)>=4 and not w.isdigit() and w not in {'SERIES','SERIE','VOLUME','NUMBER'}}
    visible=set(expanded_words(match[1]))
    return len(title_words)<4 or len(title_words & visible)>=2

def conflicting_reference(fields):
    return any(marker in fields.get('Transcription source','') for marker in ['[Identité visuelle vérifiée]','[Titre principal vérifié]']) and not printed_number_matches(fields,{'titre':fields.get('Artiste','')+' - '+fields.get('Titre','')})

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
    return '[comparaison-visuelle-v11:' + hashlib.sha256(json.dumps(evidence).encode()).hexdigest()[:16] + ']'

def should_compare(fields):
    if fingerprint(fields) not in fields.get('Diagnostic', ''):
        return True
    retry = re.search(r'Recherche visuelle suivante : (\d{4}-\d{2}-\d{2})', fields.get('Diagnostic', ''))
    today = datetime.now(ZoneInfo('America/Toronto')).date().isoformat()
    return not retry or retry[1] <= today

def canonical_candidate(fields):
    if not any(marker in fields.get('Transcription source','') for marker in ['[Identité visuelle vérifiée]','[Titre principal vérifié]','[Titre vérifié automatiquement]']):
        return None
    existing = str(fields.get('Discogs référence release_id',''))
    if existing.isdigit():
        return {'release_id': existing}
    candidates = json.loads(fields.get('Candidats Discogs') or '[]')
    return next((c for c in candidates if str(c.get('release_id','')).isdigit()), None)

def canonical_marker(candidate):
    return '[identite-canonique-v2:' + str(candidate['release_id']) + ']'

def canonical_fields(candidate):
    dg = {'Authorization': 'Discogs token=' + os.environ['DISCOGS_TOKEN'], 'User-Agent':'VinylesInventaire/1.0 (personal catalog)'}
    rid = str(candidate['release_id'])
    release = request_json('https://api.discogs.com/releases/' + rid, dg)
    time.sleep(1.2)
    if str(release.get('id')) != rid or not release.get('title') or not release.get('artists'):
        raise ValueError('Référence structurée invalide')
    artist = ', '.join(a['name'] for a in release['artists'])
    labels = release.get('labels') or []
    canonical_candidate = {'release_id':int(rid),'master_id':release.get('master_id'),'titre':artist+' - '+release['title'],'catno':labels[0].get('catno') if labels else None,'label':[label.get('name') for label in labels],'pays':release.get('country'),'annee':release.get('year'),'reference_illustrative':True}
    return {'Artiste': artist, 'Titre': release['title'], 'Discogs référence release_id':rid, 'Discogs référence album':'https://www.discogs.com/release/'+rid, '_canonical_candidate':canonical_candidate}

def headers():
    return {'Authorization': 'Bearer ' + os.environ['AIRTABLE_TOKEN'], 'Content-Type': 'application/json'}

def select():
    rows = []
    offset = None
    while True:
        args = {'pageSize': 100, 'filterByFormula': '{Photos personnelles}'}
        if offset:
            args['offset'] = offset
        page = request_json(f'{API}/{BASE}/{TABLE}?' + urllib.parse.urlencode(args), headers())
        for row in page.get('records', []):
            f = row['fields']
            if f.get('Ne pas retraiter') or f.get('Discogs release_id') or f.get("Preuve d'identification") == 'Validation humaine':
                continue
            try:
                candidates = json.loads(f.get('Candidats Discogs') or '[]')
                known = bool(f.get('Artiste') and f.get('Titre'))
                canon = canonical_candidate(f) if known else None
                if known and conflicting_reference(f):
                    rows.append(row)
                elif known and canon and canonical_marker(canon) not in f.get('Transcription source',''):
                    rows.append(row)
                elif (not known or not str(f.get('Discogs référence release_id','')).isdigit()) and should_compare(f):
                    rows.append(row)
            except (ValueError, TypeError):
                continue
        offset = page.get('offset')
        if not offset:
            break
    rows.sort(key=lambda row: (bool(row['fields'].get('Artiste') and row['fields'].get('Titre')), not any(marker in row['fields'].get('Transcription source','') for marker in ['[Identité visuelle vérifiée]','[Titre principal vérifié]']), row['fields'].get('Bac', ''), row['fields'].get('Ordre dans le bac', 0)))
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

def compare(photo, reference, text_confirmed=False):
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
    if inliers < (24 if text_confirmed else 14) or inliers / len(good) < (.70 if text_confirmed else .55):
        return 0
    selected = src[mask.ravel().astype(bool)]
    coverage = cv2.contourArea(cv2.convexHull(selected)) / (reference[0][0] * reference[0][1])
    if coverage < (.08 if text_confirmed else .20):
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
        if f.get("Preuve d'identification") == 'Validation humaine' or f.get('Ne pas retraiter') or f.get('Discogs release_id'):
            continue
        if f.get('Artiste') and f.get('Titre') and conflicting_reference(f):
            updates=reset_reference(f)
            source=re.sub(r'\n\[(?:Identité visuelle vérifiée|Titre principal vérifié|identite-canonique-v\d+:[^\]]+)\]','',f.get('Transcription source',''))
            updates.update({'Artiste':None,'Titre':None,'Candidats Discogs':'[]','Transcription source':source,'Diagnostic':'Référence automatique retirée : le texte du volume imprimé contredit le titre proposé. Nouvelle recherche automatique en cours.'})
            request_json(f'{API}/{BASE}/{TABLE}/'+row['id'],headers(),{'fields':updates})
            f={key:value for key,value in {**f,**updates}.items() if value is not None}
        if f.get('Artiste') and f.get('Titre') and canonical_candidate(f):
            candidate = canonical_candidate(f)
            if candidate and canonical_marker(candidate) not in f.get('Transcription source',''):
                updates = canonical_fields(candidate)
                item = updates.pop('_canonical_candidate')
                prior = json.loads(f.get('Candidats Discogs') or '[]')
                updates['Candidats Discogs'] = json.dumps([item] + [entry for entry in prior if str(entry.get('release_id')) != str(item['release_id'])],ensure_ascii=False)
                updates['Transcription source'] = f.get('Transcription source','') + '\n' + canonical_marker(candidate)
                request_json(f'{API}/{BASE}/{TABLE}/' + row['id'], headers(), {'fields': updates})
                print(f"{row['id']}: artiste et titre vérifiés dans les champs structurés", flush=True)
            continue
        candidates = json.loads(f.get('Candidats Discogs') or '[]')
        stamp = fingerprint(f)
        if not should_compare(f):
            continue
        match = re.search(r'fichier source: ([^\n]+)', f.get('Transcription source', ''))
        path = (ROOT / match[1]).resolve() if match else None
        if path and ROOT in path.parents and path.is_file():
            photo = features(path.read_bytes())
        else:
            attachments = f.get('Photos personnelles') or []
            if not attachments:
                errors += 1
                print(f"{row['id']}: photo source indisponible; reprise automatique nécessaire", flush=True)
                continue
            with urllib.request.urlopen(attachments[0]['url'],timeout=30) as response:
                photo_data=response.read(20_000_001)
            if len(photo_data)>20_000_000:
                raise ValueError('Photo trop volumineuse')
            photo=features(photo_data)
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
                credit, _, title = c['titre'].rpartition(' - ')
                score = compare(photo, reference, supported(f.get('Transcription source', ''), credit, title))
                if score:
                    scores.append((score, c))
        master_candidates = []
        if not scores:
            # A release search can be monopolized by variants of popular albums.
            # Search masters as well: one entry per album exposes rarer artists.
            source = f.get('Transcription source', '')
            queries, _ = neural_evidence(source)
            text = source.partition('[OCR neural texte]')[2].split('[OCR neural regions]')[0]
            variants = text.replace('@', 'O')
            variants = re.sub(r'\bZ[Oo0]{2}1', '2001', variants, flags=re.I)
            variants = re.sub(r'(?<=\d)(?=[A-Za-z])|(?<=[A-Za-z])(?=\d)', ' ', variants)
            individual = [w for w in words(variants) if len(w) >= 4 and w not in {'STEREO','MONO','RECORDS','VOLUME','POLYDOR','CORAL','DECCA'}]
            region_match = re.search(r'\[OCR neural regions\] ([^\n]+)', source)
            heading_query = ''
            if region_match:
                primary_regions = sorted(json.loads(region_match[1]), key=lambda r: r['height'], reverse=True)[:4]
                primary_text = ' '.join(r['text'] for r in primary_regions)
                primary_text = re.sub(r'\b([A-Z])(?=[A-Z][a-z])', r'\1 ', primary_text)
                primary_tokens = [w for w in words(primary_text.replace('@','O')) if len(w)>=3 and w not in {'THE','AND','WITH','STEREO','MONO'}]
                heading_query = ' '.join(dict.fromkeys(primary_tokens))
            stored_title = str(f.get('Titre') or '').replace('—',' - ')
            title_queries = [stored_title] + [part.strip() for part in stored_title.split(' - ') if len(part.strip())>=4] if stored_title else []
            queries = list(dict.fromkeys(([heading_query] if heading_query else []) + queries[:4] + title_queries + individual[:8]))[:10]
            print('Requêtes de titres: ' + json.dumps(queries,ensure_ascii=False),flush=True)
            dg = {'Authorization': 'Discogs token=' + os.environ['DISCOGS_TOKEN'], 'User-Agent': 'VinylesInventaire/1.0 (personal catalog)'}
            masters = {}
            for query in queries:
                result = request_json('https://api.discogs.com/database/search?' + urllib.parse.urlencode({'q': query[:100], 'type': 'master', 'per_page': 100}), dg)
                time.sleep(1.2)
                for item in result.get('results', []):
                    if item.get('cover_image') and ' - ' in item.get('title', ''):
                        masters[item['id']] = {'master_id': item['id'], 'master_candidate': True, 'titre': item['title'], 'cover_url': item['cover_image']}
            visible = set(words(source + ' ' + variants))
            def priority(item):
                return sum(w in visible or any(len(v) >= 6 and w.startswith(v) for v in visible) for w in set(words(item['titre'])) if len(w) >= 4)
            # OCR can fuse a film/year with its words and misread a short volume.
            # Search literal numeric variants; only the printed heading can validate.
            years = [w for w in expanded_words(variants) if w.isdigit() and len(w)==4 and 1900 <= int(w) <= 2100]
            volume_words = expanded_words(variants)
            if years and any(near_word(w, 'VOLUME') for w in volume_words):
                for number, name in [('1','ONE'),('2','TWO'),('3','THREE')]:
                    if not any(near_word(w, name) for w in volume_words):
                        continue
                    for hint in [years[0]+' VOLUME '+name, years[0]+' '+number]:
                        result = request_json('https://api.discogs.com/database/search?' + urllib.parse.urlencode({'q':hint,'type':'release','format':'Vinyl','per_page':100}), dg)
                        time.sleep(1.2)
                        for item in result.get('results', []):
                            if item.get('cover_image') and ' - ' in item.get('title', ''):
                                masters['release:'+str(item['id'])] = {'release_id':item['id'],'master_id':item.get('master_id'),'titre':item['title'],'cover_url':item['cover_image']}
            master_candidates = sorted(masters.values(), key=priority, reverse=True)[:100]
            master_candidates = [c for c in master_candidates if c['cover_url'] not in urls]
            print('Albums prioritaires: ' + json.dumps([c['titre'] for c in master_candidates[:12]],ensure_ascii=False),flush=True)
            with ThreadPoolExecutor(max_workers=4) as pool:
                for c, reference, failure in pool.map(load, master_candidates):
                    if failure:
                        unavailable += 1
                        continue
                    credit, _, title = c['titre'].rpartition(' - ')
                    score = compare(photo, reference, supported(f.get('Transcription source', ''), credit, title))
                    if score:
                        scores.append((score, c))
            print(f"{row['id']}: {len(chosen)} versions et {len(master_candidates)} albums comparés", flush=True)
        scores = [(score,candidate) for score,candidate in scores if printed_number_matches(f,candidate)]
        if not scores:
            heading = main_heading(f, candidates + master_candidates)
            if heading:
                scores = [(0, heading)]
        print('Correspondances de pochettes: ' + json.dumps([(score,candidate.get('titre')) for score,candidate in scores],ensure_ascii=False),flush=True)
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
            canonical = canonical_fields(matches[0])
            item = canonical.pop('_canonical_candidate')
            matches = [item] + [entry for entry in matches if str(entry.get('release_id')) != str(item['release_id'])]
            updates.update(canonical)
            artist, title = canonical['Artiste'], canonical['Titre']
            updates.update({'Transcription source': f.get('Transcription source', '') + ('\n[Identité visuelle vérifiée]' if scores[0][0] else '\n[Titre principal vérifié]') + '\n' + canonical_marker(matches[0]), 'Artiste': artist, 'Titre': album_title(title), 'Candidats Discogs': json.dumps(matches, ensure_ascii=False),
                'Diagnostic': (f'Album reconnu automatiquement par correspondances géométriques des pochettes ({scores[0][0]} points concordants); pressage précis non attribué.' if scores[0][0] else 'Album reconnu automatiquement par le titre principal imprimé et son artiste ou son volume; référence Discogs concordante, pressage précis non attribué.')})
            print(f"{row['id']}: album reconnu " + (f"par image ({scores[0][0]} points)" if scores[0][0] else 'par titre principal imprimé'), flush=True)
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
