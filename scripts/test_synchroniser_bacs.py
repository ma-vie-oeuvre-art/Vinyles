"""Contrôles métier : pas de comptage annoncé inventé ni de confirmation de pressage."""
from unittest.mock import patch
from synchroniser_bacs import summary, migrate_references
from completer_metadonnees import metadata

def row(rank, **fields):
    return {'id': 'rec' + str(rank), 'fields': {'Bac': 'B-08', 'Ordre dans le bac': rank, **fields}}

rs = [row(1), row(2)]
out = summary('B-08', rs, {'Nombre annoncé': 3}, 'now')
assert out['Nombre identifié'] == 2 and 'ÉCART À VÉRIFIER' in out['Audit de clôture']
assert 'Nombre annoncé' not in out and 'TRAITEMENT INCOMPLET' in out['Audit de clôture']
assert 'Comptage annoncé non documenté' in summary('B-09', rs, {}, 'now')['Audit de clôture']
try:
    summary('B-08', [row(1), row(1)], {}, 'now')
    raise AssertionError('Duplicate positions accepted')
except ValueError:
    pass

writes = []
rs = [row(1, **{'Discogs release_id': '123', 'Statut': 'Pressage à confirmer', 'Transcription source': 'Preuve conservée'}),
      row(2, **{'Discogs release_id': '456', 'Statut': 'Identifié'})]
with patch('synchroniser_bacs.request_json', side_effect=lambda *args, **kw: writes.append(args[2])):
    migrate_references(rs, {})
assert rs[0]['fields']['Discogs release_id'] is None
assert rs[0]['fields']['Discogs référence release_id'] == '123'
assert 'Preuve conservée' in rs[0]['fields']['Transcription source'] and 'ancien Discogs release_id=123' in rs[0]['fields']['Transcription source']
assert rs[1]['fields']['Discogs release_id'] == '456' and len(writes[0]['records']) == 1

f = {'Artiste': 'Wrong artist', 'Titre': 'Wrong title', 'Discogs référence release_id': '2',
     'Tracklist JSON': '[{"title":"Wrong track"}]', 'Enrichissement automatique': 'metadata-v2:old',
     'Transcription source': '[identite-canonique-v2:2]', 'Statut': 'À revoir', 'Notes': 'Personal note',
     'Images verrouillées': True, 'Front Cover': [{'id': 'personal-cover'}]}
release = {'id': 2, 'title': 'Correct title', 'artists_sort': 'Correct artist', 'tracklist': [{'type_': 'track', 'position': 'A1', 'title': 'Correct track'}]}
with patch('completer_metadonnees.market_metadata', return_value={}):
    out = metadata(f, release, {}, {})
assert out['Titre'] == 'Correct title' and out['Artiste'] == 'Correct artist'
assert 'Correct track' in out['Tracklist JSON'] and 'Wrong track' not in out['Tracklist JSON']
assert out['Statut'] == 'Pressage à confirmer'
assert 'Notes' not in out and 'Front Cover' not in out and 'Discogs release_id' not in out
with patch('completer_metadonnees.market_metadata', return_value={}):
    out = metadata({**f, "Preuve d'identification": 'Validation humaine'}, release, {}, {})
assert 'Titre' not in out and 'Artiste' not in out and 'Statut' not in out
print('Comptage, références indicatives, métadonnées périmées, images verrouillées et données humaines vérifiés.')
