# Relay — Instructions pour tous les agents CLI

> **Lis ce fichier en PREMIER**, avant CLAUDE.md, GEMINI.md ou tout autre fichier agent.
> Ensuite lis ton fichier spécifique (`CLAUDE.md`, `GEMINI.md`, etc.) pour tes capacités et limites.

---

## Qu'est-ce que ce framework

Relay est un hub de coordination pour plusieurs agents CLI (Claude Code, Gemini CLI, et futurs agents) travaillant en tandem sur des projets. Il centralise le protocole, les conventions et la mémoire partagée entre agents.

---

## Résolution de TANDEM_ROOT

`TANDEM_ROOT` = le dossier contenant ce fichier `AGENTS.md`.

- Dans les fichiers `.md` et `.yml` : chemins relatifs depuis `TANDEM_ROOT` (ex: `./tandem-brain.yml`)
- Dans les scripts Node.js : `path.dirname(fileURLToPath(import.meta.url))` + navigation relative
- Variable d'environnement `$TANDEM_ROOT` optionnelle : si définie, prend la priorité

Ne jamais hardcoder de chemin absolu dans ce framework.

---

## Détection premier lancement

**Avant toute autre chose** : vérifie si `./tandem-brain.yml` existe.

Si **absent** (seul `./tandem-brain.template.yml` existe) :
> Tu es sur un nouveau poste. N'entre **pas** dans le protocole normal.
> Dis à l'utilisateur : "Je détecte un premier lancement du Relay. Je vais te guider à travers la configuration initiale."
> Lance `/framework-init` ou guide manuellement via `SETUP.md`.

Si **présent** : continuer avec le protocole d'entrée ci-dessous.

---

## Protocole d'entrée (ordre obligatoire)

1. **Lire `./tandem-brain.yml`** → capacités agents, claims actifs, état wmux
2. **Lire `./TANDEM_LOG.md` (50 dernières lignes)** → état récent cross-projets
3. **Lire `.tandem-bootstrap.md` LOCAL au projet** (si opération dans un sous-projet de PROJECTS/)
4. **Lire `AGENTS.md` du projet** → règles métier locales du projet en cours
5. **Vérifier `claims[]` dans tandem-brain.yml** → ne pas doubler le travail
6. **Vérifier wmux** (voir section dédiée ci-dessous)
7. **Commencer, logger dans `./TANDEM_LOG.md`**

---

## Étape wmux — comportement conditionnel

Tester la disponibilité : `wmux ping`

**Windows** : si `wmux ping` échoue → proposer activement l'installation à l'utilisateur.
Expliquer : wmux = panel navigateur visible en temps réel, intégré au terminal CLI.
- Si l'utilisateur accepte : guider l'install, puis mettre `wmux_status: operational` dans tandem-brain.yml.
- Si refus : mettre `wmux_status: unavailable` et continuer sans.

**macOS** : wmux non disponible nativement. Proposer de rechercher ensemble des alternatives adaptées (iTerm2, Warp, etc.). Mettre `wmux_status: unavailable` pour l'instant.

**Linux** : wmux non disponible nativement. Alternatives possibles : tmux + xdotool. Proposer à l'utilisateur. Mettre `wmux_status: unavailable` si refus ou non applicable.

Si wmux opérationnel :
- Navigation web visible : `wmux browser open <url>` → `wmux browser snapshot` → `wmux browser click @eN`
- Plugin orchestrateur (agent orchestrateur uniquement) : `/wmux:orchestrate "<tâche>"`

---

## Plan des dossiers

| Dossier | Rôle | Chemin relatif |
|---------|------|---------------|
| `PROJECTS/` | Un sous-dossier par projet. Chaque projet a AGENTS.md, CLAUDE.md, GEMINI.md, .tandem-bootstrap.md | `./PROJECTS/{nom}/` |
| `SKILLS/` | Tables de routage de commandes, partagées entre agents. Un dossier par projet ayant des skills. | `./SKILLS/{nom}/SKILL.md` |
| `TOOLS/` | Scripts utilitaires standalone (Node.js). Aucune logique projet-spécifique. Interface : args CLI, exit 0/1. | `./TOOLS/{script}.mjs` |
| `TANDEM_LOG.md` | Journal partagé cross-agents, cross-projets. Un batch-summary par tâche significative. | `./TANDEM_LOG.md` |
| `tandem-brain.yml` | Hub d'état central : capacités agents, registry projets, claims, conventions. | `./tandem-brain.yml` |

---

## Conventions partagées

- **Encodage** : toujours UTF-8 sans BOM. Ne jamais utiliser PowerShell pour écrire du texte accentué — utiliser write_file/replace ou Node.js.
- **Atomicité** : lire → modifier → sauvegarder en une seule opération. Pas de lecture puis écriture séparée.
- **Batch logging** : 1 ligne par batch dans TANDEM_LOG.md. Pas une ligne par fichier traité.
- **Claims** : ajouter un claim dans `tandem-brain.yml → projects.{nom}.claims[]` avant toute tâche longue. Format : `{id, agent, status: in_progress|done|blocked, started_at, note}`.
- **Pas d'écrasement** : ne jamais écraser le travail d'un autre agent sans le documenter dans TANDEM_LOG.md.
- **Sync bootstrap** : si `tandem-bootstrap.md` générique change, ajouter le projet dans `conventions.needs_bootstrap_sync`.

---

## Protocole "qui fait quoi" sur un nouveau projet

1. Lire `tandem-brain.yml → conventions` (propriétaires par défaut)
2. Si le projet a des besoins différents → ouvrir un débat dans `TANDEM_LOG.md`
3. Ne pas assumer que la division d'un projet existant s'applique au nouveau
4. Documenter la division retenue dans `.tandem-bootstrap.md` local du projet

---

## Checklist — démarrer un nouveau projet

- [ ] Copier `PROJECTS/_template/` → `PROJECTS/{nom}/`
- [ ] Remplir `PROJECTS/{nom}/AGENTS.md` (rôles, structure, conventions)
- [ ] Adapter `PROJECTS/{nom}/.tandem-bootstrap.md` (overrides par rapport au générique)
- [ ] Ajouter projet dans `tandem-brain.yml → projects:`
- [ ] Créer `SKILLS/{nom}/SKILL.md` si le projet a des modes/commandes spécifiques
- [ ] Documenter la division des rôles dans `TANDEM_LOG.md`

Raccourci : `node TOOLS/init-tandem-project.mjs {nom}` (automatise les étapes 1, 3, 4).

---

## Ajouter un nouveau CLI agent

1. Créer `{AGENT}.md` à la racine de TANDEM_ROOT (même pattern que CLAUDE.md / GEMINI.md)
2. Ce fichier commence par `@./AGENTS.md`, puis déclare les capacités et limites spécifiques
3. Ajouter le nouvel agent dans `tandem-brain.yml → agents:`
4. Si le CLI supporte les slash commands : créer `.{agent}/commands/framework-init.{ext}` avec le wizard adapté
5. Documenter dans TANDEM_LOG.md
