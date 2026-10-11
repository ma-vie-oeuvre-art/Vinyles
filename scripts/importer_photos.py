"""GitHub importe les lots d'images une seule fois, sans recréer les fiches supprimées."""
import json
import hashlib
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
    existing = {}
    for row in rows:
        key = (row['fields'].get('Bac'), row['fields'].get('Ordre dans le bac'))
        if key in existing:
            raise ValueError(f'Position en double dans Airtable : {key}')
        existing[key] = row
    for manifest in sorted((ROOT / 'inbox').glob('**/*.json')):
        relative = manifest.relative_to(ROOT).as_posix()
        if relative in done:
            continue
        items = json.loads(manifest.read_text())['images']
        # Validate the complete batch before performing any writes.
        keys = set()
        for item in items:
            photo = (ROOT / item['path']).resolve()
            if ROOT not in photo.parents or not photo.is_file():
                raise ValueError('Image source absente ou chemin invalide')
            if item.get('sha256') and hashlib.sha256(photo.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('Photo source incomplète : empreinte incorrecte')
            bac, position = item['bac'], item['position']
            if not isinstance(position, int) or position < 1:
                raise ValueError('Position invalide')
            if not isinstance(bac, str) or not __import__('re').fullmatch(r'B-\d{1,5}', bac):
                raise ValueError('Bac invalide')
            key = (bac, position)
            if key in keys:
                raise ValueError('Position en double dans le lot')
            keys.add(key)
        pending_create, pending_update = [], []
        expected = []
        for item in items:
            photo = ROOT / item['path']
            bac, position = item['bac'], item['position']
            key = (bac, position)
            source_sha = os.environ.get('PHOTO_SOURCE_SHA') or os.environ['GITHUB_SHA']
            url = f'https://raw.githubusercontent.com/{repo}/{source_sha}/{item["path"]}'
            attachment = {'url': url, 'filename': photo.name}
            source_line = f'Photo personnelle — fichier source: {item["path"]}'
            expected.append((key, photo.name))
            if key in existing:
                row = existing[key]
                prior = row['fields'].get('Photos personnelles') or []
                source = row['fields'].get('Transcription source', '')
                stamp = '[Photo complète sha256:' + item['sha256'] + ']' if item.get('refresh_attachment') and item.get('sha256') else None
                if any(a.get('filename') == photo.name for a in prior) and (not stamp or stamp in source):
                    continue
                retained = [a for a in prior if not stamp or a.get('filename') != photo.name]
                fields = {'Photos personnelles': [{'id': a['id']} for a in retained] + [attachment]}
                if source_line not in source:
                    source += ('\n' if source else '') + source_line
                if stamp and stamp not in source:
                    source += '\n' + stamp
                if source != row['fields'].get('Transcription source', ''):
                    fields['Transcription source'] = source
                pending_update.append({'id': row['id'], 'fields': fields})
                continue
            fields = {'Clé': f'{bac}-{position:03}', 'Bac': bac, 'Ordre dans le bac': position,
                      'Statut': 'Dicté', 'Photos personnelles': [attachment],
                      'Transcription source': f'Photo personnelle — fichier source: {item["path"]}\nTraitement automatique GitHub; pressage précis reporté au besoin.'}
            pending_create.append({'fields': fields})
        # Airtable accepts ten records per call; one submitted batch stays atomic.
        for operation, method in ((pending_update, 'PATCH'), (pending_create, 'POST')):
            for start in range(0, len(operation), 10):
                result = request_json(endpoint, at, {'records': operation[start:start+10]}, method=method)
                for row in result.get('records', []):
                    existing[(row['fields']['Bac'], row['fields']['Ordre dans le bac'])] = row
        for key, filename in expected:
            if not any(a.get('filename') == filename for a in existing[key]['fields'].get('Photos personnelles', [])):
                raise ValueError(f'Photo non confirmée par Airtable : {key} / {filename}')
        done.add(relative)
        print(f'Lot vérifié dans Airtable : {relative}; {len(pending_create)} créations, {len(pending_update)} fiches complétées, {len(expected)} photos confirmées.', flush=True)
    if done != set(ledger):
        import base64
        body = {'message': 'Mémoriser les lots de photos importés', 'content': base64.b64encode(json.dumps(sorted(done)).encode()).decode()}
        if ledger_sha:
            body['sha'] = ledger_sha
        request_json(ledger_url, gh, body, method='PUT')

if __name__ == '__main__':
    main()
