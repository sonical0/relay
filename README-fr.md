<div align="center">

# Relay

**Un framework de coordination portable pour les workflows multi-agents IA en ligne de commande**

[![License: MIT](https://img.shields.io/badge/Licence-MIT-yellow?style=for-the-badge)](LICENSE)
[![Node.js](https://img.shields.io/badge/Node.js-18+-339933?style=for-the-badge&logo=node.js&logoColor=white)](https://nodejs.org)
[![Python](https://img.shields.io/badge/Python-3.8+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Platform](https://img.shields.io/badge/Plateforme-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey?style=for-the-badge)]()
[![Last Commit](https://img.shields.io/github/last-commit/sonical0/relay?style=for-the-badge)](https://github.com/sonical0/relay/commits/master)

[Read in English](README.md)

</div>

---

## Le problème

Les agents IA en ligne de commande — Claude Code, Antigravity CLI (agy), Gemini CLI — sont individuellement capables de tâches complexes en plusieurs étapes. Le problème apparaît quand plusieurs d'entre eux travaillent sur un même projet : aucune mémoire partagée, aucune conscience de ce que fait l'autre, aucun mécanisme pour diviser le travail. Le contexte se perd entre les sessions. Le travail est dupliqué. Un agent défait ce que l'autre vient de faire.

## La solution

Relay est une couche de coordination en étoile (hub-and-spoke) qui s'intercale entre vos agents et vos projets. À chaque démarrage, chaque agent lit le même point d'entrée, consulte le même fichier d'état partagé, ajoute une entrée dans le même journal d'activité, et suit le même protocole pour revendiquer une tâche avant de la commencer.

```
                        RELAY ROOT
                            │
          ┌─────────────────┼──────────────────┐
          │                 │                  │
      CLAUDE.md       ANTIGRAVITY.md     {agent}.md
          │                 │
     relay-brain.yml ◄──────┘    (état partagé)
     RELAY_LOG.md    ◄──────┘    (journal partagé)
     RELAY_CHAT.md   ◄──────┘    (canal live)
          │
    ┌─────┴──────┐
    │            │
PROJECTS/     SKILLS/
project-a/    project-a/SKILL.md
project-b/    project-b/SKILL.md
```

Le dépôt Git contient le **protocole** (agnostique aux agents, partageable, stable). Les données personnelles des projets vivent dans la synchronisation cloud. Un nouveau poste est opérationnel en trois commandes.

---

## Fonctionnement

**`AGENTS.md`** — Point d'entrée universel. Chaque agent CLI lit ce fichier en premier. Définit le protocole d'entrée, l'organisation des répertoires, les conventions partagées, et la procédure de premier lancement.

**`relay-brain.yml`** — Hub d'état partagé. Répertorie les agents disponibles, les projets enregistrés, et les tâches actuellement revendiquées. Les agents écrivent des claims avant de commencer des tâches longues.

**`RELAY_LOG.md`** — Journal d'activité partagé. Les agents y écrivent des résumés par batch (max 5 bullets, 15 mots chacun). Surface de coordination principale entre agents asynchrones.

**`SKILLS/`** — Tables de routage de commandes par projet. Définit les modes disponibles, quels fichiers charger pour chaque mode, quand lancer des sous-agents, et comment détecter l'intention automatiquement.

**`TOOLS/`** — Scripts utilitaires partagés. Chaque outil expose une interface CLI standard (arguments, codes de sortie, `--json` optionnel) sans chemins hardcodés.

---

## Stack

| Couche | Technologie |
|--------|-------------|
| État et configuration | YAML, Markdown |
| Scripts utilitaires | Node.js 18+ (ESM), Python 3.8+ |
| Intégration agents | Claude Code CLI, Antigravity CLI (agy) / Gemini CLI |
| Message broker | HTTP + SQLite (`relay-server.py`, port 7432) |
| Système de hooks | Hooks Claude Code (session / setup / stop) |
| Versionnage du framework | Git |
| Synchronisation données perso | MEGA / Nextcloud (ou tout service de sync) |
| Optimisation tokens | RTK — Rust Token Killer (optionnel) |

---

## Démarrage rapide

### Prérequis

- **Node.js 18+**
- **Claude Code CLI** et/ou **Antigravity CLI** (`agy`) / **Gemini CLI**, authentifiés
- **Git**

### Installation

```bash
git clone https://github.com/sonical0/relay.git
cd relay
claude .
```

Puis lancer le wizard d'initialisation :

```
/framework-init
```

Le wizard va :
1. Détecter votre OS et copier `relay-brain.template.yml` → `relay-brain.yml`
2. Installer les trois hooks Claude Code dans `~/.claude/settings.json`
3. Vérifier la disponibilité de wmux, RTK et Antigravity CLI
4. Afficher un récapitulatif de configuration

> La procédure de configuration manuelle complète est documentée dans [`SETUP.md`](SETUP.md).

---

## Structure du projet

```
relay/
  AGENTS.md                       Point d'entrée universel — lu en premier par tous les agents CLI
  CLAUDE.md                       Claude Code : @relay-brain-ref.md + capacités, limites
  ANTIGRAVITY.md                  Antigravity CLI (agy) : capacités, limites, sandbox
  GEMINI.md                       Alias compat Gemini CLI → @ANTIGRAVITY.md
  relay-bootstrap.md              Protocole d'entrée — 8 étapes obligatoires, chemins portables
  relay-brain.template.yml        Template live slim — copier en relay-brain.yml au setup
  relay-brain-ref.template.yml    Template référence statique (capacités agents, conventions)
  relay-brain-ref.md              Référence statique — @include dans CLAUDE.md
  relay-brain.yml                 Hub d'état actif — gitignored, synchronisé via cloud
  RELAY_LOG.md                    Journal d'activité partagé — cross-agents, cross-projets
  RELAY_CHAT.md                   Canal live inter-agents — gitignored, synchronisé via cloud

  PROJECTS/
    _template/                    Fichiers de départ pour un nouveau projet (committé)
    {nom-du-projet}/              Un répertoire par projet (cloud uniquement, gitignored)

  SKILLS/
    _template/SKILL.md            Template de skill vierge (committé)
    {nom-du-projet}/SKILL.md      Table de routage par projet (cloud uniquement, gitignored)

  TOOLS/
    relay-server.py               Message broker — HTTP + SQLite, port 7432
    relay-chat.mjs                CLI de chat live — send, poll, read, status
    relay-agent-loop.sh           Listener agent — poll broker, dispatch vers agy
    relay-server-start.sh         Démarrer le broker en arrière-plan (idempotent)
    init-relay-project.mjs        Initialiser un nouveau projet depuis le template

  .claude/hooks/
    relay-session-hook.js         UserPromptSubmit : injecter le contexte boot Relay
    relay-setup-hook.js           SessionStart : vérifier l'installation des hooks
    relay-stop-hook.js            Stop : vérifier que RELAY_LOG.md est à jour
```

---

## Modèle de synchronisation

Le framework sépare deux préoccupations : le **protocole** (partageable, stable, dans Git) et les **données** (personnelles, volatiles, dans le cloud).

| Contenu | Git | Sync cloud |
|---------|:---:|:----------:|
| AGENTS.md, CLAUDE.md, ANTIGRAVITY.md, GEMINI.md | ✅ | |
| relay-bootstrap.md, relay-brain.template.yml, relay-brain-ref.md | ✅ | |
| PROJECTS/_template/, SKILLS/_template/ | ✅ | |
| TOOLS/relay-*.*, TOOLS/init-relay-project.mjs | ✅ | |
| .claude/hooks/relay-*.js | ✅ | |
| relay-brain.yml (état actif) | | ✅ |
| RELAY_LOG.md (historique complet) | | ✅ |
| PROJECTS/{projet}/ (données du projet) | | ✅ |
| SKILLS/{projet}/SKILL.md | | ✅ |

Le dépôt Git ne contient **aucune donnée personnelle** et peut être cloné proprement sur n'importe quel poste.

---

## Le framework grandit avec vous

Relay est conçu pour accumuler de la valeur de manière organique au fil du travail.

- **Les skills** sont ajoutés dès qu'un workflow récurrent est formalisé — ad hoc la première fois, une entrée de skill à la troisième.
- **Les tools** suivent un protocole de promotion — un script rejoint `TOOLS/` uniquement après avoir prouvé son utilité sur au moins deux projets distincts.
- **Les nouveaux agents** s'intègrent sans rien casser — un nouveau fichier `{AGENT}.md` et une entrée dans `relay-brain.yml`.

Le framework grossit dans le cloud et reste léger dans Git.

---

## Ajouter un nouveau projet

```bash
node TOOLS/init-relay-project.mjs mon-projet
```

Crée `PROJECTS/mon-projet/` depuis le template et l'enregistre dans `relay-brain.yml`. Remplir ensuite `PROJECTS/mon-projet/AGENTS.md` pour définir la division des rôles et les conventions locales.

---

## Ajouter un nouvel agent CLI

1. Créer `{AGENT}.md` à la racine — commencer par `@./AGENTS.md`, puis déclarer les capacités et contraintes.
2. Ajouter une entrée sous `agents:` dans `relay-brain.yml`.
3. Créer optionnellement `.{agent}/commands/framework-init.{ext}` pour un wizard slash command.
4. Logger l'ajout dans `RELAY_LOG.md`.

Tout agent qui lit `AGENTS.md` et suit le protocole d'entrée est un participant de premier ordre.

---

## Exemple de configuration

| Rôle | Agent | Responsabilités |
|------|-------|----------------|
| Orchestrateur | Claude Code | Évaluation, génération PDF, merges, raisonnement long |
| Worker | Antigravity CLI (`agy`) | Scraping, traitement batch, vérifications liveness |

```
Hooks    : relay-session-hook.js (contexte boot) + relay-stop-hook.js (vérification log)
Broker   : relay-server.py sur le port 7432 — délégation via relay-chat.mjs
Sync     : relay-brain.yml + RELAY_LOG.md + PROJECTS/ synchronisés via cloud
Tokens   : proxy RTK — réduction 60-90% de l'output CLI via hook PreToolUse
```

---

## Optimisation des tokens

Relay est conçu pour être économe en tokens à chaque couche.

| Mécanisme | Économies | Comment |
|-----------|-----------|---------|
| **Proxy RTK** | 60–90% | Encapsule les commandes CLI (`git`, `npm`, `cargo`, `pytest`…) et supprime le bruit avant que l'output n'atteigne l'agent |
| **Split brain** | Significatives | `relay-brain-ref.md` est `@include` dans `CLAUDE.md` — le contenu statique est chargé une fois via le prompt caching et jamais relu entre les sessions |
| **Déduplication de session** | Par session | `relay-session-hook.js` exécute le protocole de boot une seule fois par fenêtre de 30 minutes, pas à chaque prompt |
| **Format log compact** | Cumulatif | `RELAY_LOG.md` impose max 5 bullets × 15 mots par entrée — le log reste dense au fil du temps |
| **Brain slim** | Par lecture | `relay-brain.yml` contient uniquement l'état volatile (projets, claims, wmux). Le contenu stable (capacités, conventions) vit dans `relay-brain-ref.md` et n'est jamais relu |

### Configuration RTK

[RTK (Rust Token Killer)](https://github.com/reachingforthejack/rtk) est un proxy CLI optionnel mais recommandé :

```bash
# Installer
cargo install rtk   # ou voir la doc RTK pour l'installation par binaire

# Vérifier
rtk --version
rtk gain            # voir les économies de tokens
```

Une fois installé, RTK est configuré comme hook `PreToolUse` dans `~/.claude/settings.json`. Le wizard `/framework-init` gère cette étape automatiquement.

---

## Contribuer

Les contributions sont les bienvenues. Ouvrir une issue pour discuter des changements avant de soumettre une pull request.

- Commits focalisés et descriptifs
- Aucun chemin absolu — tout doit se résoudre relativement à `RELAY_ROOT`
- Aucune donnée personnelle dans les fichiers commités

---

## Licence

[MIT](LICENSE) — © 2026 sonical0
