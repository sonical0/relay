@./relay-brain-ref.md
@./AGENTS.md

## Capacités Claude

- Accès filesystem complet (aucun sandbox)
- wmux-orchestrator : `/wmux:orchestrate` disponible (Claude uniquement)
- wmux-browser : `wmux browser open <url>` pour navigation visible en temps réel
- PDF : `node TOOLS/html-to-pdf-generator.mjs <in.html> <out.pdf>` *(outil cloud-only — vérifier son existence avant usage)*
- Sous-agents : Explore, Plan, general-purpose

## Limites Claude

- Pas de playwright-live (browser interactif direct) → utiliser wmux-browser à la place

## Note wmux

La configuration wmux (hook PostToolUse dans `~/.claude/settings.json`) est **locale à chaque poste**.
Sur un nouveau poste : lancer `/framework-init` ou installer wmux manuellement, puis vérifier `wmux ping`.
Mettre à jour `wmux_status` dans `./relay-brain.yml` après installation.
