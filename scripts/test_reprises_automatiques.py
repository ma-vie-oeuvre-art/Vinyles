"""Vérifier les délais, les nouvelles preuves et la comparaison concurrente sans API."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from types import SimpleNamespace
from reprises_automatiques import next_retry, retry_due
from cache_recherches import cached_search, save_search
import comparer_pochettes as visual
from reconnaitre_albums import recognition_stamp, should_search

now = datetime(2026, 10, 11, 2, 0, tzinfo=timezone.utc)
for label in ['Recherche album suivante', 'Recherche visuelle suivante', 'Prochaine recherche automatique']:
    report = label + ' : ' + next_retry(now)
    assert not retry_due(report, label, now + timedelta(minutes=14))
    assert retry_due(report, label, now + timedelta(minutes=15))
    assert retry_due(label + ' : 2026-10-12', label, now)
    assert not retry_due(label + ' : 2026-10-12\n' + report, label, now)
    assert retry_due(label + ' : invalid', label, now)

future = next_retry(datetime.now(timezone.utc) + timedelta(hours=1))
diagnostic = recognition_stamp('old text') + '\nRecherche album suivante : ' + future
assert not should_search('old text', diagnostic, now.date())
assert should_search('new text', diagnostic, now.date())
fields = {'Transcription source': 'old evidence', 'Candidats Discogs': '[]'}
fields['Diagnostic'] = visual.fingerprint(fields) + '\nRecherche visuelle suivante : ' + future
assert not visual.should_compare(fields)
assert visual.should_compare({**fields, 'Transcription source': 'new evidence'})

with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'RUNNER_TEMP': directory}):
    url = 'https://api.discogs.com/database/search?q=mock&type=release'
    assert cached_search(url) is None
    save_search(url, {'results': []})
    assert cached_search(url) == {'results': []}
    assert cached_search('https://api.discogs.com/releases/1') is None
    calls = []
    with patch.object(visual, '_request_json', side_effect=lambda *args: calls.append(args)):
        assert visual.request_json(url, {}) == {'results': []}
    assert not calls

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    (root / 'mock.jpg').write_bytes(b'mocked image, never decoded')
    selection = root / 'selection.json'
    selection.write_text(json.dumps([{'id': 'mock-row'}]))
    candidates = [{'release_id': i, 'master_id': 1, 'titre': 'Artist - Album', 'cover_url': 'mock:' + str(i)} for i in range(1, 5)]
    fields = {'Transcription source': 'fichier source: mock.jpg', 'Candidats Discogs': json.dumps(candidates)}
    barrier = threading.Barrier(2)
    threads = set()
    def compare(*args):
        threads.add(threading.get_ident())
        barrier.wait(timeout=5)
        return 80
    writes = []
    def request(url, headers, data=None):
        if data:
            writes.append(data['fields'])
            return {}
        return {'fields': fields}
    canonical = {'Artiste': 'Artist', 'Titre': 'Album', 'Discogs référence release_id': '1', '_canonical_candidate': candidates[0]}
    with patch.dict(sys.modules, {'cv2': SimpleNamespace(setNumThreads=lambda n: None)}), \
         patch.object(visual, 'ROOT', root), patch.object(visual, 'SELECTION', selection), \
         patch.object(visual, 'features', return_value='mocked features'), \
         patch.object(visual, 'download', return_value=b'mocked reference'), \
         patch.object(visual, 'compare', side_effect=compare), \
         patch.object(visual, 'request_json', side_effect=request), \
         patch.object(visual, 'headers', return_value={}), \
         patch.object(visual, 'canonical_fields', return_value=canonical):
        visual.run()
    assert len(threads) == 2, 'Comparisons must execute concurrently'
    assert len(writes) == 1 and writes[0]['Titre'] == 'Album'
    assert 'Discogs release_id' not in writes[0], 'No physical pressing inferred'
print('Reprises à 15 minutes, abandon des anciens reports, nouvelles preuves immédiates, cache et comparaisons concurrentes vérifiés.')
