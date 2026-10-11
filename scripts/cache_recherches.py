"""Partager les réponses Discogs entre étapes d'une seule exécution GitHub."""
import hashlib
import json
import os
from pathlib import Path

def cache_path(url):
    temporary = os.environ.get('RUNNER_TEMP')
    if not temporary or not url.startswith('https://api.discogs.com/database/search?'):
        return None
    return Path(temporary) / 'vinyles-search-cache' / (hashlib.sha256(url.encode()).hexdigest() + '.json')

def cached_search(url):
    path = cache_path(url)
    if path and path.is_file():
        try:
            result = json.loads(path.read_text())
            if isinstance(result, dict) and isinstance(result.get('results'), list):
                return result
        except (OSError, ValueError):
            pass
    return None

def save_search(url, result):
    path = cache_path(url)
    if path and isinstance(result, dict) and isinstance(result.get('results'), list):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(result), encoding='utf-8')
        temporary.replace(path)
