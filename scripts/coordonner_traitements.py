"""Remplacer une exécution obsolète après correction du code, sans annuler les lots."""
import json
import os
import urllib.request

WORKFLOW = '.github/workflows/enrichir-vinyles.yml'

def request(path, method='GET'):
    repo = os.environ['GITHUB_REPOSITORY']
    req = urllib.request.Request('https://api.github.com/repos/' + repo + '/' + path,
        method=method, headers={'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
        'Accept': 'application/vnd.github+json', 'User-Agent': 'VinylesInventaire/1.0'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response) if method == 'GET' else None

def main():
    current = int(os.environ['GITHUB_RUN_ID'])
    head = os.environ['GITHUB_SHA']
    # Include queued runs: one can start between listing and cancellation of its predecessor.
    runs = request('actions/runs?per_page=100')['workflow_runs']
    for run in runs:
        if (run.get('status') not in {'in_progress', 'queued', 'pending', 'waiting', 'requested'}
                or run['id'] == current or run.get('path') != WORKFLOW
                or run.get('head_branch') != 'main' or run['head_sha'] == head
                or int(run['id']) > current):
            continue
        changes = request(f'compare/{run["head_sha"]}...{head}')
        # Photo-only pushes keep the active work. Code corrections take effect now.
        if any(f['filename'].startswith('scripts/') or f['filename'] == WORKFLOW
               for f in changes.get('files', [])):
            request(f'actions/runs/{run["id"]}/cancel', 'POST')
            print(f'Ancienne version remplacée : exécution {run["id"]}. Les fiches déjà enregistrées sont conservées.', flush=True)

if __name__ == '__main__':
    main()
