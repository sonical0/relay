# {PROJECT_NAME} — Instructions agents

## Qu'est-ce que ce projet

<!-- Une ou deux phrases : objectif principal, utilisateurs, outputs clés -->

## Structure des fichiers

| Chemin | Rôle |
|--------|------|
| (à remplir) | |

## Division des rôles

Voir `../../relay-brain.yml → conventions` pour les propriétaires par défaut.
Documenter ici uniquement les exceptions ou spécificités de ce projet.

| Tâche | Agent propriétaire | Notes |
|-------|-------------------|-------|
| (à définir) | | |

## Canal temps réel

Si le message broker est actif (`bash TOOLS/relay-server-start.sh`) :
- Envoyer une task à agy : `node TOOLS/relay-chat.mjs send claude --to agy "<prompt>"`
- Lire les résultats : `node TOOLS/relay-chat.mjs poll claude`

## Contrat de données

<!-- Quels fichiers sont des données utilisateur vs générés automatiquement ? -->
<!-- Qui peut écraser quoi sans confirmation préalable ? -->

## Conventions locales

<!-- Uniquement ce qui diffère des conventions globales dans relay-brain.yml -->
<!-- Si rien ne diffère, laisser vide -->
