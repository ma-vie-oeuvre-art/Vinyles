#!/usr/bin/env python3
"""Traitement différé non destructif des fiches Airtable au statut Dicté."""
import json
import re
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "apporVr2Na6l5HGh2"
TABLE = "tblcqsKKwNW28bMSE"
API = "https://api.airtable.com/v0"
FIELDS = {
    "cle": "Clé",
    "statut": "Statut",
    "artiste": "Artiste",
    "titre": "Titre",
    "label": "Label",
    "numero": "N° catalogue",
    "candidats": "Candidats Discogs",
    "diagnostic": "Diagnostic",
    "ne_pas_retraiter": "Ne pas retraiter",
}
LIMIT = max(1, min(100, int(os.getenv("BATCH_SIZE", "20"))))
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"


def request_json(url, headers, data=None, method=None):
    from cache_recherches import cached_search, save_search
    is_read = data is None and method in (None, 'GET') and url.startswith('https://api.discogs.com/')
    if is_read:
        cached = cached_search(url)
        if cached is not None: return cached
    payload = None if data is None else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers=headers,
                                 method=method or ("PATCH" if payload is not None else "GET"))
    with urllib.request.urlopen(req, timeout=25) as response:
        result = json.load(response)
    if is_read: save_search(url, result)
    return result


def eligible(fields):
    return (not fields.get(FIELDS["candidats"])
            and not fields.get(FIELDS["ne_pas_retraiter"])
            and not fields.get("Discogs release_id")
            and (fields.get(FIELDS["numero"])
                 or (fields.get(FIELDS["artiste"]) and fields.get(FIELDS["titre"]))
                 or "[OCR pochette]" in fields.get("Transcription source", "")))


def main():
    token = os.environ["AIRTABLE_TOKEN"]
    discogs_token = os.environ.get("DISCOGS_TOKEN", "")
    if not discogs_token:
        print("DISCOGS_TOKEN absent : arrêt sans modifier Airtable", file=sys.stderr)
        raise SystemExit(1)
    airtable_headers = {"Authorization": f"Bearer {token}",
                       "Content-Type": "application/json"}
    discogs_headers = {"User-Agent": "VinylesInventaire/1.0 (personal catalog)",
                       "Accept": "application/json"}
    if discogs_token:
        discogs_headers["Authorization"] = f"Discogs token={discogs_token}"
    cursor = None
    selected = []
    # Airtable filter is only on Dicté; existing identified records are untouched.
    while len(selected) < LIMIT:
        params = {"pageSize": 100, "filterByFormula": "{Statut}='Dicté'"}
        if cursor:
            params["offset"] = cursor
        url = f"{API}/{BASE}/{TABLE}?" + urllib.parse.urlencode(params)
        page = request_json(url, airtable_headers)
        for row in page.get("records", []):
            fields = row.get("fields", {})
            # Do not reprocess a row where candidates already exist.
            if eligible(fields):
                selected.append(row)
                if len(selected) >= LIMIT:
                    break
        cursor = page.get("offset")
        if not cursor:
            break
    print(f"Fiches en attente à examiner: {len(selected)}")
    errors = 0
    for row in selected:
        f = row["fields"]
        catno = str(f.get(FIELDS["numero"], "")).strip()
        label = str(f.get(FIELDS["label"], "")).strip()
        artist = str(f.get(FIELDS["artiste"], "")).strip()
        title = str(f.get(FIELDS["titre"], "")).strip()
        key = str(f.get(FIELDS["cle"], "")).strip()
        ocr = f.get("Transcription source", "").split("[OCR pochette]")[-1].strip() if "[OCR pochette]" in f.get("Transcription source", "") else ""
        if not (catno or (artist and title) or key or ocr):
            print(f"{row['id']}: données insuffisantes; inchangé")
            continue
        params = {"type": "release", "format": "Vinyl", "per_page": 50}
        if catno and re.search(r"[A-Za-z]", catno):
            params["catno"] = catno
            if label:
                params["label"] = label
        elif artist and title:
            params.update({"artist": artist, "release_title": title})
        else:
            # Un numero purement numerique ne suffit jamais pour identifier un pressage.
            if catno and catno.isdigit():
                print(f"{row['id']}: numero numerique sans artiste/titre; validation requise")
                continue
            params["q"] = key or ocr.split(" | ")[0][:100]
        url = "https://api.discogs.com/database/search?" + urllib.parse.urlencode(params)
        try:
            response = request_json(url, discogs_headers)
            results = response.get("results", [])
            # Recherche par numero: eliminer les correspondances de simple prefixe.
            if "catno" in params:
                norm = lambda x: re.sub(r"[^A-Z0-9]", "", str(x).upper())
                results = [x for x in results if norm(x.get("catno", "")) == norm(catno)]
            # Candidates are suggestions, not verified pressage identifications.
            candidates = [{"release_id": x.get("id"), "titre": x.get("title"),
                           "label": x.get("label"), "catno": x.get("catno"),
                           "pays": x.get("country"), "annee": x.get("year")}
                          for x in results]
            diagnosis = f"Recherche différée : {len(candidates)} candidat(s) Discogs; validation humaine requise."
            if not DRY_RUN:
                endpoint = f"{API}/{BASE}/{TABLE}/{row['id']}"
                request_json(endpoint, airtable_headers,
                             {"fields": {FIELDS["candidats"]: json.dumps(candidates, ensure_ascii=False),
                                         FIELDS["diagnostic"]: diagnosis}})
            print(f"{row['id']}: {len(candidates)} candidat(s)" + (" (simulation)" if DRY_RUN else ""))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            errors += 1
            print(f"{row['id']}: accès Discogs/Airtable indisponible: {exc}", file=sys.stderr)
        time.sleep(2.2)  # stay conservatively under Discogs rate limits

    if errors:
        raise SystemExit(f"{errors} recherche(s) en échec technique")

if __name__ == "__main__":
    main()

