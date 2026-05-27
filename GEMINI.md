@./AGENTS.md

## Capacités Gemini

- Playwright headless : liveness checks, scraping SPAs (Indeed, WTTJ, etc.)
- node-scripts, python, shell-powershell
- Sous-agents : generalist, codebase_investigator
- wmux-browser si wmux installé sur le poste

## Limites Gemini

- Sandbox = TANDEM_ROOT et sous-dossiers uniquement
- Pas de wmux-orchestrator (peut être spawné comme worker, pas orchestrateur)
- Pas d'accès aux chemins hors sandbox

## Sandbox

Si `$TANDEM_ROOT` défini → sandbox = `$TANDEM_ROOT`
Sinon → dossier contenant ce `GEMINI.md`
