#!/usr/bin/env python3
"""Synchronisation Airtable -> GitHub Pages, suppressions comprises pour le bac B-08."""
import json
import os
import re
import sys
import hashlib
from concurrent.futures import ThreadPoolExecutor
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "apporVr2Na6l5HGh2"
TABLE = "tblcqsKKwNW28bMSE"
MARKER = "const D = /*__DONNEES__*/"
ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "index.html"

def durable_image(url, identity):
    """Copy image bytes into the published site, without external dependencies."""
    if not url:
        return None
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme:
        local = (ROOT / urllib.parse.unquote(url)).resolve()
        if ROOT.resolve() in local.parents and local.is_file():
            return local.relative_to(ROOT.resolve()).as_posix()
        raise ValueError('Image locale absente : ' + url)
    if parsed.scheme != 'https':
        raise ValueError('Source image non HTTPS')
    key = hashlib.sha256(identity.encode()).hexdigest()[:32]
    directory = ROOT / 'covers'
    for suffix in ('.jpg', '.png', '.webp'):
        cached = directory / (key + suffix)
        if cached.is_file() and cached.stat().st_size:
            return cached.relative_to(ROOT).as_posix()
    request = urllib.request.Request(url, headers={'User-Agent': 'Vinyles-Catalogue/1.0'})
    with urllib.request.urlopen(request, timeout=25) as response:
        data = response.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024:
        raise ValueError('Image trop volumineuse')
    if data.startswith(b'\xff\xd8\xff'):
        suffix = '.jpg'
    elif data.startswith(b'\x89PNG\r\n\x1a\n'):
        suffix = '.png'
    elif data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        suffix = '.webp'
    else:
        raise ValueError('La source ne contient pas une image valide')
    directory.mkdir(exist_ok=True)
    destination = directory / (key + suffix)
    temporary = destination.with_suffix(suffix + '.tmp')
    temporary.write_bytes(data)
    temporary.replace(destination)
    return destination.relative_to(ROOT).as_posix()

def prepare_images(fields):
    cover, back, personal = site_images(fields)
    for side, url, field in [('front', cover, 'Front Cover'), ('back', back, 'Back Cover')]:
        attachments = fields.get(field) or (fields.get('Photos personnelles') if side == 'front' else []) or []
        # Fresh attachment URLs are used only during download, never in the HTML.
        remote = bool(url and urllib.parse.urlparse(url).scheme)
        attachment = attachments[0] if attachments and remote else {}
        source = attachment.get('url') or url
        identity = attachment.get('id') or source or ''
        stable = durable_image(source, identity) if source else None
        if side == 'front':
            cover = stable
        else:
            back = stable
    return cover, back, personal

def site_images(fields):
    """Publish durable source URLs, never expiring Airtable download links."""
    cover = as_url(fields.get('Front Cover')) or as_url(fields.get('Photos personnelles'))
    personal = False
    paths = re.findall(r'fichier source: ([^\n]+)', fields.get('Transcription source', ''))
    for attachment in fields.get('Photos personnelles') or []:
        filename = attachment.get('filename', '')
        if filename and Path(filename).name == filename:
            paths.append('photos/' + str(fields.get('Bac', '')) + '/' + filename)
    for path in paths:
        local = (ROOT / path.strip()).resolve()
        if ROOT.resolve() in local.parents and local.is_file() and local.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp'}:
            cover = local.relative_to(ROOT.resolve()).as_posix()
            personal = True
            break
    back = as_url(fields.get('Back Cover'))
    sources = fields.get('Images Discogs — URLs sources', '')
    primary = re.search(r'(?:^|\n)primary: (https://\S+)', sources)
    secondary = re.search(r'(?:^|\n)secondary: (https://\S+)', sources)
    if primary and fields.get('Front Cover') and not personal:
        cover = 'https://images.weserv.nl/?url=' + urllib.parse.quote(primary[1], safe='') + '&output=jpg'
    if secondary and back:
        back = 'https://images.weserv.nl/?url=' + urllib.parse.quote(secondary[1], safe='') + '&output=jpg'
    return cover, back, personal

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
    with ThreadPoolExecutor(max_workers=8) as pool:
        prepared = list(pool.map(prepare_images, rows))
    added, pending, updated = [], 0, 0
    seen = set()
    for r, images in zip(rows, prepared):
        bac, pos = r.get("Bac"), r.get("Ordre dans le bac")
        if not bac or not isinstance(pos, (int, float)) or int(pos) != pos:
            if bac == 'B-08':
                raise ValueError('Position B-08 invalide : synchronisation interrompue')
            continue
        key = (bac, int(pos))
        if key in seen:
            raise ValueError(f"Position Airtable dupliquée : {key}; site inchangé")
        seen.add(key)
        cover, back, personal = images
        release = r.get("Discogs release_id")
        # La capture est visible immédiatement, même si l'album reste incertain.
        # These labels exist only in the view; they are not invented metadata.
        if not (r.get("Titre") and r.get("Artiste")):
            pending += 1
            if not r.get('Photos personnelles') or not cover:
                continue
        verified = bool(release and str(release).isdigit() and r.get("Statut") == "Identifié")
        documented = verified or bool(r.get("Discogs référence release_id"))
        item = {
            "b": bac, "o": int(pos), "t": r.get('Titre') or 'Identification en cours', "a": r.get('Artiste') or 'À identifier',
            "y": str((r.get("Année") or "") if verified else r.get("Année de l'album") or ""),
            "f": r.get("Famille") or "À classer", "r": r.get("Réputation sonore") or "", "k": " ".join(
                x for x in [r.get("Label"), r.get("N° catalogue")] if x),
            "d": str(release) if verified else "", "th": cover, "rc": cover,
            "vs": back, "n": r.get("Note Discogs (sur 5)") if documented else None, "v": r.get("Valeur minimale") if documented else None, "m": r.get("Prix suggéré VG+ (Discogs)") if documented else None, "x": r.get("Prix suggéré Mint (Discogs)") if documented else None,
            "h": r.get("Possédé par (Discogs)") if documented else None, "w": r.get("Recherché par (Discogs)") if documented else None, "no": r.get("Nombre de notes (Discogs)") if documented else None, "ev": r.get("Exemplaires en vente (Discogs)") if documented else None,
            "fa": r.get("Familles") or [], "fp": r.get("Format physique") or "", "pa": (r.get("Pays du pressage") or "") if verified else "",
            "dc": (r.get("Décennie du pressage") or "") if verified else "",
            "album": r.get("Discogs référence album") or "", "np": r.get("Nombre de plages"),
            "indicatif": bool(documented and not verified), "sourcePrix": r.get("Source de l'estimation") or "",
            "technique": r.get("Qualité d'enregistrement (source)") or "", "historique": r.get("Informations historiques") or "",
        }
        if key in by_key:
            # Actualiser les fiches enrichies par GitHub sans retirer les entrées historiques.
            pos = by_key[key]
            old = existing[pos]
            # Repair images for every historical row, even without metadata changes.
            repaired = {**old}
            if cover:
                repaired.update(th=cover, rc=cover)
            if back:
                repaired['vs'] = back
            if repaired != old:
                existing[pos] = repaired
                updated += 1
            old = repaired
            if bac == 'B-08' or r.get('Photos personnelles') or r.get('Enrichissement automatique', '').startswith('metadata-v'):
                metadata = {name: item[name] for name in ('t', 'a', 'k', 'vs', 'y', 'f', 'r', 'd', 'n', 'v', 'm', 'x', 'h', 'w', 'no', 'ev', 'fa', 'fp', 'pa', 'dc', 'album', 'np', 'indicatif', 'sourcePrix', 'technique', 'historique')}
                if r.get('Famille'):
                    metadata['f'] = r['Famille']
                metadata.update({'s': r.get('Statut', ''), 'diag': r.get('Enrichissement automatique') or r.get('Diagnostic', ''), 'photo': personal})
                if cover:
                    metadata.update({'th': cover, 'rc': cover})
                if bac != 'B-08':
                    metadata = {k: v for k, v in metadata.items() if v is not None and v != '' and v != []}
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
    assert {(r['b'],r['o']) for r in output if r.get('b') != 'B-08'} >= {(r['b'],r['o']) for r in history if r.get('b') != 'B-08'}, 'Fiches historiques perdues'
    rebuilt = original[:start] + json.dumps(output, ensure_ascii=False, separators=(",", ":")) + original[end:]
    if os.getenv("DRY_RUN", "1") == "1":
        print(f"SIMULATION : {len(added)} ajouts, {updated} mises à jour, {len(removed)} suppressions B-08, {pending} en attente")
        return 0
    SITE.write_text(rebuilt, encoding="utf-8")
    print(f"Publication : {len(added)} ajouts, {updated} mises à jour B-08, {len(removed)} suppressions B-08. Les autres bacs conservés.")
    return 0

if __name__ == "__main__":
    sys.exit(main())



