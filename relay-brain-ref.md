# Relay — Référence agents & conventions
# Ce fichier est statique (ne change pas entre sessions).
# Chargé via @include dans CLAUDE.md pour le context caching.
# Pour l'état courant (projets, claims, wmux) → lire relay-brain.yml.

## Architecture

- **Hub** : `./relay-brain.yml` (état courant) + `./relay-brain-ref.yml` (référence statique)
- **Projets** : `./PROJECTS/{PROJECT}/`
- **Skills** : `./SKILLS/`
- **Tools** : `./TOOLS/`
- **RELAY_ROOT** : dossier contenant `AGENTS.md`. Résoudre via `$RELAY_ROOT` (si défini) ou relatif.

## Capacités agents

### Claude Code
| Peut | Ne peut pas |
|---|---|
| pdf-generation (`node TOOLS/html-to-pdf-generator.mjs`) | playwright-live (browser interactif) |
| node-scripts, file-write (tout chemin) | |
| webfetch / websearch | |
| wmux-browser + wmux-orchestrator (`/wmux:orchestrate`) | |
| sous-agents : Explore, Plan, general-purpose | |

### Antigravity CLI (agy) — anciennement Gemini CLI
| Peut | Ne peut pas |
|---|---|
| playwright-headless (scraping SPAs, liveness) | paths-outside-sandbox |
| node-scripts / python / shell | wmux-orchestrator |
| wmux-browser (si wmux installé) | |
| sous-agents : generalist, codebase_investigator | |

**Sandbox Antigravity** : `RELAY_ROOT` et sous-dossiers uniquement. Pas d'accès hors sandbox.

### Orchestration cross-agents
- Orchestrateur : **Claude** seul (`/wmux:orchestrate`)
- Worker : antigravity spawnable via `wmux agent spawn --cmd "agy -p @prompt.md"`
- Pattern : Wave 1 workers parallèles → Wave 2 merge/synthèse

## Conventions

| Règle | Valeur |
|---|---|
| Encodage | UTF-8 sans BOM. Jamais PowerShell pour texte accentué → Node.js |
| PDF | 2 fichiers : `[slug].pdf` (couleur) + `[slug]-bw.pdf` (N&B imprimable) |
| PDF owner | Claude (`TOOLS/html-to-pdf-generator.mjs`) |
| Liveness owner | Antigravity (Playwright headless) |
| Merge owner | Claude |
| Écriture | Atomique : lire → modifier → sauvegarder en une opération |
| Claims | Déclarer dans `relay-brain.yml` avant toute tâche longue |
| RELAY_LOG format | `## YYYY-MM-DD — Agent (projet : sujet)` + max 5 bullets, 15 mots chacun. Pas de sous-listes |
| cross_project_log | Garder 30 derniers jours dans brain.yml. Archiver dans `relay-brain-history.yml` |

## Délégation sous-agents

- **Claude** : Explore, Plan, general-purpose
- **Antigravity** : generalist, codebase_investigator
- Seuils : >3 fichiers, tâche batch, recherche spéculative
- Sous-agents héritent du sandbox parent

## Message broker (temps réel)

- **Serveur** : `python3 TOOLS/relay-server.py` → port 7432, SQLite, purge auto 24h
- **CLI** : `node TOOLS/relay-chat.mjs send|poll|read|status`
- **Agent loop** : `bash TOOLS/relay-agent-loop.sh` (tmux, Linux)
- **Canal** : `RELAY_CHAT.md` (cloud-synced, gitignored)

## Nouveau poste — setup

```bash
node .claude/hooks/relay-setup-hook.js   # installe les 3 hooks Claude Code (idempotent)
cp relay-brain.template.yml relay-brain.yml   # ou /framework-init
```
