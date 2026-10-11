"""Synchroniser les bacs et rendre leur complétude explicite, sans inventer le comptage annoncé."""
import json
import os
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
from enrichir_vinyles import request_json, API, BASE, TABLE

BACS = 'tbln7WU5ucaw8CVq6'

def fetch_all(table, headers):
    rows, offset = [], None
    while True:
        params = {'pageSize': 100}
        if offset: params['offset'] = offset
        page = request_json(f'{API}/{BASE}/{table}?' + urllib.parse.urlencode(params), headers)
        rows.extend(page.get('records', []))
        offset = page.get('offset')
        if not offset: return rows

def summary(bac, rows, recorded, now):
    n = len(rows)
    ranks = [r['fields'].get('Ordre dans le bac') for r in rows]
    if len(set(ranks)) != n or any(not isinstance(rank, (int, float)) or rank < 1 or int(rank) != rank for rank in ranks):
        raise ValueError(f'{bac}: positions invalides ou dupliquées')
    missing = sorted(set(range(1, int(max(ranks, default=0)) + 1)) - set(ranks))
    photo = sum(bool(r['fields'].get('Photos personnelles')) for r in rows)
    identified = sum(bool(r['fields'].get('Artiste') and r['fields'].get('Titre')) for r in rows)
    enriched = sum(bool(r['fields'].get('Discogs référence release_id') and r['fields'].get('Tracklist JSON') and r['fields'].get('Enrichissement automatique', '').startswith('metadata-v')) for r in rows)
    confirmed = sum(r['fields'].get('Statut') == 'Identifié' and bool(r['fields'].get('Discogs release_id')) for r in rows)
    unresolved = sum(r['fields'].get('Statut') in (None, 'Dicté', 'À revoir', 'Non trouvé') for r in rows)
    complete = n > 0 and identified == n and enriched == n and not unresolved and not missing
    declared = recorded.get('Nombre annoncé')
    discrepancy = declared is not None and declared != n
    lines = [f"{'TRAITEMENT INCOMPLET' if not complete else 'ALBUMS TRAITÉS'} — contrôle GitHub {now}",
             f'Fiches inventoriées : {n}; photos personnelles : {photo}/{n}; albums nommés : {identified}/{n}; albums enrichis : {enriched}/{n}; pressages confirmés : {confirmed}/{n}.',
             f'Identification/recherche encore ouverte : {unresolved}.']
    if discrepancy: lines.append(f'ÉCART À VÉRIFIER : nombre annoncé {declared}; fiches présentes {n}. Comptage annoncé conservé.')
    if missing: lines.append('Rangs absents : ' + ', '.join(map(str, missing)))
    if declared is None: lines.append('Comptage annoncé non documenté; aucune valeur reconstituée comme annonce de Luc.')
    lines.append('Les pressages non confirmés et les informations de rangement restent distincts de la complétude du traitement des albums.')
    out = {'Nombre identifié': n, 'Audit de clôture': '\n'.join(lines)}
    families = Counter(r['fields'].get('Famille') for r in rows if r['fields'].get('Famille'))
    if families:
        family, count = families.most_common(1)[0]
        out['Famille dominante'] = f'{family} ({count} sur {n}; calcul automatique)'
    return out

def migrate_references(rows, headers):
    endpoint = f'{API}/{BASE}/{TABLE}'
    pending = []
    for row in rows:
        f = row['fields']
        rid = f.get('Discogs release_id')
        if not rid or f.get('Statut') == 'Identifié' or f.get('Ne pas retraiter'): continue
        source = f.get('Transcription source', '')
        stamp = f'[Migration référence indicative : ancien Discogs release_id={rid}; statut={f.get("Statut", "vide")}]'
        updates = {'Discogs release_id': None, 'Transcription source': source + ('\n' if source else '') + stamp}
        if not f.get('Discogs référence release_id'):
            updates.update({'Discogs référence release_id': str(rid), 'Discogs référence album': f'https://www.discogs.com/release/{rid}'})
        pending.append({'id': row['id'], 'fields': updates})
        f.update(updates)
    for start in range(0, len(pending), 10):
        request_json(endpoint, headers, {'records': pending[start:start+10]}, method='PATCH')
    print(f'{len(pending)} identifiant(s) non confirmés séparés du pressage physique; anciens identifiants archivés.', flush=True)

def main(migrate=False):
    headers = {'Authorization': 'Bearer ' + os.environ['AIRTABLE_TOKEN'], 'Content-Type': 'application/json'}
    rows = fetch_all(TABLE, headers)
    if migrate: migrate_references(rows, headers)
    groups = defaultdict(list)
    for row in rows:
        if row['fields'].get('Bac'): groups[row['fields']['Bac']].append(row)
    bacs = {}
    for row in fetch_all(BACS, headers):
        code = row['fields'].get('Bac')
        if code in bacs: raise ValueError(f'Bac dupliqué : {code}')
        bacs[code] = row
    now = datetime.now(ZoneInfo('America/Toronto')).isoformat(timespec='seconds')
    for code, group in sorted(groups.items()):
        prior = bacs.get(code)
        fields = summary(code, group, prior['fields'] if prior else {}, now)
        if prior:
            request_json(f'{API}/{BASE}/{BACS}/' + prior['id'], headers, {'fields': fields})
        else:
            request_json(f'{API}/{BASE}/{BACS}', headers, {'performUpsert': {'fieldsToMergeOn': ['Bac']}, 'records': [{'fields': {'Bac': code, **fields}}]}, method='PATCH')
        print(f'{code}: {fields["Audit de clôture"]}', flush=True)

if __name__ == '__main__':
    import sys
    main(migrate='--migrate-references' in sys.argv)
