#!/usr/bin/env python3
"""Ajouter des pochettes Discogs identifiées de manière conservatrice, tous bacs.

Chaque exemplaire reste distinct. Ne déduit JAMAIS un release_id d'une liste
de candidats : les pressages ambigus restent sans pochette.
"""
import json
import os
import re
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


def unique_candidate(f):
    """Choose only one exact catalogue AND album-title match; never guess a pressing."""
    cat = f.get("N° catalogue", "")
    title = f.get("Titre", "")
    artist = f.get("Artiste", "")
    if not cat or not title:
        return None
    from qualifier_vinyles import concordant
    try:
        candidates = json.loads(f.get("Candidats Discogs") or "[]")
    except (ValueError, TypeError):
        return None
    if not isinstance(candidates, list):
        return None
    matches = [x for x in candidates if concordant(f, x)]
    return matches[0] if len(matches) == 1 else None


def main():
    at = {"Authorization": f"Bearer {os.environ['AIRTABLE_TOKEN']}"}
    discogs = {"User-Agent": "VinylesInventaire/1.0 (personal catalog)"}
    if os.getenv("DISCOGS_TOKEN"):
        discogs["Authorization"] = f"Discogs token={os.environ['DISCOGS_TOKEN']}"
    cursor = None
    scanned = 0
    updated = 0
    ambiguous = 0
    errors = 0
    while True:
        params = {"pageSize": 100, "filterByFormula": "AND(OR(NOT({Front Cover}),NOT({Back Cover})),NOT({Images verrouillées}))"}
        if cursor:
            params["offset"] = cursor
        page = get(API + "?" + urllib.parse.urlencode(params), at)
        for row in page.get("records", []):
            if updated >= BATCH:
                break
            scanned += 1
            f = row.get("fields", {})
            if (f.get("Front Cover") and f.get("Back Cover")) or f.get("Images verrouillées") or f.get("Ne pas retraiter"):
                continue
            release_id = str(f.get("Discogs release_id", "")).strip()
            candidate = None
            if not release_id.isdecimal():
                candidate = unique_candidate(f)
                if candidate:
                    release_id = str(candidate["release_id"])
            if not release_id.isdecimal():
                ambiguous += 1
                print(f"{f.get('Bac')}/{f.get('Ordre dans le bac')}: pressage non confirmé; aucune image attribuée")
                continue
            try:
                release = get(f"https://api.discogs.com/releases/{release_id}", discogs)
                if str(release.get("id")) != release_id:
                    raise ValueError("Discogs release_id incohérent")
                if candidate:
                    norm = lambda s: re.sub(r"[^A-Z0-9]", "", str(s).upper())
                    if not any(norm(lbl.get("catno")) == norm(f.get("N° catalogue"))
                               for lbl in release.get("labels", [])):
                        raise ValueError("Numéro catalogue non confirmé dans la fiche Discogs")
                    if not any(fmt.get("name", "").lower() == "vinyl" for fmt in release.get("formats", [])):
                        raise ValueError("Le support Discogs n'est pas un vinyle")
                images = release.get("images") or []
                image = next((i.get("uri") for i in images if i.get("type") == "primary" and i.get("uri")), None)
                if not image:
                    image = next((i.get("uri") for i in images if i.get("uri")), None)
                if not image or not image.startswith("https://"):
                    print(f"{release_id}: aucune pochette exploitable")
                    continue
                # Charger le recto et, si disponible, une premiere image secondaire (verso presume).
                secondary = next((i.get("uri") for i in images if i.get("type") == "secondary" and i.get("uri") and i.get("uri") != image), None)
                # Airtable importe le fichier distant dans son champ attachment.
                if os.getenv("DRY_RUN", "1") == "1":
                    print(f"SIMULATION: {release_id} -> {image}")
                else:
                    proxy = "https://images.weserv.nl/?url=" + urllib.parse.quote(image, safe="") + "&output=jpg"
                    fields = {}
                    if not f.get("Front Cover"):
                        fields["Front Cover"] = [{"url": proxy}]
                    if secondary and not f.get("Back Cover"):
                        fields["Back Cover"] = [{"url": "https://images.weserv.nl/?url=" + urllib.parse.quote(secondary, safe="") + "&output=jpg"}]
                    fields["Images Discogs — URLs sources"] = "\n".join(f"{i.get('type', 'secondary')}: {i.get('uri')}" for i in images if i.get("uri"))
                    if candidate:
                        fields["Diagnostic"] = "Pochette Discogs indicative : artiste, titre, format et catalogue concordants; pressage physique non confirmé."
                    patch(f"{API}/{row['id']}", at, fields)
                    fresh = get(f"{API}/{row['id']}", at)
                    for attempt in range(12):
                        required = [key for key in ("Front Cover", "Back Cover") if key in fields]
                        if all(any(a.get("size") for a in fresh.get("fields", {}).get(key, [])) for key in required):
                            break
                        time.sleep(5)
                        fresh = get(f"{API}/{row['id']}", at)
                    if not any(a.get("size") for a in fresh.get("fields", {}).get("Front Cover", [])):
                        raise ValueError("La pièce jointe n'est pas confirmée dans Airtable")
                    if "Back Cover" in fields and not any(a.get("size") for a in fresh.get("fields", {}).get("Back Cover", [])):
                        print(f"{release_id}: verso non confirme; recto confirme")
                    updated += 1
                    print(f"IMAGE CONFIRMÉE: {release_id}")
            except Exception as exc:
                errors += 1
                print(f"{release_id}: échec sans nouvelle attribution: {exc}")
            time.sleep(1.2)
        cursor = page.get("offset")
        if not cursor or updated >= BATCH:
            break
    print(f"Bilan: {scanned} examinés, {updated} images importées, {ambiguous} pressages sans identification confirmée")
    if errors:
        raise SystemExit(f"ÉCHEC TECHNIQUE : {errors} import(s) de pochette en erreur.")
    if not updated:
        print("Aucune pochette importable : identification ou image source manquante; aucune attribution forcée.")


if __name__ == "__main__":
    main()

