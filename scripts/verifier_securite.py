"""Check public pages without ever printing a possible credential."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = re.compile(r'AIRTABLE_TOKEN|DISCOGS_TOKEN|UPLOAD_KEY|Bearer\s+[A-Za-z0-9]{16,}', re.I)


def main():
    invalid = []
    for name in ('index.html', 'capture.html'):
        if FORBIDDEN.search((ROOT / name).read_text(encoding='utf-8')):
            invalid.append(name)
    if invalid:
        raise SystemExit('Contrôle des pages publiques en échec : ' + ', '.join(invalid))
    print('Pages publiques contrôlées; aucune clé exposée.')


if __name__ == '__main__':
    main()
