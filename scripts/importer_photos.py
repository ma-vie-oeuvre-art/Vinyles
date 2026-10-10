"""GitHub importe les lots d'images une seule fois, sans recréer les fiches supprimées."""
import json
import os
import urllib.error
import urllib.parse
from pathlib import Path
from enrichir_vinyles import request_json, API, BASE, TABLE

ROOT = Path(__file__).resolve().parents[1]

def main():
    at = {'Authorization': 'Bearer ' + os.environ['AIRTABLE_TOKEN'], 'Content-Type': 'application/json'}
    gh = {'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'], 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json'}
    repo = os.environ['GITHUB_REPOSITORY']
    ledger_url = f'https://api.github.com/repos/{repo}/contents/state/imports.json'
    try:
        remote = request_json(ledger_url, gh)
        import base64
        ledger = json.loads(base64.b64decode(remote['content']))
        ledger_sha = remote['sha']
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        ledger, ledger_sha = [], None
    done = set(ledger)
    endpoint = f'{API}/{BASE}/{TABLE}'
    rows, cursor = [], None
    while True:
        params = {'pageSize': 100}
        if cursor:
            params['offset'] = cursor
        page = request_json(endpoint + '?' + urllib.parse.urlencode(params), at)
        rows += page.get('records', [])
        cursor = page.get('offset')
        if not cursor:
            break
    existing = {(r['fields'].get('Bac'), r['fields'].get('Ordre dans le bac')) for r in rows}
    for manifest in sorted((ROOT / 'inbox').glob('**/*.json')):
        relative = manifest.relative_to(ROOT).as_posix()
        if relative in done:
            continue
        items = json.loads(manifest.read_text())['images']
        for item in items:
            photo = (ROOT / item['path']).resolve()
            if ROOT not in photo.parents or not photo.is_file():
                raise ValueError('Image source absente ou chemin invalide')
            bac, position = item['bac'], item['position']
            if not isinstance(position, int) or position < 1:
                raise ValueError('Position invalide')
            key = (bac, position)
            if key in existing:
                continue
            source_sha = os.environ.get('PHOTO_SOURCE_SHA') or os.environ['GITHUB_SHA']
            url = f'https://raw.githubusercontent.com/{repo}/{source_sha}/{item["path"]}'
            fields = {'Clé': f'{bac}-{position:03}', 'Bac': bac, 'Ordre dans le bac': position,
                      'Statut': 'Dicté', 'Photos personnelles': [{'url': url, 'filename': photo.name}],
                      'Transcription source': f'Photo personnelle — fichier source: {item["path"]}\nTraitement automatique GitHub; pressage précis reporté au besoin.'}
            request_json(endpoint, at, {'records': [{'fields': fields}]}, method='POST')
            existing.add(key)
        done.add(relative)
        print(f'Lot transféré dans Airtable : {relative}')
    if done != set(ledger):
        import base64
        body = {'message': 'Mémoriser les lots de photos importés', 'content': base64.b64encode(json.dumps(sorted(done)).encode()).decode()}
        if ledger_sha:
            body['sha'] = ledger_sha
        request_json(ledger_url, gh, body, method='PUT')

if __name__ == '__main__':
    main()
