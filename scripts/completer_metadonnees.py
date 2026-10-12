#!/usr/bin/env python3
"""Enrichissement automatique : album et pressage restent deux sources distinctes."""
import json
import os
import re
import time
import unicodedata
import hashlib
from difflib import SequenceMatcher
import urllib.error
from datetime import datetime
from zoneinfo import ZoneInfo
from reprises_automatiques import retry_due, next_retry
import urllib.parse
from collections import Counter
from enrichir_vinyles import request_json as _request_json, BASE, TABLE, API
from recuperer_pochettes_confirmees import album_reference, candidate_reference

VERSION = 'metadata-v3'
REF = 'Discogs référence album'
RID = 'Discogs référence release_id'
REPORT = 'Enrichissement automatique'
_last_discogs_call = 0.0

def request_json(url, headers, data=None):
    global _last_discogs_call
    for attempt in range(4):
        if url.startswith('https://api.discogs.com/'):
            time.sleep(max(0, 1.2 - (time.monotonic() - _last_discogs_call)))
            _last_discogs_call = time.monotonic()
        try:
            return _request_json(url, headers, data)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429,500,502,503,504) or attempt == 3: raise
            time.sleep(min(30, int(exc.headers.get('Retry-After', '0')) or 2 ** (attempt+1)))
        except (urllib.error.URLError, TimeoutError):
            if attempt == 3: raise
            time.sleep(2 ** (attempt+1))

def cad_price(price, cache):
    if not isinstance(price,dict) or price.get('value') is None: return None, ''
    currency=price.get('currency'); value=float(price['value'])
    if not currency: raise ValueError('Devise Discogs absente')
    if currency=='CAD': return round(value,2), 'CAD'
    series='FX'+currency+'CAD'
    if series not in cache:
        data=request_json(f'https://www.bankofcanada.ca/valet/observations/{series}/json?recent=1',{})
        obs=data.get('observations') or []
        if not obs or not obs[-1].get(series,{}).get('v'): raise ValueError('Taux de change absent')
        cache[series]=(float(obs[-1][series]['v']),obs[-1]['d'])
    rate,date=cache[series]
    return round(value*rate,2), f'{currency} → CAD : {rate}, Banque du Canada, {date}'

def market_metadata(f, release, headers, cache, confirmed):
    rid=str(release['id']); out={}; details=[]; errors=[]
    try:
        data=request_json(f'https://api.discogs.com/marketplace/stats/{rid}?curr_abbr=CAD',headers)
        if data.get('num_for_sale') is not None: out['Exemplaires en vente (Discogs)']=data['num_for_sale']
        price,conversion=cad_price(data.get('lowest_price'),cache)
        if price is not None: out['Valeur minimale']=price; details.append('prix affiché le plus bas : '+conversion)
        elif data.get('num_for_sale')==0: details.append('aucune annonce en vente; prix minimal non fourni')
        else: details.append('prix minimal non fourni par Discogs')
    except urllib.error.HTTPError as exc:
        if exc.code not in (403,404): raise
        errors.append(f'marché : HTTP {exc.code}')
    try:
        prices=request_json(f'https://api.discogs.com/marketplace/price_suggestions/{rid}',headers)
        for field,key in [('Prix suggéré VG+ (Discogs)','Very Good Plus (VG+)'),('Prix suggéré Mint (Discogs)','Mint (M)')]:
            value,conversion=cad_price(prices.get(key),cache)
            if value is not None: out[field]=value; details.append(key+' : '+conversion)
            else: details.append(key+' : suggestion non fournie')
    except urllib.error.HTTPError as exc:
        if exc.code not in (403,404): raise
        errors.append(f'suggestions : HTTP {exc.code}, accès vendeur non disponible pour cette version')
    out["Source de l'estimation"]='\n'.join([f'https://www.discogs.com/release/{rid}', 'Pressage identifié.' if confirmed else 'INDICATIF : version de référence; le pressage possédé n’est pas confirmé.', 'Montants en CAD. Suggestions de mise en vente, pas des ventes observées.', *details, *errors])
    if any(k in out for k in ['Valeur minimale','Prix suggéré VG+ (Discogs)','Prix suggéré Mint (Discogs)']): out["Date de l'estimation"]=datetime.now(ZoneInfo('America/Toronto')).date().isoformat()
    return out

def recording_metadata(release, confirmed):
    url=f"https://www.discogs.com/release/{release['id']}"
    scope='Pressage identifié.' if confirmed else 'Version de référence, applicabilité au pressage possédé non confirmée.'
    notes=str(release.get('notes') or '').strip()
    descriptions=list(dict.fromkeys(d for f in release.get('formats',[]) for d in f.get('descriptions',[]) if d in {'Mono','Stereo','Quadraphonic','Remastered','Reissue'}))
    credits=[f"{a.get('role')} : {a.get('name')}" for a in release.get('extraartists',[]) if re.search(r'engineer|mastered|recorded|mixed|lacquer|producer',a.get('role',''),re.I)]
    passages=[p.strip() for p in re.split(r'\n\s*\n|\n',notes) if re.search(r'record|master|stereo|mono|quad|engineer|studio|live|digital|analog|remaster|78\s*(?:rpm|tour)',p,re.I)]
    out={}
    technical=[*descriptions,*credits,*passages]
    if technical: out["Qualité d'enregistrement (source)"]='\n'.join([url,scope,'Données documentées dans la fiche Discogs; aucune note audiophile déduite.',*technical])[:90000]
    historical=[p.strip() for p in re.split(r'\n\s*\n|\n',notes) if p.strip()]
    if historical: out['Informations historiques']='\n'.join([url,scope,'Notes de la version Discogs :',*historical])[:90000]
    return out

def physical_hints(f, release):
    """Lire les identifiants sur l'OCR personnel, jamais les copier d'une référence."""
    out={}
    raw=f.get('Transcription source','')
    ocr=raw.split('[OCR pochette]',1)[1] if '[OCR pochette]' in raw else ''
    def visible(value):
        chars=re.sub(r'[^A-Z0-9]','',unicodedata.normalize('NFD',str(value).upper()))
        if len(chars)<4: return False
        pattern=r'\b'+r'[\W_]*'.join(re.escape(c) for c in chars)+r'\b'
        text=''.join(c for c in unicodedata.normalize('NFD',ocr.upper()) if unicodedata.category(c)!='Mn')
        return bool(re.search(pattern,text))
    cats={x.get('catno') for x in release.get('labels',[]) if x.get('catno') and re.search(r'\d',x['catno']) and visible(x['catno'])}
    def label_visible(name):
        trademark=re.sub(r'\b(?:records?|inc|ltd|limited)\b\.?','',name,flags=re.I).strip()
        return visible(name) or (len(re.sub(r'\W','',trademark))>=4 and visible(trademark))
    labels={x.get('name') for x in release.get('labels',[]) if x.get('name') and label_visible(x['name'])}
    if not f.get('N° catalogue') and len(cats)==1: out['N° catalogue']=next(iter(cats))
    if not f.get('Label') and len(labels)==1: out['Label']=next(iter(labels))
    cat=f.get('N° catalogue') or out.get('N° catalogue')
    from qualifier_vinyles import norm
    matched=cat and any(norm(x.get('catno'))==norm(cat) for x in release.get('labels',[]))
    fmt=physical_format(release)
    if matched and fmt and not f.get('Format physique'): out['Format physique']=fmt
    if cat and not f.get("Preuve d'identification"):
        out["Preuve d'identification"]='Catalogue + label' if f.get('Label') or out.get('Label') else 'Catalogue seul'
    return out

def normalize_title(title):
    title = re.sub(r'(?:\s*[\[(][^\])]*[\])])+\s*$', '', str(title))
    title = ''.join(c for c in unicodedata.normalize('NFD', title.lower()) if unicodedata.category(c) != 'Mn')
    title = re.sub(r"^\s*l['’]", '', title)
    title = re.sub(r'[^\w\s]', '', title, flags=re.UNICODE)
    title = re.sub(r'^\s*(?:le|la|les|l|un|une|des|du|the|a|an)\s+', '', title)
    return '-'.join(title.split())

def validated_reference(f):
    if f.get("Preuve d'identification") != 'Validation humaine': return None
    references=set(re.findall(r'(?:release\s+|discogs\.com/release/)(\d+)',f.get('Informations historiques',''),re.I))
    return next(iter(references)) if len(references)==1 else None

def restore_documented_fields(f):
    """Réparer les champs effacés contredisant une transcription déjà validée."""
    out={}
    if f.get("Preuve d'identification") != 'Validation humaine': return out
    history=f.get('Informations historiques','')
    status=re.search(r'^Statut réel\s*:\s*(Identifié|Pressage à confirmer|À revoir|Non trouvé|Dicté)\.',history,re.M)
    if status: out['Statut']=status[1]
    if re.search(r'\bCause réelle\s*:\s*aucun résultat Discogs',history,re.I):
        out.update({'Cause':'Aucun résultat Discogs','Candidats Discogs':'[]'})
    if 'numéro de fascicule, pas un numéro de catalogue' in history:
        out.update({'N° catalogue':None,'Cause':'Numéro de fascicule, pas de catalogue','Candidats Discogs':'[]'})
    if 'réputation sonore laissée vide' in f.get('Source de la réputation','').lower(): out['Réputation sonore']=None
    source=f.get('Transcription source','')
    identity=re.search(r'Ce disque\s*:\s*([^,\n]+),\s*(.*?)\s*—\s*(.+)',history,re.I)
    if identity:
        if not f.get('Titre'): out['Titre']=identity[2].strip()
        if not f.get('Artiste'): out['Artiste']=identity[1].strip()+' / '+identity[3].strip().rstrip('.')
    cover=re.search(r'\[photo pochette\]\s*(.*?)(?=\[photo|$)',source,re.S|re.I)
    if cover and (not f.get('Titre') or not f.get('Artiste')):
        parts=[p.strip().rstrip('.') for p in cover[1].split(' — ')]
        matches=[i for i,p in enumerate(parts) if re.search(r'\b(?:symphonie|symphony|concerto|sonata|sonate|quatuor|requiem)\b',p,re.I)]
        if len(matches)==1 and 0<matches[0]<len(parts)-1:
            i=matches[0]
            if not f.get('Titre'): out['Titre']=parts[i]
            if not f.get('Artiste'): out['Artiste']=' / '.join([parts[i-1],parts[i+1]])
            out['Statut']='Pressage à confirmer'
    central=re.search(r'\[photo étiquette centrale\]\s*(.*)',source,re.S|re.I)
    if central and not f.get("Qualité d'enregistrement (source)"):
        out["Qualité d'enregistrement (source)"]='Source : transcription validée de l’étiquette de cet exemplaire.\n'+central[1]
    if central:
        country=re.search(r'\bMade in (Italy|Canada|France|Germany|England|USA|US)\b',central[1],re.I)
        names={'italy':'Italy','canada':'Canada','france':'France','germany':'Germany','england':'UK','usa':'US','us':'US'}
        if country and not f.get('Pays du pressage'): out['Pays du pressage']=names[country[1].lower()]
    if 'Cause' in out:
        out['Diagnostic']='Conclusion validée restaurée : '+str(out.get('Statut') or f.get('Statut') or '')+'. '+out['Cause']+'. Le pressage précis reste distinct de l’enrichissement de l’album.'
    return out

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

WORLD_STYLES = {'Folk', 'Celtic', 'Traditional', 'Polka', 'Flamenco', 'Fado', 'Tango', 'Calypso', 'Soca', 'Greek',
                'Balkan', 'Klezmer', 'African', 'Highlife', 'Bossanova', 'Bolero', 'Folklore', 'Afro-Cuban', 'Cumbia',
                'Rumba', 'Ranchera', 'Mariachi', 'Pacific', 'Hawaiian', 'Steel Band', 'Musette', 'Indian Classical',
                'Field Recording', 'Nordic', 'Volksmusik', 'Andean Music', 'Gypsy Jazz', 'Mento', 'Bhangra', 'Celtic Folk'}
COUNTRY_STYLES = {'Country', 'Country Rock', 'Bluegrass', 'Honky Tonk', 'Nashville Sound', 'Countrypolitan', 'Cajun'}

def classification(release):
    """Famille principale + familles secondaires.
    Règles : la chanson (style Chanson) est Francophone; la country n'est ni Traditionnel / Monde
    ni Jazz (elle reste Général, avec l'étiquette Country); un genre Jazz accolé à Pop/Rock sur une
    fiche country est une erreur Discogs et ne décide pas de la famille."""
    genres = set(release.get('genres', []))
    styles = set(release.get('styles', []))
    chanson = 'Chanson' in styles
    world = bool(styles & WORLD_STYLES)
    # Country : un style country, ou le mot « country » dans le titre (Discogs laisse souvent les styles vides).
    country = bool(styles & COUNTRY_STYLES) or 'country' in str(release.get('title', '')).lower()
    # Sans aucun style, « Folk, World, & Country » reste folklorique sauf indice country.
    world = world or (not styles and not country)
    tags = []
    if styles & {'Christmas'}: tags.append('Noël')
    if styles & {'Soundtrack', 'Score', 'Musical'}: tags.append('Bandes sonores')
    if chanson: tags.append('Francophone')
    for genre, family in [('Classical','Classique'),('Jazz','Jazz'),('Rock','Pop-rock'),('Pop','Pop-rock'),('Electronic','Électro / Dance'),('Funk / Soul','Funk / Soul'),('Folk, World, & Country','Traditionnel / Monde'),('Blues','Blues'),('Latin','Latin'),('Reggae','Reggae'),('Hip Hop','Hip-hop')]:
        if genre not in genres or family in tags: continue
        if country and family in ('Jazz', 'Traditionnel / Monde'): continue
        if family == 'Traditionnel / Monde' and not world: continue
        tags.append(family)
    if chanson: tags.append('Chanson')
    if country: tags.append('Country')
    if 'Non-Music' in genres and styles & {'Comedy','Spoken Word'}: tags.append('Parlé et humour')
    descriptions = {d for fmt in release.get('formats', []) for d in fmt.get('descriptions', [])}
    if 'Compilation' in descriptions: tags.append('Compilation')
    if 'Live' in styles: tags.append('Enregistrement public')
    allowed = {'Classique','Noël','Bandes sonores','Francophone','Jazz','Pop-rock','Électro / Dance','Funk / Soul','Traditionnel / Monde'}
    rock = bool(genres & {'Rock', 'Pop'}) and not (styles & COUNTRY_STYLES - {'Country Rock'})
    if country and not chanson and not rock:
        primary = 'Général'
    elif country and rock:
        primary = 'Pop-rock'
    else:
        primary = next((x for x in tags if x in allowed), 'Général')
    return primary, list(dict.fromkeys(tags)) or ['Général']

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

def clean_name(name):
    """Forme lisible d'un nom Discogs : « Shadows, The » → « The Shadows », « Re-Flex (2) » → « Re-Flex »."""
    if not name: return name
    name = re.sub(r'\s\(\d+\)', '', str(name)).replace('*', '')
    name = re.sub(r'(^|, )([^,]+?), The(?=,|$)', r'\1The \2', name)
    return re.sub(r'\s{2,}', ' ', name).strip()

def bac_number(code):
    try: return int(str(code).split('-')[-1])
    except ValueError: return 0

def cle(f):
    """Convention de la base (bacs B-01 à B-07) : « Label Catno ». Sans catno lu : « Label Titre »."""
    label, catno, title = (str(f.get(k) or '').strip() for k in ('Label', 'N° catalogue', 'Titre'))
    parts = [label, catno] if catno else [label, title]
    key = ' '.join(x for x in parts if x)
    if not key and f.get('Bac') and isinstance(f.get('Ordre dans le bac'), (int, float)):
        key = f"{f['Bac']}-{int(f['Ordre dans le bac']):03}"
    return key

def derived(row, totals):
    f = row['fields']; out = restore_documented_fields(f)
    bac, rank = f.get('Bac'), f.get('Ordre dans le bac')
    if bac and isinstance(rank, (int,float)) and rank > 0 and int(rank) == rank:
        out.update({'Position': f'{int(rank)} / {totals[bac]}', 'Total du bac': totals[bac]})
        previous_bac, previous_rank = f.get('Bac précédent'), f.get('Rang précédent')
        if (previous_bac, previous_rank) != (bac, rank):
            stamp = datetime.now(ZoneInfo('America/Toronto')).date().isoformat()
            event = f'{stamp} — emplacement observé : {bac}, rang {int(rank)} / {totals[bac]}' if not previous_bac else f'{stamp} — déplacement observé : {previous_bac}, rang {previous_rank} → {bac}, rang {int(rank)} / {totals[bac]}'
            out.update({'Bac précédent': bac, 'Rang précédent': int(rank), 'Historique de rangement': '\n'.join(x for x in [f.get('Historique de rangement'), event] if x)})
    if bac_number(bac) >= 8:
        if f.get('Artiste') and clean_name(f['Artiste']) != f['Artiste']:
            out['Artiste'] = clean_name(f['Artiste'])
        if f.get('Label') and clean_name(f['Label']) != f['Label']:
            out['Label'] = clean_name(f['Label'])
        key = cle({**f, **out})
        if key: out['Clé'] = key
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
    rid = str(f.get(RID) or validated_reference(f) or '').strip()
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
    automatic = f.get('Enrichissement automatique', '').startswith('metadata-v') and f.get("Preuve d'identification") != 'Validation humaine'
    if automatic and f.get('Photos personnelles') and reference_conflicts(f, release):
        from reconnaitre_albums import reset_reference, clear_identity_markers
        out = reset_reference(f)
        source = clear_identity_markers(f.get('Transcription source', ''))
        source += f'\n[Référence rejetée automatiquement r{rid}: titre incompatible avec les grandes inscriptions]'
        out.update({'Artiste': None, 'Titre': None, 'Statut': 'À revoir', 'Cause': 'Transcription incertaine', 'Candidats Discogs': '[]', 'Transcription source': source,
                    REPORT: f'{VERSION} : référence contradictoire retirée; aucun enrichissement actif.',
                    'Diagnostic': 'Ancienne référence retirée : son titre ne concorde pas avec les grandes inscriptions de la pochette. Recherche automatique poursuivie.'})
        return out
    changed_reference = automatic and (str(f.get(RID, '')) != rid or not f.get('Enrichissement automatique', '').startswith(VERSION))
    if changed_reference:
        from reconnaitre_albums import reset_reference
        out.update(reset_reference(f))
        f = {**f, **out}
        out.update({RID: rid, REF: 'https://www.discogs.com/release/' + rid})
    if (not f.get('Titre') or automatic) and release.get('title'): out['Titre']=release['title']
    if not f.get('Artiste') or automatic:
        artist=clean_name(release.get('artists_sort') or ', '.join(a.get('name','') for a in release.get('artists',[])))
        if artist: out['Artiste']=artist
    canonical = re.findall(r'\[identite-canonique-v\d+:(\d+)\]', f.get('Transcription source', ''))
    if automatic and not confirmed and canonical and canonical[-1] == rid:
        out.update({'Statut': 'Pressage à confirmer', 'Cause': 'Numéro de catalogue absent ou incomplet' if not f.get('N° catalogue') else 'Données Discogs insuffisantes',
                    'Diagnostic': 'Identité de l’album synchronisée avec la référence canonique vérifiée; pressage physique non confirmé.'})
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
    # Convention B-01 à B-07 : Label et Année viennent de la version de référence, confirmée ou non.
    if not f.get('Label') and release.get('labels'): out['Label'] = clean_name(release['labels'][0].get('name'))
    if release.get('year') and not f.get('Année'): out['Année'] = release['year']
    if confirmed:
        if release.get('country') and not f.get('Pays du pressage'): out['Pays du pressage'] = release['country']
        fmt = physical_format(release)
        if fmt and not f.get('Format physique'): out['Format physique'] = fmt
        year = f.get('Année') or release.get('year')
        if year and 1940 <= int(year) < 2030 and not f.get('Décennie du pressage'): out['Décennie du pressage'] = str(int(year)//10*10)
    community = release.get('community', {})
    for field, value in [('Possédé par (Discogs)',community.get('have')),('Recherché par (Discogs)',community.get('want')),('Note Discogs (sur 5)',community.get('rating',{}).get('average')),('Nombre de notes (Discogs)',community.get('rating',{}).get('count')),('Exemplaires en vente (Discogs)',release.get('num_for_sale'))]:
        if value is not None: out[field] = value
    out.update(physical_hints(f,release))
    details=[f"https://www.discogs.com/release/{rid}", 'Référence uniquement.' if not confirmed else 'Pressage identifié.', 'Label : '+', '.join(x.get('name','') for x in release.get('labels',[])), 'Catalogue : '+', '.join(x.get('catno','') for x in release.get('labels',[])), 'Pays : '+str(release.get('country') or 'non documenté'), 'Année de cette version : '+str(release.get('year') or 'non documentée'), 'Format : '+str(physical_format(release) or 'non documenté')]
    out['Version de référence — détails']='\n'.join(details)
    for field,value in recording_metadata(release,confirmed).items():
        if not f.get(field): out[field]=value
    # Ne pas refaire les prix historiques déjà présents : priorité aux lacunes.
    if not f.get("Date de l'estimation") or f.get('Bac')=='B-08' or any(f.get(k) is None for k in ['Valeur minimale','Prix suggéré VG+ (Discogs)','Prix suggéré Mint (Discogs)']):
        out.update(market_metadata(f,release,headers,cache,confirmed))
    out[REPORT] = f'{VERSION} : album renseigné depuis r{rid}. ' + ('Pressage identifié.' if confirmed else f'Référence indicative : statistiques, prix et notes décrivent r{rid}; le pressage possédé reste à confirmer.') + ' Les images secondaires Discogs ne garantissent pas un verso; les informations personnelles et la réputation sonore exigent leurs propres sources.'
    return out

def reference_conflicts(fields, release):
    """Une attribution ancienne ne peut contredire plusieurs grands mots imprimés."""
    from reconnaitre_albums import words
    match = re.search(r'\[OCR neural regions\] ([^\n]+)', fields.get('Transcription source', ''))
    if not match: return False
    try: regions = json.loads(match[1])
    except (ValueError, TypeError): return False
    regions = [r for r in regions if r.get('confidence', 0) >= .3]
    if not regions: return False
    maximum = max(r.get('height', 0) for r in regions)
    printed = {w for r in regions if r.get('height', 0) >= maximum * .65 for w in words(r.get('text', '')) if len(w) >= 5}
    if len(printed) < 2: return False
    title = [w for w in words(release.get('title', '')) if len(w) >= 4]
    artist = words(release.get('artists_sort') or ', '.join(a.get('name', '') for a in release.get('artists', [])))
    return bool(title) and not any(a == b or SequenceMatcher(None, a, b).ratio() >= .72 for a in title + artist for b in printed)

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
    rows.sort(key=lambda r: (bool(r['fields'].get(REPORT)), '[identite-canonique-' not in r['fields'].get('Transcription source', ''), -int(r['fields'].get('Bac', 'B-0').split('-')[-1])))
    for row in rows:
        f=row['fields']
        if f.get('Ne pas retraiter'):continue
        updates=derived(row,totals)
        try:
            effective={**f,**updates}
            signature = VERSION + ':' + hashlib.sha256(json.dumps([effective.get('Artiste'),effective.get('Titre'),effective.get('N° catalogue'),effective.get('Label'),effective.get('Transcription source'),effective.get('Discogs release_id'),effective.get('Statut'),effective.get('Candidats Discogs')],ensure_ascii=False).encode()).hexdigest()[:12]
            report=f.get(REPORT,'')
            expired='Prochaine recherche automatique :' in report and retry_due(report, 'Prochaine recherche automatique')
            contradiction = bool(effective.get('Photos personnelles') and report.startswith('metadata-v') and effective.get("Preuve d'identification") != 'Validation humaine' and reference_conflicts(effective, {'title': effective.get('Titre', ''), 'artists_sort': effective.get('Artiste', '')}))
            needed = os.getenv('DERIVED_ONLY','0')!='1' and (not report.startswith(signature) or expired or contradiction) and (effective.get('Discogs release_id') or validated_reference(effective) or (effective.get('Titre') and effective.get('Artiste')))
            if needed and fetched < max(1, int(os.getenv('METADATA_BATCH_SIZE', '40'))):
                release=resolve(effective,dg,cache); fetched+=1
                if release:
                    updates.update(metadata(effective,release,dg,cache))
                    updates[REPORT] = updates[REPORT].replace(VERSION,signature,1)
                else: updates[REPORT]=f'{signature} : album sans correspondance certaine; photo conservée, aucune métadonnée inventée.\nProchaine recherche automatique : {next_retry(report=report)}'
            merged={**f,**updates}
            if REPORT in updates:
                # Fingerprint the fields actually saved, including enrichment hints.
                # Otherwise our own label/catalogue updates trigger another full lookup.
                final_signature = VERSION + ':' + hashlib.sha256(json.dumps([merged.get('Artiste'),merged.get('Titre'),merged.get('N° catalogue'),merged.get('Label'),merged.get('Transcription source'),merged.get('Discogs release_id'),merged.get('Statut'),merged.get('Candidats Discogs')],ensure_ascii=False).encode()).hexdigest()[:12]
                updates[REPORT] = updates[REPORT].replace(signature, final_signature, 1)
            updates.update(derived({**row,'fields':merged},totals))
            updates={k:v for k,v in updates.items() if f.get(k)!=v}
            if updates:
                if os.getenv('DRY_RUN','0')!='1':request_json(endpoint+'/'+row['id'],at,{'fields':updates})
                changed+=1; print(f"{row['id']} : {', '.join(updates)}")
        except Exception as exc:
            errors+=1;print(f"{row['id']} : échec technique {type(exc).__name__}: {exc}")
    print(f'Bilan : {len(rows)} fiches examinées, {changed} corrigées, {errors} erreurs.')
    if errors:raise SystemExit(1)

if __name__=='__main__':main()

