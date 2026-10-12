#!/usr/bin/env python3
"""Prises de son v1 — lier chaque disque aux personnes de sa chaîne sonore.

Source : le champ « Qualité d'enregistrement (source) » des Exemplaires,
écrit par completer_metadonnees.py à partir des crédits Discogs
(lignes « Rôle : Nom »). Destination : la table « Prises de son ».

Règles :
- Seuls les rôles qui touchent au son sont repris (prise de son, mixage,
  mastering, gravure). Un producteur seul n'y entre pas.
- Le nom est normalisé comme sur le site : sans astérisque Discogs ni
  numéro d'homonymie « (2) ». C'est la clé d'appariement.
- Le script ne retire jamais un lien posé à la main : il ajoute.
- La réputation n'est posée que pour une ligne neuve, et seulement si le
  nom figure dans REPUTATIONS avec sa source. Une réputation déjà saisie
  dans Airtable n'est jamais écrasée.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

BASE = "apporVr2Na6l5HGh2"
EXEMPLAIRES = "tblcqsKKwNW28bMSE"
PRISES = "tblxfIqgyn9LcUnPT"
F_TECH = "fldNT8Gvis6vBVvbA"        # Qualité d'enregistrement (source)
F_LIEN_EX = "fldO9zlNR1CRjM719"     # Exemplaires -> Prises de son
F_NOM = "fldv1xgRSwXAplF3E"
F_ETAPES = "fldkFDNtKBBFQZ6Mb"
F_REPUT = "fld2QkvNZE2wQ1NIz"
F_SOURCE = "fldwZJ9RbsA4fGG8c"
F_ROLES = "fldx5iNMUGs09cUVA"
F_LIEN_PS = "fld9oNu4fnw9xeJ5v"     # Prises de son -> Exemplaires

SON = re.compile(r"engineer|mastered|recorded|mixed|mixage|lacquer|cut by|remaster|transfer", re.I)

# Réputations documentées dans le métier (distinctions, maisons de référence).
# Jamais une écoute. Ajouter un nom ici exige sa source.
REPUTATIONS = {
    "Rudy Van Gelder": ("Référence historique", "Ingénieur de Blue Note, Prestige et Impulse!; son studio d'Englewood Cliffs définit le son jazz des années 1950-60."),
    "Bob Ludwig": ("Référence historique", "Ingénieur de mastering (Masterdisk, Gateway Mastering); nombreux Grammy et TEC Awards."),
    "C. Robert Fine": ("Référence historique", "Concepteur de la technique Mercury Living Presence à trois micros."),
    "Kenneth Wilkinson": ("Référence historique", "Ingénieur Decca, Kingsway Hall; prises de son classiques de référence."),
    "Lewis Layton": ("Référence historique", "Ingénieur RCA Living Stereo (Chicago Symphony, Boston Symphony)."),
    "Roy DuNann": ("Référence historique", "Ingénieur de Contemporary Records; prises jazz de la côte Ouest réputées."),
    "Al Schmitt": ("Référence historique", "Ingénieur de prise de son et de mixage; plus de vingt Grammy."),
    "Bruce Swedien": ("Référence historique", "Ingénieur de prise de son et de mixage (Quincy Jones, Michael Jackson); Grammy multiples."),
    "Geoff Emerick": ("Référence historique", "Ingénieur EMI Abbey Road (Beatles); Grammy multiples."),
    "Alan Parsons": ("Référence historique", "Ingénieur Abbey Road (The Dark Side of the Moon)."),
    "Doug Sax": ("Référence historique", "Fondateur de The Mastering Lab; pionnier de la gravure directe (Sheffield Lab)."),
    "Bernie Grundman": ("Référence historique", "Ingénieur de mastering (A&M, Bernie Grundman Mastering)."),
    "George Marino": ("Reconnu", "Ingénieur de mastering, Sterling Sound."),
    "Ted Jensen": ("Reconnu", "Ingénieur de mastering, Sterling Sound."),
    "Greg Calbi": ("Reconnu", "Ingénieur de mastering, Sterling Sound / Masterdisk."),
    "Ray Staff": ("Reconnu", "Ingénieur de gravure, Trident Studios / Air Mastering."),
    "Arun Chakraverty": ("Reconnu", "Ingénieur de gravure, Master Room (Londres)."),
    "Phil Ramone": ("Référence historique", "Ingénieur et réalisateur, A&R Recording; Grammy multiples."),
    "Elliot Scheiner": ("Référence historique", "Ingénieur de prise de son et de mixage; Grammy multiples (Steely Dan)."),
    "Roger Nichols": ("Reconnu", "Ingénieur de Steely Dan; Grammy multiples."),
    "Bob Clearmountain": ("Référence historique", "Ingénieur de mixage, Power Station."),
    "Hugh Padgham": ("Reconnu", "Ingénieur de prise de son et de mixage; Grammy multiples."),
    "Glyn Johns": ("Référence historique", "Ingénieur et réalisateur (Rolling Stones, Led Zeppelin, The Who)."),
    "Eddie Kramer": ("Référence historique", "Ingénieur (Jimi Hendrix, Led Zeppelin), Electric Lady Studios."),
    "Tom Dowd": ("Référence historique", "Ingénieur d'Atlantic Records; pionnier de l'enregistrement multipiste."),
    "Paul Northfield": ("Reconnu", "Ingénieur de Le Studio, Morin-Heights (Rush)."),
    "Nick Blagona": ("Reconnu", "Ingénieur de Le Studio, Morin-Heights."),
    "Rafe McKenna": ("Reconnu", "Ingénieur de prise de son et de mixage, Royaume-Uni."),
    "Anthony Hawkins": ("Reconnu", "Ingénieur Decca (classique)."),
    "James Lock": ("Référence historique", "Ingénieur Decca (Solti, Karajan)."),
    "Gordon Parry": ("Référence historique", "Ingénieur Decca (Ring de Solti)."),
    "Stanley Goodall": ("Reconnu", "Ingénieur Decca (classique)."),
    "Günter Hermanns": ("Référence historique", "Ingénieur Deutsche Grammophon (Karajan)."),
    "Klaus Hiemann": ("Reconnu", "Ingénieur Deutsche Grammophon."),
    "Christopher Parker": ("Reconnu", "Ingénieur EMI (classique)."),
    "Ralph Couzens": ("Reconnu", "Ingénieur Chandos."),
    "Tony Faulkner": ("Reconnu", "Ingénieur de prise de son classique indépendant."),
    "Jack Renner": ("Référence historique", "Ingénieur Telarc; pionnier de l'enregistrement numérique classique."),
}


def nom_normal(nom):
    return re.sub(r"\s*\(\d+\)$", "", str(nom).replace("*", "")).strip()


def etapes(role):
    r = role.lower()
    out = set()
    if re.search(r"assist|2nd|second\b", r):
        out.add("Assistant")
    if re.search(r"lacquer|cut by|cutting|gravure", r):
        out.add("Gravure")
    if re.search(r"master", r):
        out.add("Mastering")
    if re.search(r"mix", r):
        out.add("Mixage")
    if re.search(r"recorded|recording|engineer|live|sound|ingenieur|transfer", r) and not out - {"Assistant"}:
        out.add("Prise de son")
    return out


def credits(texte):
    """[(nom, rôle)] des lignes « Rôle : Nom » qui touchent au son."""
    out = []
    for ligne in str(texte or "").split("\n"):
        m = re.match(r"^(.+?) : (.+)$", ligne.strip())
        if not m or m[1].startswith("http"):
            continue
        role = m[1].strip()
        # Une phrase de notes de pochette (« Enregistré au studio… : … ») n'est pas un rôle Discogs.
        if not SON.search(role) or ". " in role or re.search(r"[a-zà-ÿ]{2}\.$", m[2].strip()):
            continue
        for nom in re.split(r",\s*(?![^()]*\))", m[2]):
            nom = nom_normal(nom)
            if nom and len(nom) < 60 and re.search(r"[A-Za-zÀ-ÿ]{2}", nom) and not nom.endswith("."):
                out.append((nom, role))
    return out


def api(method, table, token, payload=None, query=None):
    url = f"https://api.airtable.com/v0/{BASE}/{table}"
    if query:
        url += "?" + urllib.parse.urlencode(query, doseq=True)
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    for essai in range(5):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and essai < 4:
                time.sleep(30)
                continue
            raise


def lire(table, token, champs):
    rows, offset = [], None
    while True:
        q = {"pageSize": 100, "fields[]": champs, "returnFieldsByFieldId": "true"}
        if offset:
            q["offset"] = offset
        page = api("GET", table, token, query=q)
        rows += page.get("records", [])
        offset = page.get("offset")
        if not offset:
            return rows
        time.sleep(0.25)


def par_lots(seq, n=10):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def main():
    token = os.getenv("AIRTABLE_TOKEN")
    if not token:
        print("AIRTABLE_TOKEN absent : liaison des prises de son reportée.", file=sys.stderr)
        return 1
    simulation = os.getenv("DRY_RUN", "0") == "1"
    disques = lire(EXEMPLAIRES, token, [F_TECH, F_LIEN_EX])
    personnes = lire(PRISES, token, [F_NOM, F_ETAPES, F_ROLES, F_LIEN_PS, F_REPUT])
    par_nom = {p["fields"].get(F_NOM, "").strip(): p for p in personnes if p["fields"].get(F_NOM)}

    voulu = {}   # nom -> {"ex": set, "roles": set}
    for d in disques:
        for nom, role in credits(d["fields"].get(F_TECH)):
            v = voulu.setdefault(nom, {"ex": set(), "roles": set()})
            v["ex"].add(d["id"])
            v["roles"].add(role)

    creer, maj = [], []
    for nom, v in sorted(voulu.items()):
        etp = sorted(set().union(*(etapes(r) for r in v["roles"])) or {"Prise de son"})
        p = par_nom.get(nom)
        if not p:
            champs = {F_NOM: nom, F_ETAPES: etp, F_ROLES: "\n".join(sorted(v["roles"])),
                      F_LIEN_PS: sorted(v["ex"])}
            rep, src = REPUTATIONS.get(nom, ("Non évalué", ""))
            champs[F_REPUT] = rep
            if src:
                champs[F_SOURCE] = src
            creer.append({"fields": champs})
            continue
        f = p["fields"]
        liens = set(f.get(F_LIEN_PS) or [])
        etp_tous = sorted(set(f.get(F_ETAPES) or []) | set(etp))
        roles = sorted(set(filter(None, (f.get(F_ROLES) or "").split("\n"))) | v["roles"])
        champs = {}
        if not v["ex"] <= liens:
            champs[F_LIEN_PS] = sorted(liens | v["ex"])
        if etp_tous != sorted(f.get(F_ETAPES) or []):
            champs[F_ETAPES] = etp_tous
        if "\n".join(roles) != (f.get(F_ROLES) or ""):
            champs[F_ROLES] = "\n".join(roles)
        if not f.get(F_REPUT):
            rep, src = REPUTATIONS.get(nom, ("Non évalué", ""))
            champs[F_REPUT] = rep
            if src:
                champs[F_SOURCE] = src
        if champs:
            maj.append({"id": p["id"], "fields": champs})

    print(f"Prises de son : {len(voulu)} personnes créditées, {len(creer)} à créer, {len(maj)} à mettre à jour.")
    if simulation:
        return 0
    for lot in par_lots(creer):
        api("POST", PRISES, token, {"records": lot, "typecast": True})
        time.sleep(0.25)
    for lot in par_lots(maj):
        api("PATCH", PRISES, token, {"records": lot, "typecast": True})
        time.sleep(0.25)
    # Relecture : une écriture acceptée n'est pas une écriture confirmée (règle 9).
    if creer or maj:
        relu = lire(PRISES, token, [F_NOM, F_LIEN_PS])
        relus = {r["fields"].get(F_NOM): set(r["fields"].get(F_LIEN_PS) or []) for r in relu}
        manquants = [n for n, v in voulu.items() if not v["ex"] <= relus.get(n, set())]
        if manquants:
            raise SystemExit(f"{len(manquants)} liaison(s) non confirmée(s) à la relecture : {manquants[:5]}")
        print("Relecture : toutes les liaisons confirmées.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
