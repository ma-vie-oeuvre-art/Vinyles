# Vinyles — séparation et activation sécurisée

Le catalogue public GitHub Pages est **en lecture seule**. L'administration est un Worker indépendant `vinyles-import-prive`. Aucun secret MVOA ne doit être repris.

## Secrets Cloudflare du Worker Vinyles

Configurer uniquement dans **Cloudflare > Workers & Pages > vinyles-import-prive > Settings > Variables and Secrets** :

- `AIRTABLE_TOKEN` : nouveau Personal Access Token Airtable créé pour **Vinyles / Inventaire** seulement, permissions `data.records:read` et `data.records:write`. Ne pas donner accès à MVOA ni à toutes les bases.
- `UPLOAD_KEY` : mot de passe aléatoire fort **distinct** du jeton Airtable, jamais commité et jamais copié dans une conversation.

Le secret `AIRTABLE_PAT` du Worker MVOA ne doit ni être lu ni être transféré.

## Validation avant ouverture

1. Secrets configurés : requête au point `/health` renvoie `configured: true`.
2. Requête `POST /import` sans Authorization : **401**, zéro fiche créée.
3. Essai avec une nouvelle position dans un **bac de test**, photo JPEG : création d'une fiche au statut `Dicté`, photo confirmée dans Airtable.
4. Même position soumise à nouveau : réponse 409, **pas de doublon**.
5. Revoir la fiche et vérifier l'image, la position, le statut.
6. Tester l'enrichissement Discogs séparément et confirmer les champs avant toute synchronisation du site public.
7. Activer l'adresse de capture seulement après validation des contrôles.

## Précautions

- La page publique de consultation ne contient **aucune clé** et ne doit pas exposer d'API d'écriture.
- Le Worker Vinyles est isolé de MVOA.
- Un mot de passe d'importation seul constitue une première barrière, pas une authentification forte personnelle. Pour une protection plus élevée, utiliser Cloudflare Access avec identité et MFA quand disponible.
- L'ingestion directe Airtable est un **prototype** : ne pas utiliser avec les photos réelles avant le test de bout en bout et le contrôle des erreurs/reprises.
- Le moteur actuel Discogs recherche des candidats pour les fiches disposant de métadonnées textuelles, mais **n'identifie pas automatiquement une photo brute**. Il faut un traitement d'image dédié pour arriver au fonctionnement souhaité.
