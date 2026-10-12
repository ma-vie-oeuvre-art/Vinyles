#!/usr/bin/env python3
"""Valeurs v1 — rafraîchir les chiffres de marché Discogs par rotation, avec historique daté.

Décidé le 12 oct. 2026 (avis croisés Claude / ChatGPT) :
- Rotation : LOT disques par passage, les relevés les plus anciens d'abord, pour que
  toute la collection soit revue chaque semaine. L'âge réel des relevés est mesuré à
  chaque passage (state/rotation_valeurs.json) : c'est lui qui prouve la cadence.
- File prioritaire : un disque « en mouvement » est relevé chaque jour (demande en
  hausse ET offres en baisse, ou prix suggéré VG+ qui bouge de 10 % ou plus).
- Seuls les CHIFFRES DE MARCHÉ sont écrits, pour tous les bacs, 01 à 07 compris.
  Titre, artiste, label, statut, images : jamais touchés ici.
- Une donnée absente ne devient jamais zéro. Erreur d'API : champ inchangé, relevé
  noté « null ». Aucune offre en vente (Discogs le dit) : offre minimale vidée.
- Historique : une ligne JSON par relevé dans historique/valeurs/AAAA-MM.jsonl
  (hors Airtable, qui plafonne à 50 000 lignes). Le commit est fait par le workflow.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = "apporVr2Na6l5HGh2"
TABLE = "tblcqsKKwNW28bMSE"
ROOT = Path(__file__).resolve().parents[1]
HISTO = ROOT / "historique" / "valeurs"
ETAT = ROOT / "state" / "rotation_valeurs.json"
TZ = ZoneInfo("America/Toronto")
LOT = int(os.getenv("VALEURS_LOT", "15"))
LOT_PRIORITAIRE = int(os.getenv("VALEURS_LOT_PRIORITAIRE", "10"))
SEUIL_PRIX = 0.10

F = {
    "bac": "Bac", "ordre": "Ordre dans le bac", "statut": "Statut",
    "rid": "Discogs release_id", "ref": "Discogs référence release_id",
    "date": "Date de l'estimation", "gel": "Ne pas retraiter", "preuve": "Preuve d'identification",
    "vmin": "Valeur minimale", "vg": "Prix suggéré VG+ (Discogs)", "mint": "Prix suggéré Mint (Discogs)",
    "have": "Possédé par (Discogs)", "want": "Recherché par (Discogs)", "vente": "Exemplaires en vente (Discogs)",
    "note": "Note Discogs (sur 5)", "nbnotes": "Nombre de notes (Discogs)",
}
_dernier_appel = 0.0
_fx = {}


def http_json(url, headers, data=None, method=None):
    """Appel NON mis en cache : un relevé de marché doit être frais."""
    global _dernier_appel
    for essai in range(4):
        if url.startswith("https://api.discogs.com/"):
            time.sleep(max(0, 1.2 - (time.monotonic() - _dernier_appel)))
            _dernier_appel = time.monotonic()
        corps = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(url, data=corps, headers=headers,
                                     method=method or ("PATCH" if corps else "GET"))
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or essai == 3:
                raise
            time.sleep(min(60, int(exc.headers.get("Retry-After", "0") or 0) or 2 ** (essai + 2)))
        except (urllib.error.URLError, TimeoutError):
            if essai == 3:
                raise
            time.sleep(2 ** (essai + 1))


def en_cad(prix):
    if not isinstance(prix, dict) or prix.get("value") is None:
        return None
    devise, valeur = prix.get("currency"), float(prix["value"])
    if devise == "CAD":
        return round(valeur, 2)
    if not devise:
        return None
    serie = "FX" + devise + "CAD"
    if serie not in _fx:
        obs = http_json(f"https://www.bankofcanada.ca/valet/observations/{serie}/json?recent=1", {}).get("observations") or []
        if not obs or not obs[-1].get(serie, {}).get("v"):
            return None
        _fx[serie] = float(obs[-1][serie]["v"])
    return round(valeur * _fx[serie], 2)


def release_de(f):
    rid = str(f.get(F["rid"]) or "").strip()
    if rid.isdigit() and f.get(F["statut"]) == "Identifié":
        return rid
    ref = str(f.get(F["ref"]) or "").strip()
    if ref.isdigit():
        return ref
    return rid if rid.isdigit() else None


def lire_historique():
    """{record_id: [relevés triés par date]}"""
    h = {}
    if HISTO.is_dir():
        for fichier in sorted(HISTO.glob("*.jsonl")):
            for ligne in fichier.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(ligne)
                except ValueError:
                    continue
                h.setdefault(r.get("id"), []).append(r)
    for v in h.values():
        v.sort(key=lambda r: r.get("t", ""))
    return h


def en_mouvement(releves):
    """Comparer les deux derniers relevés complets."""
    if len(releves) < 2:
        return False
    a, b = releves[-2], releves[-1]
    if a.get("rid") != b.get("rid"):
        return False
    demande = a.get("want") is not None and b.get("want") is not None and b["want"] > a["want"]
    offres = a.get("vente") is not None and b.get("vente") is not None and b["vente"] < a["vente"]
    prix = bool(a.get("vg") and b.get("vg")) and abs(b["vg"] - a["vg"]) / a["vg"] >= SEUIL_PRIX
    return (demande and offres) or prix


def releve(rid, discogs):
    """Retourne (relevé, champs Airtable). Une valeur absente reste None."""
    rel = {"rid": rid, "vg": None, "mint": None, "vmin": None, "vente": None,
           "have": None, "want": None, "note": None, "nbnotes": None, "erreurs": []}
    champs = {}
    try:
        r = http_json(f"https://api.discogs.com/releases/{rid}", discogs)
        c = r.get("community") or {}
        rel["have"], rel["want"] = c.get("have"), c.get("want")
        rel["note"] = (c.get("rating") or {}).get("average")
        rel["nbnotes"] = (c.get("rating") or {}).get("count")
    except urllib.error.HTTPError as exc:
        rel["erreurs"].append(f"release HTTP {exc.code}")
    try:
        s = http_json(f"https://api.discogs.com/marketplace/stats/{rid}?curr_abbr=CAD", discogs)
        rel["vente"] = s.get("num_for_sale")
        rel["vmin"] = en_cad(s.get("lowest_price"))
    except urllib.error.HTTPError as exc:
        rel["erreurs"].append(f"stats HTTP {exc.code}")
    try:
        p = http_json(f"https://api.discogs.com/marketplace/price_suggestions/{rid}", discogs)
        rel["vg"] = en_cad(p.get("Very Good Plus (VG+)"))
        rel["mint"] = en_cad(p.get("Mint (M)"))
    except urllib.error.HTTPError as exc:
        rel["erreurs"].append(f"suggestions HTTP {exc.code}")
    for cle in ("vg", "mint", "have", "want", "vente", "note", "nbnotes"):
        if rel[cle] is not None:
            champs[F[cle]] = rel[cle]
    if rel["vmin"] is not None:
        champs[F["vmin"]] = rel["vmin"]
    elif rel["vente"] == 0:
        champs[F["vmin"]] = None   # Discogs confirme : aucune offre. Vidé, jamais zéro.
    if any(rel[k] is not None for k in ("vg", "mint", "vmin", "vente", "want")):
        champs[F["date"]] = datetime.now(TZ).date().isoformat()
    return rel, champs


def lire_airtable(token):
    lignes, offset = [], None
    while True:
        q = [("pageSize", "100")] + [("fields[]", v) for v in F.values()]
        if offset:
            q.append(("offset", offset))
        page = http_json(f"https://api.airtable.com/v0/{BASE}/{TABLE}?" + urllib.parse.urlencode(q),
                         {"Authorization": f"Bearer {token}"})
        lignes += page.get("records", [])
        offset = page.get("offset")
        if not offset:
            return lignes
        time.sleep(0.25)


def age_jours(f, aujourd_hui):
    d = f.get(F["date"])
    try:
        return (aujourd_hui - date.fromisoformat(d)).days
    except (TypeError, ValueError):
        return 10_000


def main():
    token, dtoken = os.getenv("AIRTABLE_TOKEN"), os.getenv("DISCOGS_TOKEN")
    if not token or not dtoken:
        print("Jetons absents : rafraîchissement des valeurs reporté.", file=sys.stderr)
        return 1
    simulation = os.getenv("DRY_RUN", "0") == "1"
    discogs = {"User-Agent": "VinylesLuc/1.0", "Authorization": f"Discogs token={dtoken}"}
    airtable = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    aujourd_hui = datetime.now(TZ).date()

    # Une ligne gelée, ou décrochée par validation humaine (règle 18), ne reçoit aucun chiffre de marché.
    def suivie(f):
        if f.get(F["gel"]) or not release_de(f):
            return False
        return not (f.get(F["preuve"]) == "Validation humaine" and f.get(F["statut"]) != "Identifié")
    lignes = [l for l in lire_airtable(token) if suivie(l["fields"])]
    historique = lire_historique()

    prioritaires = [l for l in lignes if en_mouvement(historique.get(l["id"], [])) and age_jours(l["fields"], aujourd_hui) >= 1]
    prioritaires.sort(key=lambda l: -age_jours(l["fields"], aujourd_hui))
    choisis = prioritaires[:LOT_PRIORITAIRE]
    deja = {l["id"] for l in choisis}
    rotation = sorted((l for l in lignes if l["id"] not in deja and age_jours(l["fields"], aujourd_hui) >= 1),
                      key=lambda l: (-age_jours(l["fields"], aujourd_hui), l["fields"].get(F["bac"], ""), l["fields"].get(F["ordre"], 0)))
    choisis += rotation[:LOT]

    nouveaux, ecrits, erreurs = [], 0, 0
    for l in choisis:
        rid = release_de(l["fields"])
        try:
            rel, champs = releve(rid, discogs)
        except Exception as exc:  # réseau : on passe, le disque reviendra au prochain passage
            erreurs += 1
            print(f"{l['id']} : échec {type(exc).__name__}: {exc}")
            continue
        rel.update({"t": datetime.now(TZ).isoformat(timespec="seconds"), "id": l["id"],
                    "b": l["fields"].get(F["bac"]), "o": l["fields"].get(F["ordre"]), "devise": "CAD"})
        nouveaux.append(rel)
        if champs and not simulation:
            http_json(f"https://api.airtable.com/v0/{BASE}/{TABLE}/{l['id']}", airtable, {"fields": champs})
            ecrits += 1
            time.sleep(0.22)

    if nouveaux and not simulation:
        HISTO.mkdir(parents=True, exist_ok=True)
        with (HISTO / f"{aujourd_hui:%Y-%m}.jsonl").open("a", encoding="utf-8") as sortie:
            for rel in nouveaux:
                sortie.write(json.dumps(rel, ensure_ascii=False, sort_keys=True) + "\n")

    # Âge réel des relevés : la preuve que la rotation reste hebdomadaire.
    ages = sorted(age_jours(l["fields"], aujourd_hui) for l in lignes if l["id"] not in {r["id"] for r in nouveaux})
    ages += [0] * len(nouveaux)
    ages.sort()
    jamais = sum(1 for a in ages if a >= 10_000)
    dates = [a for a in ages if a < 10_000]
    etat = {
        "passage": datetime.now(TZ).isoformat(timespec="seconds"),
        "disques_suivis": len(lignes), "jamais_releves": jamais,
        "age_median_jours": dates[len(dates) // 2] if dates else None,
        "age_max_jours": dates[-1] if dates else None,
        "plus_de_7_jours": sum(1 for a in ages if a > 7),
        "prioritaires_en_attente": len(prioritaires), "releves_ce_passage": len(nouveaux), "erreurs": erreurs,
    }
    if not simulation:
        ETAT.parent.mkdir(exist_ok=True)
        ETAT.write_text(json.dumps(etat, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    resume = (f"Valeurs : {len(nouveaux)} relevés ({min(len(prioritaires), LOT_PRIORITAIRE)} prioritaires), {ecrits} fiches écrites, "
              f"{erreurs} échecs. Âge médian {etat['age_median_jours']} j, max {etat['age_max_jours']} j, "
              f"{etat['plus_de_7_jours']} de plus de 7 j, {jamais} jamais relevés.")
    print(resume)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as s:
            s.write("\n- " + resume + "\n")
    return 1 if erreurs and not nouveaux else 0


if __name__ == "__main__":
    sys.exit(main())
