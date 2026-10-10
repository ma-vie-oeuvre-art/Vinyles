#!/usr/bin/env python3
"""Publication incrémentale Airtable -> GitHub Pages. Aucune suppression de fiches existantes."""
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "apporVr2Na6l5HGh2"
TABLE = "tblcqsKKwNW28bMSE"
MARKER = "const D = /*__DONNEES__*/"
SITE = Path(__file__).resolve().parents[1] / "index.html"

def fetch_rows(token):
    rows, cursor = [], None
    while True:
        args = {"pageSize": 100}
        if cursor:
            args["offset"] = cursor
        url = f"https://api.airtable.com/v0/{BASE}/{TABLE}?" + urllib.parse.urlencode(args)
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(request, timeout=35) as response:
            page = json.load(response)
        rows += [r["fields"] for r in page.get("records", [])]
        cursor = page.get("offset")
        if not cursor:
            return rows

def as_url(value):
    if isinstance(value, list) and value:
        return value[0].get("url")
    return None

def main():
    token = os.getenv("AIRTABLE_TOKEN")
    if not token:
        print("AIRTABLE_TOKEN absent : publication impossible, site inchangé.", file=sys.stderr)
        return 1
    original = SITE.read_text(encoding="utf-8")
    if original.count(MARKER) != 1:
        raise ValueError("Marqueur de données absent ou ambigu")
    start = original.index(MARKER) + len(MARKER)
    # JSONDecoder identifie exactement le tableau, sans modifier le reste du site.
    whitespace = len(original[start:]) - len(original[start:].lstrip())
    start += whitespace
    existing, consumed = json.JSONDecoder().raw_decode(original[start:])
    if not isinstance(existing, list) or len(existing) < 200:
        raise ValueError("Catalogue historique inattendu : publication interrompue")
    end = start + consumed
    by_key = {(r.get("b"), r.get("o")) for r in existing}
    if len(by_key) != len(existing):
        raise ValueError("Doublons dans le catalogue historique : publication interrompue")
    rows = fetch_rows(token)
    added, pending = [], 0
    for r in rows:
        bac, pos = r.get("Bac"), r.get("Ordre dans le bac")
        if not bac or not isinstance(pos, (int, float)) or int(pos) != pos:
            continue
        key = (bac, int(pos))
        if key in by_key:
            continue
        cover = as_url(r.get("Front Cover"))
        release = r.get("Discogs release_id")
        # Ne publie pas les fiches provisoires, sans identification ET sans pochette.
        if not (cover and release and r.get("Titre") and r.get("Artiste")):
            pending += 1
            continue
        item = {
            "b": bac, "o": int(pos), "t": r["Titre"], "a": r["Artiste"],
            "y": str(r["Année"]) if r.get("Année") else "",
            "f": "À classer", "r": "", "k": " ".join(
                x for x in [r.get("Label"), r.get("N° catalogue")] if x),
            "d": str(release), "th": cover, "rc": as_url(r.get("Back Cover")),
            "vs": None, "n": None, "v": None, "m": None, "x": None,
            "h": None, "w": None, "no": None, "ev": None,
            "fa": [], "fp": "", "pa": r.get("Pays du pressage") or "",
            "dc": "",
        }
        added.append(item)
        by_key.add(key)
    if not added:
        print(f"Aucune nouvelle fiche prête. {pending} fiche(s) en attente. Site inchangé.")
        return 0
    output = existing + sorted(added, key=lambda r: (r["b"], r["o"]))
    rebuilt = original[:start] + json.dumps(output, ensure_ascii=False, separators=(",", ":")) + original[end:]
    if os.getenv("DRY_RUN", "1") == "1":
        print(f"SIMULATION : {len(added)} ajouts, {len(existing)} fiches historiques conservées, {pending} en attente")
        return 0
    SITE.write_text(rebuilt, encoding="utf-8")
    print(f"Publication : {len(added)} ajouts. {len(existing)} fiches historiques conservées.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
