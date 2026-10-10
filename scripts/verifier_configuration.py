#!/usr/bin/env python3
"""Contrôle préalable: aucun lancement d'enrichissement sans configuration valide."""
import os
import sys

required = ("AIRTABLE_TOKEN", "DISCOGS_TOKEN")
missing = [key for key in required if not os.environ.get(key, "").strip()]
if missing:
    print("Configuration incomplète : " + ", ".join(missing), file=sys.stderr)
    sys.exit(1)
print("Secrets présents dans l'environnement (valeurs non affichées).")
