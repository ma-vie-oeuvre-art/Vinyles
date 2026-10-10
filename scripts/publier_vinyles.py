#!/usr/bin/env python3
"""Synchronisation Airtable -> GitHub Pages, suppressions comprises pour le bac B-08."""
import json
import os
import re
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
    by_key = {(r.get("b"), r.get("o")): i for i, r in enumerate(existing)}
    initial_count = len(existing)
    if len(by_key) != len(existing):
        raise ValueError("Doublons dans le catalogue historique : publication interrompue")
    history = [dict(r) for r in existing]
    rows = fetch_rows(token)
    added, pending, updated = [], 0, 0
    seen = set()
    for r in rows:
        bac, pos = r.get("Bac"), r.get("Ordre dans le bac")
        if not bac or not isinstance(pos, (int, float)) or int(pos) != pos:
            if bac == 'B-08':
                raise ValueError('Position B-08 invalide : synchronisation interrompue')
            continue
        key = (bac, int(pos))
        if key in seen:
            raise ValueError(f"Position Airtable dupliquée : {key}; site inchangé")
        seen.add(key)
        cover = as_url(r.get("Front Cover"))
        if not cover:
            cover = as_url(r.get("Photos personnelles"))
        personal = bool(bac == 'B-08' and (r.get('Front Cover') or r.get('Photos personnelles')) and
                        (r.get('Front Cover') or r.get('Photos personnelles'))[0].get('filename') == f'IMG_{820 + int(pos):04}.jpeg')
        if personal:
            cover = f'photos/B-08/IMG_{820 + int(pos):04}.jpeg'
        # Les URL de pièces jointes Airtable expirent. Les sources Discogs
        # permettent au site de conserver une illustration accessible.
        back = as_url(r.get('Back Cover'))
        sources = r.get('Images Discogs — URLs sources', '')
        if bac == 'B-08' and sources:
            primary = re.search(r'(?:^|\n)primary: (https://\S+)', sources)
            secondary = re.search(r'(?:^|\n)secondary: (https://\S+)', sources)
            if primary and r.get('Front Cover'):
                cover = 'https://images.weserv.nl/?url=' + urllib.parse.quote(primary[1], safe='') + '&output=jpg'
                personal = False
            if secondary and back:
                back = 'https://images.weserv.nl/?url=' + urllib.parse.quote(secondary[1], safe='') + '&output=jpg'
        release = r.get("Discogs release_id")
        # Ne publie pas les fiches provisoires, sans identification ET sans pochette.
        if not (r.get("Titre") and r.get("Artiste")):
            pending += 1
            continue
        verified = bool(release and str(release).isdigit() and r.get("Statut") == "Identifié")
        item = {
            "b": bac, "o": int(pos), "t": r["Titre"], "a": r["Artiste"],
            "y": str((r.get("Année") or "") if verified else r.get("Année de l'album") or ""),
            "f": r.get("Famille") or "À classer", "r": r.get("Réputation sonore") or "", "k": " ".join(
                x for x in [r.get("Label"), r.get("N° catalogue")] if x),
            "d": str(release) if verified else "", "th": cover, "rc": cover,
            "vs": back, "n": r.get("Note Discogs (sur 5)") if verified else None, "v": r.get("Valeur minimale") if verified else None, "m": r.get("Prix suggéré VG+ (Discogs)") if verified else None, "x": r.get("Prix suggéré Mint (Discogs)") if verified else None,
            "h": r.get("Possédé par (Discogs)") if verified else None, "w": r.get("Recherché par (Discogs)") if verified else None, "no": r.get("Nombre de notes (Discogs)") if verified else None, "ev": r.get("Exemplaires en vente (Discogs)") if verified else None,
            "fa": r.get("Familles") or [], "fp": r.get("Format physique") if verified else "", "pa": (r.get("Pays du pressage") or "") if verified else "",
            "dc": (r.get("Décennie du pressage") or "") if verified else "",
            "album": r.get("Discogs référence album") or "", "np": r.get("Nombre de plages"),
        }
        if key in by_key:
            # Ne jamais modifier les 252 fiches historiques. Seuls les B-08
            # provisoires peuvent recevoir une pochette ultérieurement vérifiée.
            pos = by_key[key]
            old = existing[pos]
            if bac == 'B-08':
                metadata = {name: item[name] for name in ('t', 'a', 'k', 'vs', 'y', 'f', 'r', 'd', 'n', 'v', 'm', 'x', 'h', 'w', 'no', 'ev', 'fa', 'fp', 'pa', 'dc', 'album', 'np')}
                if r.get('Famille'):
                    metadata['f'] = r['Famille']
                metadata.update({'s': r.get('Statut', ''), 'diag': r.get('Enrichissement automatique') or r.get('Diagnostic', ''), 'photo': personal})
                if cover:
                    metadata.update({'th': cover, 'rc': cover})
                fresh = {**old, **metadata}
                if fresh != old:
                    existing[pos] = fresh
                    updated += 1
                continue
            continue
        item.update({"s": r.get("Statut", ""), "diag": r.get("Diagnostic", ""), "photo": personal})
        added.append(item)
        by_key[key] = len(existing) + len(added) - 1
    # La lecture paginée complète doit réussir avant toute suppression.
    # B-08 est le bac synchronisé ; les autres bacs historiques restent conservés.
    removed = [r for r in existing if r.get('b') == 'B-08' and (r.get('b'), r.get('o')) not in seen]
    if not added and not updated and not removed:
        print(f"Aucune nouvelle fiche prête. {pending} fiche(s) en attente. Site inchangé.")
        return 0
    output = [r for r in existing if r.get("b") != "B-08" or (r.get("b"), r.get("o")) in seen] + sorted(added, key=lambda r: (r["b"], r["o"]))
    assert len(output) == initial_count + len(added) - len(removed), "Perte ou ajout inattendu"
    assert [r for r in output if r.get('b') != 'B-08'] == [r for r in history if r.get('b') != 'B-08'] + [r for r in sorted(added, key=lambda r: (r['b'], r['o'])) if r.get('b') != 'B-08'], "Fiches historiques modifiées"
    rebuilt = original[:start] + json.dumps(output, ensure_ascii=False, separators=(",", ":")) + original[end:]
    if os.getenv("DRY_RUN", "1") == "1":
        print(f"SIMULATION : {len(added)} ajouts, {updated} mises à jour, {len(removed)} suppressions B-08, {pending} en attente")
        return 0
    SITE.write_text(rebuilt, encoding="utf-8")
    print(f"Publication : {len(added)} ajouts, {updated} mises à jour B-08, {len(removed)} suppressions B-08. Les autres bacs conservés.")
    return 0

if __name__ == "__main__":
    sys.exit(main())


