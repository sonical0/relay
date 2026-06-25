# Relay Bootstrap — Générique
# Lire en PREMIER sur tout nouveau projet, avant AGENTS.md ou RELAY_LOG.md local.
# Overrides projet-spécifiques : voir .relay-bootstrap.md à la racine du projet.

## 1. Localisation des ressources partagées

Brain global  : ./relay-brain.yml        (relatif depuis RELAY_ROOT)
RELAY_LOG     : ./RELAY_LOG.md
SKILLS        : ./SKILLS/

RELAY_ROOT    : répertoire contenant ce fichier.
                Si $RELAY_ROOT est défini dans l'environnement → utiliser cette valeur.
                Sinon → résoudre depuis l'emplacement de ce fichier (relay-bootstrap.md).

## 2. Protocole d'entrée (ordre obligatoire)

1. Lire relay-brain.yml → capacités agents, claims actifs
   - relay-brain-ref.md déjà chargé via CLAUDE.md (@include, caché) → capacités agents,
     conventions, architecture disponibles sans lecture supplémentaire.
2. Lire RELAY_LOG.md global (50 dernières lignes) → état récent cross-projets
2.5. Lire RELAY_CHAT.md si présent → canal temps réel inter-agents
3. Lire .relay-bootstrap.md LOCAL au projet → overrides spécifiques
4. Lire AGENTS.md (ou équivalent) du projet → règles métier locales
5. Vérifier claims[] dans relay-brain.yml → ne pas doubler le travail
6. **Vérifier hooks Claude Code** : les 3 hooks doivent être présents dans ~/.claude/settings.json
   Si absents → exécuter immédiatement : `node .claude/hooks/relay-setup-hook.js`
   Ce script est idempotent et affiche un avertissement si des hooks ont été réinstallés.
7. **Vérifier wmux** : `wmux ping` → comportement conditionnel selon OS (voir AGENTS.md)
8. Commencer, logger dans RELAY_LOG.md global

## 3. Protocole "qui fait quoi" sur un nouveau projet

- Lire relay-brain.yml → section conventions (propriétaires par défaut)
- Si le projet a des besoins différents → ouvrir un débat dans RELAY_LOG.md
- Ne PAS assumer que la division d'un projet existant s'applique à un autre
- Documenter la division retenue dans .relay-bootstrap.md local

## 4. Checklist — Démarrer un nouveau projet

- [ ] Copier PROJECTS/_template/ → PROJECTS/{nom}/
- [ ] Remplir PROJECTS/{nom}/AGENTS.md (rôles, structure, conventions)
- [ ] Adapter PROJECTS/{nom}/.relay-bootstrap.md (pointer vers ../../relay-bootstrap.md + overrides)
- [ ] Ajouter projet dans relay-brain.yml → section projects:
- [ ] Créer SKILLS/{nom}/SKILL.md si le projet nécessite des outils partagés spécifiques
- [ ] Débattre et documenter la division des rôles dans RELAY_LOG.md global

## 5. Conventions globales (héritées par tous les projets)

- Lire before write (atomique : lire → modifier → sauvegarder en une opération)
- Batch summary dans RELAY_LOG (pas une ligne par rapport individuel)
- Format RELAY_LOG : max 5 bullets par action, 15 mots chacun. Structure : `## YYYY-MM-DD — Agent (projet : sujet)`. Pas de sous-listes imbriquées.
- Claims dans relay-brain.yml avant toute tâche longue
- Jamais écraser le travail de l'autre sans log
- cross_project_log : garder 30 derniers jours dans relay-brain.yml. Archiver dans relay-brain-history.yml.
- Si bootstrap générique change : ajouter projet dans conventions.needs_bootstrap_sync
