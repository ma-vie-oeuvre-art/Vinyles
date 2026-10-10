"""Distinguer la réussite technique des fiches effectivement complétées."""
import os
import urllib.parse
from collections import defaultdict
from enrichir_vinyles import request_json, API, BASE, TABLE

def main():
    headers = {'Authorization': 'Bearer ' + os.environ['AIRTABLE_TOKEN']}
    offset = None
    totals = defaultdict(lambda: [0, 0, 0, 0, 0])
    while True:
        args = {'pageSize': 100, 'filterByFormula': '{Photos personnelles}'}
        if offset:
            args['offset'] = offset
        page = request_json(f'{API}/{BASE}/{TABLE}?' + urllib.parse.urlencode(args), headers)
        for row in page.get('records', []):
            f = row['fields']
            stats = totals[f.get('Bac', 'Sans bac')]
            stats[0] += 1
            stats[1] += bool(f.get('Artiste') and f.get('Titre'))
            stats[2] += bool(f.get('Front Cover'))
            stats[3] += bool(f.get('Back Cover'))
            stats[4] += bool(f.get('Nombre de plages'))
        offset = page.get('offset')
        if not offset:
            break
    lines = ['| Bac | Photos | Albums identifiés | Rectos disponibles | Versos disponibles | Listes de plages |',
             '| --- | ---: | ---: | ---: | ---: | ---: |']
    for bac, stats in sorted(totals.items()):
        lines.append('| ' + bac + ' | ' + ' | '.join(map(str, stats)) + ' |')
        print(f'{bac}: {stats[1]}/{stats[0]} albums identifiés; {stats[0]-stats[1]} encore incertains.', flush=True)
    lines.append('\nUne exécution réussie ne signifie pas que toutes les données existent dans les sources. Les compteurs ci-dessus indiquent les résultats réels.')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as output:
            output.write('\n'.join(lines) + '\n')

if __name__ == '__main__':
    main()
