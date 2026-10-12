"""Laisser les traitements actifs finir; supprimer seulement les attentes obsolètes."""
import json
import os
import urllib.request
import urllib.error

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
        if (run.get('status') not in {'queued', 'pending', 'waiting', 'requested'}
                or run['id'] == current or run.get('path') != WORKFLOW
                or run.get('head_branch') != 'main' or run['head_sha'] == head
                or int(run['id']) > current):
            continue
        changes = request(f'compare/{run["head_sha"]}...{head}')
        # Photo-only pushes keep the active work. Code corrections take effect now.
        if any(f['filename'].startswith('scripts/') or f['filename'] == WORKFLOW
               for f in changes.get('files', [])):
            try:
                request(f'actions/runs/{run["id"]}/cancel', 'POST')
            except urllib.error.HTTPError as exc:
                # The run may have completed during comparison; no cancellation needed.
                if exc.code == 409 and request(f'actions/runs/{run["id"]}').get('status') == 'completed':
                    continue
                raise
            print(f'Attente obsolète remplacée : exécution {run["id"]}. Les traitements actifs continuent jusqu’au bout.', flush=True)

if __name__ == '__main__':
    main()
