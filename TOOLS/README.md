# TOOLS — Outils partagés Relay

Scripts transverses, standalone, utilisables par tous les agents sur tout projet.

Les scripts `.mjs` sont dans le cloud (non committés en git). Ce README est le contrat d'interface public.

## Convention d'interface

- Erreurs → **stderr** / succès → **stdout**
- Exit code : **0** = succès, **1** = erreur
- Flag `--json` optionnel pour output machine-readable : `{"status":"ok","result":{...}}`
- Pas de chemins hardcodés — arguments CLI ou résolution relative via `import.meta.url`

## Critères de promotion dans TOOLS/

Un script mérite d'être dans TOOLS/ si :
1. Utilité généraliste (pas spécifique à un seul projet)
2. Standalone (dépendances au contexte du projet éliminables via args CLI)
3. Interface standardisée (args CLI, exit code, no hardcoded paths)
4. Besoin avéré sur au moins 2 projets (éviter la sur-abstraction prématurée)

## Outils disponibles

| Outil | Fichier | Usage | Args |
|-------|---------|-------|------|
| HTML → PDF | `html-to-pdf-generator.mjs` | `node TOOLS/html-to-pdf-generator.mjs <in.html> <out.pdf>` | `--format=a4\|letter` |
| Fix Mojibake | `fix-mojibake.mjs` | `node TOOLS/fix-mojibake.mjs --dir=<path>` | `--dir=<path>` (requis) |
| Init Projet | `init-tandem-project.mjs` | `node TOOLS/init-tandem-project.mjs <nom-projet>` | nom-projet |

## Résolution de chemin dans les scripts

Tous les scripts utilisent `path.dirname(fileURLToPath(import.meta.url))` + navigation relative pour trouver TANDEM_ROOT dynamiquement. Aucun chemin absolu hardcodé.

Exemple :
```js
import { fileURLToPath } from 'url'
import path from 'path'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const TANDEM_ROOT = path.join(__dirname, '..')  // TOOLS/../ = relay/
```
