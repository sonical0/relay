# Tandem Bootstrap — Générique
# Lire en PREMIER sur tout nouveau projet, avant AGENTS.md ou TANDEM_LOG.md local.
# Overrides projet-spécifiques : voir .tandem-bootstrap.md à la racine du projet.

## 1. Localisation des ressources partagées

Brain global  : ./tandem-brain.yml        (relatif depuis TANDEM_ROOT)
TANDEM_LOG    : ./TANDEM_LOG.md
SKILLS        : ./SKILLS/

TANDEM_ROOT   : répertoire contenant ce fichier.
                Si $TANDEM_ROOT est défini dans l'environnement → utiliser cette valeur.
                Sinon → résoudre depuis l'emplacement de ce fichier (tandem-bootstrap.md).

## 2. Protocole d'entrée (ordre obligatoire)

1. Lire tandem-brain.yml → capacités agents, next_report_num, claims actifs
2. Lire TANDEM_LOG.md global (50 dernières lignes) → état récent cross-projets
3. Lire .tandem-bootstrap.md LOCAL au projet → overrides spécifiques
4. Lire AGENTS.md (ou équivalent) du projet → règles métier locales
5. Vérifier claims[] dans tandem-brain.yml → ne pas doubler le travail
6. **Vérifier wmux** : `wmux ping` → comportement conditionnel selon OS (voir AGENTS.md)
7. Commencer, logger dans TANDEM_LOG.md global

## 3. Protocole "qui fait quoi" sur un nouveau projet

- Lire tandem-brain.yml → section conventions (propriétaires par défaut)
- Si le projet a des besoins différents → ouvrir un débat dans TANDEM_LOG.md
- Ne PAS assumer que la division d'un projet existant s'applique à un autre
- Documenter la division retenue dans .tandem-bootstrap.md local

## 4. Checklist — Démarrer un nouveau projet

- [ ] Copier PROJECTS/_template/ → PROJECTS/{nom}/
- [ ] Remplir PROJECTS/{nom}/AGENTS.md (rôles, structure, conventions)
- [ ] Adapter PROJECTS/{nom}/.tandem-bootstrap.md (pointer vers ../../tandem-bootstrap.md + overrides)
- [ ] Ajouter projet dans tandem-brain.yml → section projects:
- [ ] Créer SKILLS/{nom}/SKILL.md si le projet nécessite des outils partagés spécifiques
- [ ] Débattre et documenter la division des rôles dans TANDEM_LOG.md global

## 5. Conventions globales (héritées par tous les projets)

- Lire before write (atomique : lire → modifier → sauvegarder en une opération)
- Batch summary dans TANDEM_LOG (pas une ligne par rapport individuel)
- Claims dans tandem-brain.yml avant toute tâche longue
- Jamais écraser le travail de l'autre sans log
- Si bootstrap générique change : ajouter projet dans needs_bootstrap_sync de tandem-brain.yml
