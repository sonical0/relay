# PROJECTS — Projets Relay

Un sous-dossier par projet. Chaque projet est un "spoke" du hub Relay.

## Créer un nouveau projet

### Via le script (recommandé)
```bash
node TOOLS/init-tandem-project.mjs mon-projet
```
Crée automatiquement `PROJECTS/mon-projet/` depuis `_template/` et met à jour `tandem-brain.yml`.

### Manuellement
1. Copier `PROJECTS/_template/` → `PROJECTS/{nom}/`
2. Remplir `PROJECTS/{nom}/AGENTS.md`
3. Adapter `PROJECTS/{nom}/.tandem-bootstrap.md`
4. Ajouter dans `tandem-brain.yml → projects:`
5. Logger dans `TANDEM_LOG.md`

## Structure minimale d'un projet

```
PROJECTS/{nom}/
├── AGENTS.md              ← Règles métier, rôles, structure
├── CLAUDE.md              ← @../../CLAUDE.md + @./AGENTS.md
├── GEMINI.md              ← @../../GEMINI.md + @./AGENTS.md
├── .tandem-bootstrap.md   ← Overrides locaux (pointe vers ../../tandem-bootstrap.md)
└── TANDEM_LOG.md          ← Stub (écrire dans ../../TANDEM_LOG.md)
```

## Données personnelles

Le contenu des projets (données, outputs, trackers, rapports...) est **cloud-only** — non committé en git.
Seul `_template/` est en git.
