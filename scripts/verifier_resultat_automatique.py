"""Distinguer la réussite technique des fiches effectivement complétées."""
import os
import json
import hashlib
import urllib.parse
from collections import defaultdict
from pathlib import Path
from enrichir_vinyles import request_json, API, BASE, TABLE

def main():
    headers = {'Authorization': 'Bearer ' + os.environ['AIRTABLE_TOKEN']}
    offset = None
    totals = defaultdict(lambda: [0, 0, 0, 0, 0, 0, 0])
    rows = {}
    while True:
        # Include uncaptured rows: a missing photo must not disappear from the audit.
        args = {'pageSize': 100}
        if offset:
            args['offset'] = offset
        page = request_json(f'{API}/{BASE}/{TABLE}?' + urllib.parse.urlencode(args), headers)
        for row in page.get('records', []):
            f = row['fields']
            stats = totals[f.get('Bac', 'Sans bac')]
            stats[0] += 1
            stats[1] += bool(f.get('Photos personnelles'))
            stats[2] += bool(f.get('Artiste') and f.get('Titre'))
            stats[3] += bool(f.get('Back Cover'))
            stats[4] += bool(f.get('Nombre de plages'))
            stats[5] += bool(f.get('Discogs référence release_id') and f.get('Nombre de plages') and f.get('Enrichissement automatique', '').startswith('metadata-v2'))
            stats[6] += bool(f.get('Discogs release_id') and f.get('Statut') == 'Identifié')
            key = (f.get('Bac'), f.get('Ordre dans le bac'))
            if key in rows:
                raise ValueError(f'Position en double : {key}')
            rows[key] = f
        offset = page.get('offset')
        if not offset:
            break
    # Check each declared photograph against the actual Airtable attachment.
    for manifest in (Path(__file__).resolve().parents[1] / 'inbox').glob('**/*.json'):
        for item in json.loads(manifest.read_text())['images']:
            photo = Path(__file__).resolve().parents[1] / item['path']
            if item.get('sha256') and hashlib.sha256(photo.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError(f'Image source tronquée : {item["path"]}')
            key = (item['bac'], item['position'])
            if key not in rows:
                print(f'::warning::Position absente de la base : {key}; aucune recréation automatique.')
                continue
            if not any(a.get('filename') == Path(item['path']).name for a in rows[key].get('Photos personnelles', [])):
                raise ValueError(f'Capture incomplète : photo manquante pour {key}')
    lines = ['| Bac | Fiches | Photos personnelles | Albums identifiés | Images secondaires | Listes de plages | Albums enrichis | Pressages confirmés |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for bac, stats in sorted(totals.items()):
        lines.append('| ' + bac + ' | ' + ' | '.join(map(str, stats)) + ' |')
        print(f'{bac}: {stats[1]}/{stats[0]} photos; {stats[2]}/{stats[0]} albums identifiés; {stats[5]}/{stats[0]} albums enrichis; {stats[6]}/{stats[0]} pressages confirmés.', flush=True)
        if stats[1] and stats[5] < stats[0]:
            print(f'::warning::{bac} : {stats[0]-stats[5]} fiche(s) encore sans enrichissement complet; ce bac reste en traitement automatique.')
    lines.append('\nUne exécution réussie ne signifie pas que toutes les données existent dans les sources. Les compteurs ci-dessus indiquent les résultats réels.')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as output:
            output.write('\n'.join(lines) + '\n')

if __name__ == '__main__':
    main()
