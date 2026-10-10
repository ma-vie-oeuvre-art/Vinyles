#!/usr/bin/env python3
"""Enrichissement automatique : album et pressage restent deux sources distinctes."""
import json
import os
import re
import time
import unicodedata
import hashlib
import urllib.error
from datetime import datetime
from zoneinfo import ZoneInfo
import urllib.parse
from collections import Counter
from enrichir_vinyles import request_json, BASE, TABLE, API
from recuperer_pochettes_confirmees import album_reference, candidate_reference

VERSION = 'metadata-v1'
REF = 'Discogs référence album'
RID = 'Discogs référence release_id'
REPORT = 'Enrichissement automatique'

def normalize_title(title):
    title = re.sub(r'(?:\s*[\[(][^\])]*[\])])+\s*$', '', str(title))
    title = ''.join(c for c in unicodedata.normalize('NFD', title.lower()) if unicodedata.category(c) != 'Mn')
    title = re.sub(r"^\s*l['’]", '', title)
    title = re.sub(r'[^\w\s]', '', title, flags=re.UNICODE)
    title = re.sub(r'^\s*(?:le|la|les|l|un|une|des|du|the|a|an)\s+', '', title)
    return '-'.join(title.split())

def flatten_tracks(items, work=''):
    result = []
    for t in items:
        title = t.get('title', '')
        if t.get('type_') == 'heading':
            work = title
            continue
        if t.get('sub_tracks'):
            result.extend(flatten_tracks(t['sub_tracks'], title or work))
            continue
        if not title:
            continue
        item = {'p': t.get('position', ''), 't': title}
        if t.get('duration'): item['d'] = t['duration']
        if work: item['o'] = work
        if t.get('artists'): item['a'] = ', '.join(a.get('name', '') for a in t['artists'])
        result.append(item)
    return result

def classification(release):
    genres = set(release.get('genres', []))
    styles = set(release.get('styles', []))
    tags = []
    for genre, family in [('Classical','Classique'),('Jazz','Jazz'),('Rock','Pop-rock'),('Pop','Pop-rock'),('Electronic','Électro / Dance'),('Funk / Soul','Funk / Soul'),('Folk, World, & Country','Traditionnel / Monde'),('Blues','Blues'),('Latin','Latin'),('Reggae','Reggae'),('Hip Hop','Hip-hop')]:
        if genre in genres and family not in tags: tags.append(family)
    if styles & {'Soundtrack','Score','Musical'}: tags.insert(0, 'Bandes sonores')
    if styles & {'Christmas'}: tags.insert(0, 'Noël')
    if 'Chanson' in styles: tags.append('Chanson')
    if 'Country' in styles: tags.append('Country')
    if 'Non-Music' in genres and styles & {'Comedy','Spoken Word'}: tags.append('Parlé et humour')
    descriptions = {d for fmt in release.get('formats', []) for d in fmt.get('descriptions', [])}
    if 'Compilation' in descriptions: tags.append('Compilation')
    if 'Live' in styles: tags.append('Enregistrement public')
    allowed = {'Classique','Noël','Bandes sonores','Francophone','Jazz','Pop-rock','Électro / Dance','Funk / Soul','Traditionnel / Monde'}
    return next((x for x in tags if x in allowed), 'Général'), list(dict.fromkeys(tags)) or ['Général']

def physical_format(release):
    formats = release.get('formats', [])
    desc = {d for fmt in formats for d in fmt.get('descriptions', [])}
    if 'Box Set' in {f.get('name') for f in formats}: return 'Coffret'
    if '78 RPM' in desc: return '78 tours'
    if '10"' in desc: return '10 pouces'
    vinyl = [f for f in formats if f.get('name') == 'Vinyl']
    if any(str(f.get('qty')) == '2' for f in vinyl): return 'Double album'
    if '45 RPM' in desc: return 'Maxi 12 pouces' if '12"' in desc else '45 tours'
    if 'LP' in desc or '33 ⅓ RPM' in desc: return '33 tours'
    return None

def derived(row, totals):
    f = row['fields']; out = {}
    bac, rank = f.get('Bac'), f.get('Ordre dans le bac')
    if bac and isinstance(rank, (int,float)) and rank > 0 and int(rank) == rank:
        out.update({'Position': f'{int(rank)} / {totals[bac]}', 'Total du bac': totals[bac]})
        previous_bac, previous_rank = f.get('Bac précédent'), f.get('Rang précédent')
        if (previous_bac, previous_rank) != (bac, rank):
            stamp = datetime.now(ZoneInfo('America/Toronto')).date().isoformat()
            event = f'{stamp} — emplacement observé : {bac}, rang {int(rank)} / {totals[bac]}' if not previous_bac else f'{stamp} — déplacement observé : {previous_bac}, rang {previous_rank} → {bac}, rang {int(rank)} / {totals[bac]}'
            out.update({'Bac précédent': bac, 'Rang précédent': int(rank), 'Historique de rangement': '\n'.join(x for x in [f.get('Historique de rangement'), event] if x)})
    if not f.get('Date de saisie') and row.get('createdTime'):
        out['Date de saisie'] = row['createdTime'][:10]
    if not f.get('Indice CDU principal'):
        out['Indice CDU principal'] = '681.854*' + row['id']
    try:
        tracks = json.loads(f.get('Tracklist JSON') or '[]')
        if not isinstance(tracks,list): raise ValueError('Tracklist JSON doit être un tableau')
        out['Nombre de plages'] = len(tracks) if f.get('Tracklist JSON') else None
        out['Titres — index normalisé'] = '\n'.join(str(t.get('p','')) + '\t' + normalize_title(t.get('t',t.get('title',''))) for t in tracks if isinstance(t,dict)) if f.get('Tracklist JSON') else None
    except (ValueError,TypeError) as exc:
        out[REPORT] = f'{VERSION} : liste de pistes invalide; correction de la source nécessaire ({type(exc).__name__}).'
    images = f.get('Images Discogs — URLs sources')
    if images:
        out['Images Discogs — statut'] = 'Recto + verso' if f.get('Front Cover') and f.get('Back Cover') else 'Recto seul' if f.get('Front Cover') else 'Échec de téléchargement'
    elif f.get('Photos personnelles'):
        out['Images Discogs — statut'] = 'Photos personnelles'
    return out

def resolve(f, headers, cache):
    rid = str(f.get(RID) or '').strip()
    physical = str(f.get('Discogs release_id') or '').strip()
    if not rid.isdecimal() and physical.isdecimal(): rid = physical
    if not rid.isdecimal():
        sources = f.get('Images Discogs — URLs sources','')
        match = re.search(r'(?:version illustrée r|images de la version |discogs\.com/release/)(\d+)', sources + ' ' + f.get('Diagnostic',''))
        if match: rid = match[1]
    if rid.isdecimal():
        if rid not in cache: cache[rid] = request_json('https://api.discogs.com/releases/' + rid, headers); time.sleep(1.2)
        release = cache[rid]
        if str(release.get('id')) != rid: raise ValueError('Identifiant Discogs incohérent')
        return release
    ref = album_reference(f, headers) or candidate_reference(f, headers, require_images=False)
    if ref: return ref[1]
    return None

def metadata(f, release, headers, cache):
    rid = str(release['id']); confirmed = f.get('Statut') == 'Identifié' and str(f.get('Discogs release_id','')).strip() == rid
    out = {RID:rid, REF:'https://www.discogs.com/release/' + rid}
    if not f.get('Tracklist JSON') and release.get('tracklist'):
        tracks = flatten_tracks(release['tracklist'])
        out['Tracklist JSON'] = json.dumps(tracks,ensure_ascii=False,separators=(',',':'))
    family,tags = classification(release)
    if not f.get('Famille'): out['Famille'] = family
    if not f.get('Familles'): out['Familles'] = tags
    master_id = release.get('master_id')
    if master_id and not f.get("Année de l'album"):
        key = 'master:' + str(master_id)
        if key not in cache: cache[key] = request_json(f'https://api.discogs.com/masters/{master_id}',headers); time.sleep(1.2)
        master = cache[key]
        if master.get('year'): out["Année de l'album"] = int(master['year'])
        out[REF] = f'https://www.discogs.com/master/{master_id}'
    if confirmed:
        if release.get('year') and not f.get('Année'): out['Année'] = release['year']
        if release.get('country') and not f.get('Pays du pressage'): out['Pays du pressage'] = release['country']
        fmt = physical_format(release)
        if fmt and not f.get('Format physique'): out['Format physique'] = fmt
        year = f.get('Année') or release.get('year')
        if year and 1940 <= int(year) < 2030 and not f.get('Décennie du pressage'): out['Décennie du pressage'] = str(int(year)//10*10)
        community = release.get('community', {})
        for field, value in [('Possédé par (Discogs)',community.get('have')),('Recherché par (Discogs)',community.get('want')),('Note Discogs (sur 5)',community.get('rating',{}).get('average')),('Nombre de notes (Discogs)',community.get('rating',{}).get('count')),('Exemplaires en vente (Discogs)',release.get('num_for_sale'))]:
            if f.get(field) is None and value is not None: out[field] = value
    out[REPORT] = f'{VERSION} : album renseigné depuis r{rid}. ' + ('Pressage identifié.' if confirmed else 'Référence d’album uniquement; pressage et prix spécifiques à confirmer plus tard.') + ' Les images secondaires Discogs ne garantissent pas un verso; les informations personnelles et la réputation sonore exigent leurs propres sources.'
    return out

def main():
    at = {'Authorization':'Bearer ' + os.environ['AIRTABLE_TOKEN'],'Content-Type':'application/json'}
    dg = {'User-Agent':'VinylesInventaire/1.0 (personal catalog)','Authorization':'Discogs token=' + os.environ['DISCOGS_TOKEN']}
    endpoint = f'{API}/{BASE}/{TABLE}'; rows=[]; cursor=None
    while True:
        params={'pageSize':100}
        if cursor: params['offset']=cursor
        page=request_json(endpoint+'?'+urllib.parse.urlencode(params),at)
        rows.extend(page.get('records',[]));cursor=page.get('offset')
        if not cursor:break
    totals=Counter(r['fields'].get('Bac') for r in rows); cache={}; errors=0; changed=0; fetched=0
    rows.sort(key=lambda r:r['fields'].get('Bac') != 'B-08')
    for row in rows:
        f=row['fields']
        if f.get('Ne pas retraiter'):continue
        updates=derived(row,totals)
        try:
            signature = VERSION + ':' + hashlib.sha256(json.dumps([f.get('Artiste'),f.get('Titre'),f.get('N° catalogue'),f.get('Discogs release_id'),f.get('Statut'),f.get('Candidats Discogs')],ensure_ascii=False).encode()).hexdigest()[:12]
            needed = not f.get(REPORT,'').startswith(signature) and (f.get('Discogs release_id') or (f.get('Titre') and f.get('Artiste')))
            if needed and fetched < 300:
                release=resolve(f,dg,cache); fetched+=1
                if release:
                    updates.update(metadata(f,release,dg,cache))
                    updates[REPORT] = updates[REPORT].replace(VERSION,signature,1)
                else: updates[REPORT]=f'{signature} : album sans correspondance certaine; photo conservée, aucune métadonnée inventée.'
            merged={**f,**updates}; updates.update(derived({**row,'fields':merged},totals))
            updates={k:v for k,v in updates.items() if f.get(k)!=v}
            if updates:
                if os.getenv('DRY_RUN','0')!='1':request_json(endpoint+'/'+row['id'],at,{'fields':updates})
                changed+=1; print(f"{row['id']} : {', '.join(updates)}")
        except Exception as exc:
            errors+=1;print(f"{row['id']} : échec technique {type(exc).__name__}: {exc}")
    print(f'Bilan : {len(rows)} fiches examinées, {changed} corrigées, {errors} erreurs.')
    if errors:raise SystemExit(1)

if __name__=='__main__':main()

