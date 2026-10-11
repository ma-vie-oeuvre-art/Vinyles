"""Verify resume, preservation and attachment repair without external writes."""
import base64
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
import importer_photos as imp


def check():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'inbox').mkdir()
        (root / 'photos').mkdir()
        (root / 'photos/one.jpeg').write_bytes(b'photo')
        (root / 'inbox/one.json').write_text(json.dumps({'images': [
            {'bac': 'B-10', 'position': 1, 'path': 'photos/one.jpeg'}]}))
        fields = {'Bac': 'B-10', 'Ordre dans le bac': 1, 'Statut': 'Identifié',
                  'Titre': 'Protected title', 'Transcription source': 'Original dictation',
                  'Photos personnelles': [{'id': 'attOld', 'filename': 'old.jpeg'}]}
        row = {'id': 'recExisting', 'fields': fields.copy()}
        ledger = []
        writes = []

        def request(url, headers, data=None, method=None):
            nonlocal ledger
            if url.endswith('state/imports.json'):
                if method == 'PUT':
                    ledger = json.loads(base64.b64decode(data['content']))
                    return {}
                return {'sha': 'ledgerSha', 'content': base64.b64encode(json.dumps(ledger).encode()).decode()}
            if data:
                assert method == 'PATCH', 'Existing row must never be recreated'
                writes.extend(data['records'])
                updated = data['records'][0]['fields']
                assert updated['Photos personnelles'][0] == {'id': 'attOld'}
                updated['Photos personnelles'] = [fields['Photos personnelles'][0],
                    {'id': 'attNew', 'filename': 'one.jpeg'}]
                row['fields'].update(updated)
            return {'records': [row]}

        with patch.object(imp, 'ROOT', root), patch.object(imp, 'request_json', request), patch.dict(os.environ,
                {'AIRTABLE_TOKEN': 'test', 'GITHUB_TOKEN': 'test', 'GITHUB_REPOSITORY': 'owner/repo', 'GITHUB_SHA': 'source'}):
            imp.main()
            assert len(writes) == 1
            assert row['fields']['Statut'] == 'Identifié'
            assert row['fields']['Titre'] == 'Protected title'
            assert row['fields']['Transcription source'].startswith('Original dictation\n')
            # A retry after writes but before ledger persistence must not append duplicates.
            ledger = []
            imp.main()
            assert len(writes) == 1
            # A completed import does not recreate an intentionally removed row.
            row.clear()
            def retired(url, headers, data=None, method=None):
                if url.endswith('state/imports.json'):
                    return {'sha': 'ledgerSha', 'content': base64.b64encode(json.dumps(ledger).encode()).decode()}
                assert not data
                return {'records': []}
            with patch.object(imp, 'request_json', retired):
                imp.main()
        print('Attachment repair, original data, prior photos, interrupted retry and retired rows verified.')


if __name__ == '__main__':
    check()
