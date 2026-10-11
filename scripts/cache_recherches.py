"""Partager les réponses Discogs entre étapes d'une seule exécution GitHub."""
import hashlib
import json
import os
import time
from pathlib import Path

def cache_path(url):
    temporary = os.environ.get('RUNNER_TEMP')
    if not temporary or not url.startswith('https://api.discogs.com/'):
        return None
    return Path(temporary) / 'vinyles-search-cache' / (hashlib.sha256(url.encode()).hexdigest() + '.json')

def cached_search(url):
    path = cache_path(url)
    if path and path.is_file() and time.time() - path.stat().st_mtime < 3600:
        try:
            result = json.loads(path.read_text())
            if isinstance(result, dict):
                return result
        except (OSError, ValueError):
            pass
    return None

def save_search(url, result):
    path = cache_path(url)
    if path and isinstance(result, dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(result), encoding='utf-8')
        temporary.replace(path)
