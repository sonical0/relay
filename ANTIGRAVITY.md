@./AGENTS.md

## Capacités Antigravity CLI (agy)

- Playwright headless : liveness checks, scraping SPAs
- node-scripts, python, shell
- Sous-agents : generalist, codebase_investigator
- wmux-browser si wmux installé sur le poste

## Limites Antigravity

- Sandbox = RELAY_ROOT et sous-dossiers uniquement
- Pas de wmux-orchestrator (peut être spawné comme worker, pas orchestrateur)
- Pas d'accès aux chemins hors sandbox

## Sandbox

Si `$RELAY_ROOT` défini → sandbox = `$RELAY_ROOT`
Sinon → dossier contenant ce fichier `ANTIGRAVITY.md`

## Migration depuis Gemini CLI

`antigravity-cli` (commande `agy`) est le successeur de `gemini-cli` (commande `gemini`).
Si vous utilisez encore `gemini-cli`, voir `GEMINI.md` pour les notes de compatibilité.
Toutes les capacités Gemini CLI sont disponibles dans antigravity-cli.
