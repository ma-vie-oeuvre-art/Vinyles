#!/usr/bin/env python3
"""Audit non destructif du catalogue statique et de sa migration Airtable.

Sans jeton: audite uniquement le site existant.
Avec AIRTABLE_TOKEN: compare les exemplaires Airtable au site en lecture seule.
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "apporVr2Na6l5HGh2"
TABLE = "tblcqsKKwNW28bMSE"
SITE = Path(__file__).resolve().parents[1] / "index.html"

def main():
    html = SITE.read_text(encoding="utf-8")
    # Les entrées existantes sont intégrées à index.html sous forme de JSON compact.
    matches = list(re.finditer(r'\{"b":"B-\d{2}","o":\d+,', html))
    if not matches:
        raise RuntimeError("Catalogue intégré introuvable: aucune modification autorisée")
    print(f"Entrées détectées dans le site (approximatif): {len(matches)}")
    token = os.getenv("AIRTABLE_TOKEN")
    if not token:
        print("AIRTABLE_TOKEN absent: comparaison Airtable ignorée; aucune modification.")
        return 0
    rows = []
    cursor = None
    while True:
        params = {"pageSize": 100, "fields[]": ["Bac", "Ordre dans le bac", "Statut",
                                                  "Front Cover", "Back Cover",
                                                  "Discogs release_id"]}
        if cursor:
            params["offset"] = cursor
        url = f"https://api.airtable.com/v0/{BASE}/{TABLE}?" + urllib.parse.urlencode(params, doseq=True)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=30) as response:
            page = json.load(response)
        rows.extend(page.get("records", []))
        cursor = page.get("offset")
        if not cursor:
            break
    from collections import Counter
    by_bac = Counter(str(r["fields"].get("Bac", "")) for r in rows)
    b08 = [r["fields"] for r in rows if r["fields"].get("Bac") == "B-08"]
    positions = sorted(r.get("Ordre dans le bac") for r in b08 if isinstance(r.get("Ordre dans le bac"), (int, float)))
    photos = sum(bool(r.get("Front Cover")) for r in b08)
    identified = sum(bool(r.get("Discogs release_id")) for r in b08)
    print("Exemplaires Airtable:", len(rows))
    print("Répartition par bac:", json.dumps(dict(sorted(by_bac.items())), ensure_ascii=False))
    print(f"B-08 : {len(b08)} fiches, {photos} photos recto, {identified} identifiants Discogs confirmés")
    if b08 and positions != list(range(1, len(b08) + 1)):
        print("ATTENTION : positions B-08 manquantes ou en double", file=sys.stderr)
        return 2
    print("Audit terminé en lecture seule. Aucun vinyle et aucun fichier modifié.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
