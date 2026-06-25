# PROJECTS — Projets Relay

Un sous-dossier par projet. Chaque projet est un "spoke" du hub Relay.

## Créer un nouveau projet

### Via le script (recommandé)
```bash
node TOOLS/init-relay-project.mjs mon-projet
```
Crée automatiquement `PROJECTS/mon-projet/` depuis `_template/` et met à jour `relay-brain.yml`.

### Manuellement
1. Copier `PROJECTS/_template/` → `PROJECTS/{nom}/`
2. Remplir `PROJECTS/{nom}/AGENTS.md`
3. Adapter `PROJECTS/{nom}/.relay-bootstrap.md`
4. Ajouter dans `relay-brain.yml → projects:`
5. Logger dans `RELAY_LOG.md`

## Structure minimale d'un projet

```
PROJECTS/{nom}/
├── AGENTS.md              ← Règles métier, rôles, structure
├── CLAUDE.md              ← @../../CLAUDE.md + @./AGENTS.md
├── GEMINI.md              ← @../../ANTIGRAVITY.md + @./AGENTS.md
├── .relay-bootstrap.md    ← Overrides locaux (pointe vers ../../relay-bootstrap.md)
└── RELAY_LOG.md           ← Stub (écrire dans ../../RELAY_LOG.md)
```

## Données personnelles

Le contenu des projets (données, outputs, trackers, rapports...) est **cloud-only** — non committé en git.
Seul `_template/` est en git.
