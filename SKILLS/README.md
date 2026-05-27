# SKILLS — Compétences partagées Relay

Tables de routage de commandes et modes, partagées entre agents CLI.

Un SKILL définit les modes/commandes disponibles pour un projet, quand les utiliser, et quels fichiers charger. Il permet à un agent d'arriver à froid et de comprendre immédiatement comment travailler sur ce projet.

## Structure

```
SKILLS/
├── _template/
│   └── SKILL.md          ← Template à copier pour un nouveau projet
└── {nom-projet}/
    └── SKILL.md          ← Skill du projet
```

## Créer un skill

1. Copier `SKILLS/_template/SKILL.md` → `SKILLS/{nom-projet}/SKILL.md`
2. Remplir la routing table, le chargement de contexte, et les règles de délégation
3. Référencer depuis `PROJECTS/{nom-projet}/AGENTS.md` si nécessaire

## Contenu type d'un SKILL.md

- **Routing table** : commandes → agent + action
- **Chargement contexte** : quels fichiers lire par mode
- **Auto-détection** : patterns qui déclenchent un mode automatiquement
- **Sous-agents** : quand déléguer (volume > 3, batch, spéculatif)

## Données personnelles

Les SKILL.md réels sont **cloud-only** — non committés en git.
Seul `_template/SKILL.md` est en git.
