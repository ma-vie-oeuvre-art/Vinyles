#!/usr/bin/env python3
"""Recalcule Famille et Familles des bacs B-08 et suivants avec la règle de classification courante.

Lancé sur demande (workflow_dispatch, entrée « recalculer_familles »), pas à chaque passage :
il relit chaque référence Discogs. Les fiches gelées (Ne pas retraiter) ne sont pas touchées.
"""
import os
import urllib.parse

from completer_metadonnees import request_json, classification, API, BASE, TABLE, RID


def bac_number(code):
    try:
        return int(str(code).split('-')[-1])
    except ValueError:
        return 0


def main():
    at = {'Authorization': 'Bearer ' + os.environ['AIRTABLE_TOKEN'], 'Content-Type': 'application/json'}
    dg = {'User-Agent': 'VinylesInventaire/1.0 (personal catalog)', 'Authorization': 'Discogs token=' + os.environ['DISCOGS_TOKEN']}
    endpoint = f'{API}/{BASE}/{TABLE}'
    rows, cursor = [], None
    while True:
        params = {'pageSize': 100}
        if cursor:
            params['offset'] = cursor
        page = request_json(endpoint + '?' + urllib.parse.urlencode(params), at)
        rows.extend(page.get('records', []))
        cursor = page.get('offset')
        if not cursor:
            break
    changed = errors = 0
    cache = {}
    for row in rows:
        f = row['fields']
        if bac_number(f.get('Bac')) < 8 or f.get('Ne pas retraiter'):
            continue
        rid = str(f.get('Discogs release_id') or f.get(RID) or '').strip()
        if not rid.isdecimal():
            continue
        try:
            if rid not in cache:
                cache[rid] = request_json('https://api.discogs.com/releases/' + rid, dg)
            family, tags = classification(cache[rid])
            updates = {}
            if f.get('Famille') != family:
                updates['Famille'] = family
            if sorted(f.get('Familles') or []) != sorted(tags):
                updates['Familles'] = tags
            if updates:
                if os.getenv('DRY_RUN', '0') != '1':
                    request_json(endpoint + '/' + row['id'], at, {'fields': updates, 'typecast': True})
                changed += 1
                print(f"{f.get('Bac')} rang {f.get('Ordre dans le bac')} — {f.get('Titre')} : {f.get('Famille')} → {family}; {tags}")
        except Exception as exc:
            errors += 1
            print(f"{row['id']} : échec {type(exc).__name__}: {exc}")
    print(f'Bilan : {changed} fiche(s) reclassée(s), {errors} erreur(s).')
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
