#!/usr/bin/env python3
"""Ajouter seulement des pochettes Discogs dont le pressage est déjà confirmé.

Chaque exemplaire reste distinct. Ne déduit JAMAIS un release_id d'une liste
de candidats : les pressages ambigus restent sans pochette.
"""
import json
import os
import time
import urllib.parse
import urllib.request

BASE = "apporVr2Na6l5HGh2"
TABLE = "tblcqsKKwNW28bMSE"
API = f"https://api.airtable.com/v0/{BASE}/{TABLE}"
BATCH = max(1, min(100, int(os.getenv("COVER_BATCH_SIZE", "50"))))


def get(url, headers):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.load(res)


def patch(url, headers, fields):
    req = urllib.request.Request(url, data=json.dumps({"fields": fields}).encode(),
                                 headers={**headers, "Content-Type": "application/json"},
                                 method="PATCH")
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.load(res)


def main():
    at = {"Authorization": f"Bearer {os.environ['AIRTABLE_TOKEN']}"}
    discogs = {"User-Agent": "VinylesInventaire/1.0 (personal catalog)"}
    if os.getenv("DISCOGS_TOKEN"):
        discogs["Authorization"] = f"Discogs token={os.environ['DISCOGS_TOKEN']}"
    cursor = None
    scanned = 0
    updated = 0
    ambiguous = 0
    while scanned < BATCH:
        params = {"pageSize": min(100, BATCH - scanned), "filterByFormula": "{Bac}='B-08'"}
        if cursor:
            params["offset"] = cursor
        page = get(API + "?" + urllib.parse.urlencode(params), at)
        for row in page.get("records", []):
            scanned += 1
            f = row.get("fields", {})
            if f.get("Bac") != "B-08" or f.get("Front Cover") or f.get("Images verrouillées"):
                continue
            release_id = str(f.get("Discogs release_id", "")).strip()
            if not release_id.isdecimal():
                ambiguous += 1
                print(f"{f.get('Bac')}/{f.get('Ordre dans le bac')}: pressage non confirmé; aucune image attribuée")
                continue
            try:
                release = get(f"https://api.discogs.com/releases/{release_id}", discogs)
                if str(release.get("id")) != release_id:
                    raise ValueError("Discogs release_id incohérent")
                images = release.get("images") or []
                image = next((i.get("uri") for i in images if i.get("type") == "primary" and i.get("uri")), None)
                if not image:
                    image = next((i.get("uri") for i in images if i.get("uri")), None)
                if not image or not image.startswith("https://"):
                    print(f"{release_id}: aucune pochette exploitable")
                    continue
                # Airtable importe le fichier distant dans son champ attachment.
                if os.getenv("DRY_RUN", "1") == "1":
                    print(f"SIMULATION: {release_id} -> {image}")
                else:
                    patch(f"{API}/{row['id']}", at, {"Front Cover": [{"url": image}]})
                    fresh = get(f"{API}/{row['id']}", at)
                    if not fresh.get("fields", {}).get("Front Cover"):
                        raise ValueError("La pièce jointe n'est pas confirmée dans Airtable")
                    updated += 1
                    print(f"IMAGE CONFIRMÉE: {release_id}")
            except Exception as exc:
                print(f"{release_id}: échec sans nouvelle attribution: {exc}")
            time.sleep(1.2)
        cursor = page.get("offset")
        if not cursor:
            break
    print(f"Bilan: {scanned} examinés, {updated} images importées, {ambiguous} pressages sans identification confirmée")


if __name__ == "__main__":
    main()
