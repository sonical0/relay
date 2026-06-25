# TOOLS — Outils partagés Relay

Scripts transverses, standalone, utilisables par tous les agents sur tout projet.

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

| Outil | Fichier | Usage | Notes |
|-------|---------|-------|-------|
| Init Projet | `init-relay-project.mjs` | `node TOOLS/init-relay-project.mjs <nom>` | Crée PROJECTS/{nom}/ + met à jour relay-brain.yml |
| HTML → PDF | `html-to-pdf-generator.mjs` | `node TOOLS/html-to-pdf-generator.mjs <in.html> <out.pdf>` | `--format=a4\|letter` |
| Fix Mojibake | `fix-mojibake.mjs` | `node TOOLS/fix-mojibake.mjs --dir=<path>` | Répare encodages UTF-8 cassés |
| Message Broker | `relay-server.py` | `python3 TOOLS/relay-server.py [--port N] [--chat PATH]` | HTTP broker SQLite port 7432 |
| Live Chat CLI | `relay-chat.mjs` | `node TOOLS/relay-chat.mjs send\|poll\|read\|status` | Communication inter-agents |
| Agent Loop | `relay-agent-loop.sh` | `bash TOOLS/relay-agent-loop.sh` | Listener agy/gemini via tmux (Linux) |
| Server Start | `relay-server-start.sh` | `bash TOOLS/relay-server-start.sh` | Démarre le broker en arrière-plan |

### Variables d'environnement (relay-chat.mjs)

| Variable | Défaut | Description |
|----------|--------|-------------|
| `RELAY_SERVER` | `http://127.0.0.1:7432` | URL du message broker |
| `RELAY_CHAT_FILE` | `../RELAY_CHAT.md` | Chemin du fichier Live Chat |
| `AGY_BIN` | `agy` | Chemin/nom de l'exécutable antigravity (relay-agent-loop.sh) |

## Résolution de chemin dans les scripts

Tous les scripts utilisent `path.dirname(fileURLToPath(import.meta.url))` + navigation relative pour trouver RELAY_ROOT dynamiquement. Aucun chemin absolu hardcodé.

```js
import { fileURLToPath } from 'url'
import path from 'path'

const __dirname  = path.dirname(fileURLToPath(import.meta.url))
const RELAY_ROOT = path.join(__dirname, '..')  // TOOLS/../ = relay/
```
