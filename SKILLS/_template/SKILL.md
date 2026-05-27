# {PROJECT_NAME} — Skill

## Routing table

| Commande / Mode | Déclencheur | Agent | Action |
|-----------------|------------|-------|--------|
| (à remplir) | | | |

## Chargement du contexte par mode

Fichiers à lire selon le mode actif :
- **mode X** → lire : `./fichier1.md`, `./config/fichier2.yml`
- **mode Y** → lire : ...

## Auto-détection

Patterns qui déclenchent un mode sans que l'utilisateur le spécifie :
- Si l'entrée contient une URL → mode ...
- Si l'entrée est un chemin de fichier → mode ...

## Règles de délégation sous-agents

- Volume > 3 fichiers → déléguer à un sous-agent
- Recherche spéculative (fichiers inconnus) → sous-agent Explore
- Même tâche × N → batch en sous-agents parallèles

## Découverte

Quand l'utilisateur tape une commande ambiguë ou demande de l'aide :
→ Afficher cette table de routage comme menu de découverte.
