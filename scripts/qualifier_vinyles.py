#!/usr/bin/env python3
"""Qualifier les recherches des pochettes importées, sans inventer de pressage confirmé."""
import json
import os
import re
import time
import unicodedata
import urllib.parse
from enrichir_vinyles import request_json, API, BASE, TABLE

def norm(text):
    return re.sub(r'[^A-Z0-9]', '', unicodedata.normalize('NFKD', str(text)).encode('ascii', 'ignore').decode().upper())

def album_norm(text):
    # Descripteurs typographiques du recto, pas variantes de catalogue.
    text = str(text)
    text = re.sub(r'\bStereophonic\b', '', text, flags=re.I)
    text = re.sub(r'\bVolumes?\b', 'Vol', text, flags=re.I)
    return norm(text)

def concordant(fields, candidate):
    if not isinstance(candidate, dict) or not candidate.get('release_id'):
        return False
    title = str(candidate.get('titre') or '')
    # Les crédits classiques peuvent eux-mêmes contenir « - ».
    parts = title.split(' - ')
    artist = fields.get('Artiste', '').split(' / ')[0]
    def without_possessive(text):
        prefix = re.escape(artist) + r"[’']s\s+"
        return re.sub(r'^' + prefix, '', text, flags=re.I) if artist else text
    expected = album_norm(without_possessive(fields.get('Titre', '')))
    splits = [(' - '.join(parts[:i]), ' - '.join(parts[i:])) for i in range(1, len(parts))]
    matching = [(credit, album) for credit, album in splits
                if album_norm(without_possessive(album)) == expected]
    if not matching:
        return False
    credit, album = matching[0]
    if norm(artist) not in ('ARTISTESVARIES', 'AUDIOFIDELITY', 'VARIOUS', ''):
        if norm(artist) not in norm(credit):
            # Prénom omis : accepter seulement plusieurs personnes toutes créditées.
            people = fields.get('Artiste', '').split(' / ')
            surnames = [norm(person.split()[-1]) for person in people if person.split()]
            credit_words = set(re.findall(r'[A-Z0-9]+', unicodedata.normalize('NFKD', credit).encode('ascii', 'ignore').decode().upper()))
            if len(surnames) < 2 or not all(name in credit_words for name in surnames):
                return False
    cat = fields.get('N° catalogue')
    return not cat or norm(candidate.get('catno', '')) == norm(cat)

def main():
    at = {'Authorization': 'Bearer ' + os.environ['AIRTABLE_TOKEN'], 'Content-Type': 'application/json'}
    dg = {'Authorization': 'Discogs token=' + os.environ['DISCOGS_TOKEN'], 'User-Agent': 'VinylesInventaire/1.0 (personal catalog)'}
    endpoint = f'{API}/{BASE}/{TABLE}'
    args = {'pageSize': 100, 'filterByFormula': "AND({Photos personnelles},{Artiste},{Titre},OR({Statut}='Dicté',{Statut}='À revoir',{Statut}='Pressage à confirmer'))"}
    rows = []
    while True:
        page = request_json(endpoint + '?' + urllib.parse.urlencode(args), at)
        rows.extend(page.get('records', []))
        if not page.get('offset'):
            break
        args['offset'] = page['offset']
    errors = 0
    for row in rows:
        f = row.get('fields', {})
        if f.get('Discogs release_id') or f.get('Ne pas retraiter'):
            continue
        try:
            raw = f.get('Candidats Discogs')
            candidates = json.loads(raw) if raw else None
            if candidates is None:
                continue  # recherche non effectuée, ne pas simuler un résultat
            if not candidates:
                title = f.get('Titre', '').split(' — ')[0]
                artist = f.get('Artiste', '').split(' / ')[0]
                params = {'type': 'release', 'format': 'Vinyl', 'per_page': 50, 'release_title': title}
                if norm(artist) not in ('ARTISTESVARIES', 'AUDIOFIDELITY', 'VARIOUS', ''):
                    params['artist'] = artist
                search = request_json('https://api.discogs.com/database/search?' + urllib.parse.urlencode(params), dg)
                candidates = [{'release_id': x.get('id'), 'titre': x.get('title'), 'catno': x.get('catno'), 'label': x.get('label'), 'pays': x.get('country'), 'annee': x.get('year')} for x in search.get('results', [])]
                time.sleep(2.2)
            matches = [c for c in candidates if concordant(f, c)]
            updates = {'Candidats Discogs': json.dumps(candidates, ensure_ascii=False)}
            if matches:
                # Un candidat concordant n'est jamais une preuve du pressage physique.
                updates.update({'Statut': 'Pressage à confirmer', 'Cause': 'Plusieurs pressages plausibles' if len(matches) > 1 else 'Données Discogs insuffisantes', 'Diagnostic': f'{len(matches)} candidat(s) concordant(s) sur {len(candidates)} examinés. Photo originale conservée. Pressage à départager par verso, étiquette ou matrices; aucune valeur attribuée.'})
            else:
                updates.update({'Statut': 'À revoir', 'Cause': 'Transcription incertaine' if candidates else 'Aucun résultat Discogs', 'Diagnostic': f'Recherche effectuée : {len(candidates)} candidat(s), aucun concordant avec les données de l’objet. Photo originale conservée; catalogue et transcription à vérifier.'})
            if not f.get('N° catalogue'):
                updates['Cause'] = 'Numéro de catalogue absent ou incomplet'
            if all(f.get(key) == value for key, value in updates.items()):
                continue
            if os.getenv('DRY_RUN', '0') != '1':
                request_json(endpoint + '/' + row['id'], at, {'fields': updates})
            print(f"{row['id']}: {updates['Statut']}; {len(matches)} concordant(s)")
        except Exception as exc:
            errors += 1
            print(f"{row['id']}: erreur technique {type(exc).__name__}; statut conservé")
    if errors:
        raise SystemExit(f'{errors} qualification(s) en échec technique')

if __name__ == '__main__':
    main()
