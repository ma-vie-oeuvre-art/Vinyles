# MANIFESTE D'EXÉCUTION — couche de lecture du Worker, v25

**Date** : 3 septembre 2026
**Fichier livré** : `worker.js`
**Décisions portées** : V2-#35 (recUI54pL5FTSY1YI), V2-#36 (recqLJWUWSgv45Aum), les deux En exploration
**Décision qui régit ce document** : V2-#20 (recP59KWQKCRHrO6j, Actif)

---

## Écart de processus, consigné avant tout le reste

V2-#20 exige la séquence : vote → manifeste → écriture → relecture live → assertions → « fait ».

Le code a été écrit avant ce manifeste. Le manifeste est donc rédigé **après** la mutation, ce qui n'est pas la séquence votée. Il est conservé sous cette forme plutôt que rétrodaté ou présenté comme conforme (V2-#9 : la correction préserve la trace de l'erreur).

Conséquence pratique : les invariants ci-dessous ont été dérivés du texte des décisions, non du code, précisément pour éviter qu'un manifeste écrit après coup se contente de décrire ce qui a été fait.

---

## A — Invariants de non-régression

Ce qui existait avant doit continuer de fonctionner. Deux régressions ont déjà été introduites puis corrigées ; ces invariants existent pour les empêcher de revenir.

| # | Invariant | Comment le vérifier |
|---|---|---|
| A1 | `Pré-remplissage IA` porte un attribut `methode_lecture` qui n'est **jamais** `aucune lecture` quand une URL est présente et lisible | lire le JSON du record après compilation |
| A2 | Le détecteur de **#206** fonctionne : sur une capture ne portant qu'une URL, `titre_rempli` est non nul et `Élément capturé` est renseigné | lire `titre_rempli` dans le JSON |
| A3 | Le contrat de **V2-#21 clause 3** tient : 1 entrée → 1 lecture → 0..N propositions | compter les propositions dans le JSON |
| A4 | **V2-#21 clause 4 / V2-#24 clause 1** : aucun objet dérivé n'est créé automatiquement — ni RD, ni Action, ni RA | vérifier qu'aucun record n'apparaît dans les tables filles |
| A5 | Le classement de **#206** est inchangé : une capture sans texte de Luc reste `Observation` | lire `Type suggéré` |

---

## B — Invariants portant V2-#35

| # | Invariant | Comment le vérifier |
|---|---|---|
| B1 | **Interdit 1 — jamais silencieuse.** Après substitution, l'URL d'origine apparaît en toutes lettres dans le contenu lu | chercher `URL d'origine, conservée :` |
| B2 | **Interdit 2 — jamais présentée comme établie.** Toute substitution est préfixée `DÉDUIT — SOURCE SUBSTITUÉE (V2-#35)` et nomme la clé ayant servi | chercher `DÉDUIT — SOURCE SUBSTITUÉE` et `Clé ayant servi` |
| B3 | **Interdit 3 — jamais automatique jusqu'à l'écriture d'objets.** Une substitution ne crée aucun objet | identique à A4 |
| B4 | **Condition de clé.** Sans titre exploitable, aucune recherche n'est lancée | le journal doit porter `résolution : non tentée — aucune clé exploitable` |
| B5 | **Pas de dérive en chaîne.** Une source substituée n'est jamais elle-même résolue | le journal doit porter `résolution : non tentée (on relisait déjà une source substituée)` si le cas se présente |

---

## C — Invariants portant V2-#36 (amendement du 3 septembre)

| # | Invariant | Comment le vérifier |
|---|---|---|
| C1 | La transcription se déclenche **sans geste de Luc** sur une vidéo à sous-titres publics | journal : `transcription : sous-titres récupérés` |
| C2 | Le texte transcrit est présenté comme **matière source**, pas comme une inférence | le contenu porte `texte de la pièce, non une inférence` |
| C3 | **Coût nul** : aucun appel facturé pour la transcription | aucune clé de fournisseur tiers dans le code |
| C4 | La couverture annoncée est honnête : **échec attendu** sur une pièce sans piste de texte | journal : `transcription : aucun sous-titre accessible` |

---

## D — Invariant de traçabilité

| # | Invariant | Comment le vérifier |
|---|---|---|
| D1 | Tout contenu lu porte un `JOURNAL DE LECTURE` énumérant chaque étape tentée et son issue | présence du bloc dans le contenu |
| D2 | Un échec total ne dit plus `statut 400` seul : il énumère ce qui a été essayé | lire le journal sur un cas d'échec |
| D3 | Le marqueur `(source : X)` demeure le **tout dernier** élément de la chaîne | contrainte de l'extracteur en aval — cause de la régression corrigée |

---

## Protocole de relecture live

Après déploiement, trois compilations de contrôle :

1. **recWCGQm2495vzM1q** — Reel Facebook, sans équivalent ouvert connu.
   Attendu : échec tracé, journal complet, aucune substitution inventée. Vérifie A1, B4, D1, D2.

2. **rectgwLtp0JmHdj21** — URL YouTube.
   Attendu : transcription récupérée. Vérifie A2, C1, C2, D3.

3. **Une nouvelle capture Facebook portant le titre exact d'une œuvre.**
   Attendu : substitution déclarée. Vérifie B1, B2, B5.

---

## Règle de clôture

Conformément à V2-#20, le mot « fait » n'est prononcé qu'après la relecture live.

Un écart mécanique et non ambigu est corrigé par l'agent, qui recommence le contrôle. Un écart exigeant une nouvelle décision est **nommé, jamais contourné**.

Ces deux décisions restent **En exploration**. Le challenge de ChatGPT n'a pas eu lieu, et il porte désormais aussi sur l'amendement de V2-#36.
