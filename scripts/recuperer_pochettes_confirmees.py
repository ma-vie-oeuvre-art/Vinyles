#!/usr/bin/env python3
"""Ajouter des pochettes Discogs identifiées de manière conservatrice, tous bacs.

Chaque exemplaire reste distinct. Ne déduit JAMAIS un release_id d'une liste
de candidats. Les albums reconnus peuvent recevoir des pochettes de référence,
explicitement distinctes de la confirmation du pressage physique.
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

def single_source_image(source):
    urls = re.findall(r'^(?:primary|secondary):\s*(https://\S+)', source or '', re.M)
    return bool(urls) and len(set(urls)) == 1


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


def album_reference(f, headers):
    """Retrouver automatiquement un album, sans attribuer son pressage à l'objet."""
    from qualifier_vinyles import concordant, norm
    title = f.get("Titre", "").strip()
    artist = f.get("Artiste", "").split(" / ")[0].strip()
    if not title:
        return None
    params = {"type": "master", "release_title": title, "per_page": 50}
    if norm(artist) not in ("ARTISTESVARIES", "VARIOUS", "AUDIOFIDELITY", ""):
        params["artist"] = artist
    data = get("https://api.discogs.com/database/search?" + urllib.parse.urlencode(params), headers)
    album_fields = {k: v for k, v in f.items() if k != "N° catalogue"}
    matches = {}
    for item in data.get("results", []):
        if item.get("type") != "master":
            continue
        if concordant(album_fields, {"release_id": item.get("id"), "titre": item.get("title")}):
            matches[item["id"]] = item
    if len(matches) != 1:
        return None
    master_id = next(iter(matches))
    master = get(f"https://api.discogs.com/masters/{master_id}", headers)
    main_id = master.get("main_release")
    from reconnaitre_albums import rejected_references
    if str(main_id) in rejected_references(f.get('Transcription source', '')):
        return None
    if not main_id or str(master.get("id")) != str(master_id):
        return None
    release = get(f"https://api.discogs.com/releases/{main_id}", headers)
    if not any(fmt.get("name", "").lower() == "vinyl" for fmt in release.get("formats", [])):
        return None
    return master_id, release


def candidate_reference(f, headers, require_images=True):
    """Pochettes illustratives d'une version concordante, sans identifier l'exemplaire."""
    from qualifier_vinyles import concordant, norm
    try:
        candidates = json.loads(f.get("Candidats Discogs") or "[]")
    except (TypeError, ValueError):
        return None
    if not isinstance(candidates, list):
        return None
    album_fields = {k: v for k, v in f.items() if k != "N° catalogue"}
    from reconnaitre_albums import rejected_references
    rejected = rejected_references(f.get('Transcription source', ''))
    matches = [x for x in candidates if str(x.get('release_id')) not in rejected and concordant(album_fields, x)]
    matches.sort(key=lambda x: (norm(x.get("catno")) != norm(f.get("N° catalogue")), int(x["release_id"])))
    for item in matches[:5]:
        release = get(f"https://api.discogs.com/releases/{item['release_id']}", headers)
        if str(release.get("id")) != str(item["release_id"]):
            raise ValueError("Référence Discogs incohérente")
        if any(fmt.get("name", "").lower() == "vinyl" for fmt in release.get("formats", [])) and (release.get("images") or not require_images):
            return f"r{release['id']}", release
        time.sleep(1.2)
    return None


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
    selected = []
    while True:
        params = {"pageSize": 100, "filterByFormula": "AND(OR(NOT({Front Cover}),NOT({Back Cover})),NOT({Images verrouillées}))"}
        if cursor:
            params["offset"] = cursor
        page = get(API + "?" + urllib.parse.urlencode(params), at)
        selected.extend(page.get('records', []))
        cursor = page.get('offset')
        if not cursor:
            break
    for row in selected:
        if updated >= BATCH:
            break
        scanned += 1
        f = row.get("fields", {})
        if (f.get("Front Cover") and f.get("Back Cover")) or f.get("Images verrouillées") or f.get("Ne pas retraiter"):
            continue
        if f.get('Front Cover') and f.get('Images Discogs — URLs sources') and 'secondary:' not in f['Images Discogs — URLs sources']:
            continue
        if f.get('Front Cover') and single_source_image(f.get('Images Discogs — URLs sources')):
            continue  # Discogs can call its only image "secondary"; no additional cover exists.
        release_id = str(f.get("Discogs release_id", "")).strip()
        persisted_reference = False
        reference_id = str(f.get('Discogs référence release_id', '')).strip()
        if not release_id.isdecimal() and reference_id.isdecimal() and f.get('Artiste') and f.get('Titre'):
            release_id = reference_id
            persisted_reference = True
        candidate = None
        if not release_id.isdecimal():
            candidate = unique_candidate(f)
            if candidate:
                release_id = str(candidate["release_id"])
        try:
            master_id = ('r' + release_id) if persisted_reference else None
            if release_id.isdecimal():
                release = get(f"https://api.discogs.com/releases/{release_id}", discogs)
            elif f.get('Titre') and f.get('Artiste'):
                reference = album_reference(f, discogs) or candidate_reference(f, discogs)
                if not reference:
                    ambiguous += 1
                    print(f"{f.get('Bac')}/{f.get('Ordre dans le bac')}: album sans référence unique; images en attente")
                    continue
                master_id, release = reference
                release_id = str(release["id"])
            else:
                ambiguous += 1
                continue
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
                fields["Images Discogs — URLs sources"] = f"Version illustrée : https://www.discogs.com/release/{release_id}\n" + "\n".join(f"{i.get('type', 'secondary')}: {i.get('uri')}" for i in images if i.get("uri"))
                if master_id:
                    reference_url = f"https://www.discogs.com/release/{release_id}" if str(master_id).startswith("r") else f"https://www.discogs.com/master/{master_id}"
                    fields["Images Discogs — URLs sources"] = f"Album de référence : {reference_url}; images de la version {release_id}, pressage physique non confirmé.\n" + fields["Images Discogs — URLs sources"]
                    fields["Diagnostic"] = f"Pochettes de référence : {reference_url}, version illustrée r{release_id}. Le pressage de cet exemplaire reste à confirmer; aucune valeur attribuée."
                elif candidate:
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
    print(f"Bilan: {scanned} examinés, {updated} images importées, {ambiguous} pressages sans identification confirmée")
    if errors:
        raise SystemExit(f"ÉCHEC TECHNIQUE : {errors} import(s) de pochette en erreur.")
    if not updated:
        print("Aucune pochette importable : identification ou image source manquante; aucune attribution forcée.")


if __name__ == "__main__":
    main()



