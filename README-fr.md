# Relay

Un framework de coordination portable pour les workflows multi-agents IA en ligne de commande.

[Read in English](README.md)

---

## Vue d'ensemble

Les agents IA en ligne de commande — Claude Code, Antigravity CLI (agy) et leurs semblables — sont individuellement capables de traiter des tâches complexes en plusieurs étapes. Le problème apparaît lorsqu'on en fait travailler plusieurs sur un même projet : aucune mémoire partagée, aucune conscience de ce que fait l'autre, aucun mécanisme pour diviser le travail proprement. Le contexte se perd entre les sessions. Le travail est dupliqué. Un agent défait ce que l'autre vient de faire.

Relay est une couche de coordination en étoile (hub-and-spoke) qui s'intercale entre vos agents et vos projets. À chaque démarrage, chaque agent lit le même point d'entrée, consulte le même fichier d'état partagé, ajoute une entrée dans le même journal d'activité, et suit le même protocole pour revendiquer une tâche avant de la commencer. Le résultat est un espace de travail multi-agents cohérent où Claude Code fait ce qu'il fait de mieux, Antigravity CLI fait ce qu'il fait de mieux, et aucun n'empiète sur le travail de l'autre.

Le framework est conçu autour d'une contrainte unique : il doit fonctionner partout. Aucun chemin absolu, aucune configuration spécifique à une machine committée dans le dépôt. Le squelette vit dans Git ; les données personnelles des projets vivent dans la synchronisation cloud. Un nouveau poste est opérationnel en trois commandes.

---

## Fonctionnement

Relay utilise une architecture en étoile. Le hub est le répertoire racine de Relay lui-même — un ensemble de fichiers partagés que tous les agents lisent avant de faire quoi que ce soit. Chaque projet est un spoke : un sous-répertoire dans `PROJECTS/` avec ses propres instructions, ses assignments de rôles et ses règles locales.

**AGENTS.md** est le point d'entrée universel. Chaque agent CLI, quel que soit son éditeur, lit ce fichier en premier. Il définit le protocole d'entrée, l'organisation des répertoires, les conventions partagées, et la procédure à suivre lors du premier lancement sur un nouveau poste. Les capacités et limites spécifiques à chaque agent se trouvent respectivement dans `CLAUDE.md` et `ANTIGRAVITY.md` — des wrappers minimalistes qui référencent `AGENTS.md` et y ajoutent ce qui est propre à cet agent.

**relay-brain.yml** est le hub d'état partagé. Il répertorie quels agents sont disponibles et ce qu'ils peuvent faire, quels projets sont enregistrés, et quelles tâches sont actuellement revendiquées. Avant de commencer une tâche longue, un agent écrit une entrée de claim ici. Avant de commencer n'importe quelle tâche, un agent vérifie les claims existants pour éviter les doublons.

**RELAY_LOG.md** est le journal d'activité partagé — un relevé continu de ce que chaque agent a fait sur tous les projets. Les agents y écrivent des résumés par batch (max 5 bullets, 15 mots chacun), pas des traces par fichier. C'est la surface de coordination principale entre agents opérant de manière asynchrone.

**SKILLS/** stocke les tables de routage de commandes par projet. Un fichier de skill définit les modes disponibles pour un projet, quels fichiers charger pour chaque mode, quand lancer des sous-agents, et comment détecter automatiquement l'intention depuis l'input utilisateur. Les skills s'accumulent avec le temps : dès qu'un workflow récurrent est formalisé, il devient réutilisable d'une session à l'autre.

**TOOLS/** contient les scripts utilitaires partagés — des modules Node.js ESM dont l'utilité a été prouvée sur au moins deux projets. Chaque outil expose une interface CLI standard (arguments, codes de sortie, output `--json` optionnel) sans chemins hardcodés.

---

## Stack

| Couche | Technologie |
|--------|-------------|
| État et configuration | YAML, Markdown |
| Scripts utilitaires | Node.js 18+ (ESM), Python 3.8+ |
| Intégration agents | Claude Code CLI, Antigravity CLI (agy) / Gemini CLI |
| Message broker | HTTP + SQLite (relay-server.py, port 7432) |
| Système de hooks | Hooks Claude Code (session / setup / stop) |
| Versionnage du framework | Git |
| Synchronisation données perso | MEGA / Nextcloud (ou tout service de sync) |
| Panel navigateur terminal | wmux (optionnel, Windows) |
| Proxy d'optimisation tokens | RTK — Rust Token Killer (optionnel) |

---

## Démarrage rapide

### Prérequis

- Node.js 18 ou supérieur
- Claude Code CLI et/ou Antigravity CLI (`agy`) / Gemini CLI, authentifiés
- Git

### Installation

```bash
git clone <repo-url> relay
cd relay
```

Ouvrir le framework avec votre agent CLI :

```bash
claude .    # Claude Code
agy         # Antigravity CLI (ou : gemini)
```

Puis lancer le wizard d'initialisation :

```
/framework-init
```

Le wizard détecte votre système d'exploitation, copie `relay-brain.template.yml` vers `relay-brain.yml`, installe les hooks Claude Code, configure les paramètres d'environnement des agents, propose l'installation de wmux (Windows) ou d'alternatives équivalentes (macOS, Linux), et propose d'installer et configurer RTK pour l'optimisation des tokens. La procédure de configuration manuelle complète est documentée dans `SETUP.md`.

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
  relay-brain-ref.md              Référence markdown statique — @include dans CLAUDE.md
  relay-brain.yml                 Hub d'état actif — gitignored, synchronisé via cloud
  RELAY_LOG.md                    Journal d'activité partagé — cross-agents, cross-projets
  RELAY_CHAT.md                   Canal live inter-agents — gitignored, synchronisé via cloud
  SETUP.md                        Guide d'installation sur un nouveau poste
  README.md                       Version anglaise
  README-fr.md                    Ce fichier

  PROJECTS/
    _template/                    Fichiers de départ pour un nouveau projet (committé)
      AGENTS.md                   Règles du projet : division des rôles, structure, conventions
      CLAUDE.md                   Hérite de ../../CLAUDE.md
      GEMINI.md                   Hérite de ../../ANTIGRAVITY.md
      .relay-bootstrap.md         Overrides locaux pointant vers ../../relay-bootstrap.md
      RELAY_LOG.md                Stub — l'activité va dans le log global
    {nom-du-projet}/              Un répertoire par projet (cloud uniquement, gitignored)

  SKILLS/
    _template/SKILL.md            Template de skill vierge (committé)
    {nom-du-projet}/SKILL.md      Table de routage par projet (cloud uniquement, gitignored)

  TOOLS/
    README.md                     Catalogue d'outils et contrat d'interface (committé)
    relay-server.py               Message broker — HTTP + SQLite, port 7432
    relay-chat.mjs                CLI de chat live — send, poll, read, status
    relay-agent-loop.sh           Listener agent — poll broker, dispatch vers agy
    relay-server-start.sh         Démarrer le broker en arrière-plan
    init-relay-project.mjs        Initialiser un nouveau projet
    html-to-pdf-generator.mjs     HTML → PDF (cloud uniquement, gitignored)
    fix-mojibake.mjs              Correction encodage UTF-8 (cloud uniquement, gitignored)

  .claude/hooks/
    relay-session-hook.js         UserPromptSubmit : injecter le contexte boot Relay
    relay-setup-hook.js           SessionStart : vérifier l'installation des hooks
    relay-stop-hook.js            Stop : vérifier que RELAY_LOG.md est à jour
  .claude/commands/
    framework-init.md             Commande /framework-init pour Claude Code
  .gemini/commands/
    framework-init.toml           Commande /framework-init pour Antigravity/Gemini CLI
```

---

## Modèle de synchronisation

Le framework sépare deux préoccupations : le protocole (agnostique aux agents, partageable, stable) et les données (personnelles, volatiles, couvrant plusieurs postes).

| Contenu | Git | Sync cloud |
|---------|-----|------------|
| AGENTS.md, CLAUDE.md, ANTIGRAVITY.md, GEMINI.md | oui | — |
| relay-bootstrap.md, relay-brain.template.yml, relay-brain-ref.template.yml | oui | — |
| relay-brain-ref.md | oui | — |
| PROJECTS/_template/, SKILLS/_template/ | oui | — |
| TOOLS/README.md, TOOLS/relay-*.*, TOOLS/init-relay-project.mjs | oui | — |
| .claude/hooks/relay-*.js | oui | — |
| .gitignore, SETUP.md, README.md | oui | — |
| relay-brain.yml (actif, avec données projets) | non | oui |
| RELAY_LOG.md (historique complet) | non | oui |
| RELAY_CHAT.md (canal live) | non | oui |
| PROJECTS/{projet}/ (données du projet) | non | oui |
| SKILLS/{projet}/SKILL.md | non | oui |

Le dépôt Git ne contient aucune donnée personnelle et peut être partagé publiquement ou cloné proprement. La couche synchronisée via cloud porte l'état vivant : projets actifs, skills accumulés, scripts utilitaires, et le journal d'activité complet.

---

## Le framework grandit avec vous

Relay n'est pas configuré une fois pour toutes. Il est conçu pour accumuler de la valeur de manière organique au fil du travail.

Les skills sont ajoutés dès qu'un workflow récurrent est formalisé : la première fois qu'une séquence de commandes est exécutée, c'est ad hoc ; la troisième fois, elle devient une entrée de skill. Les tools suivent un protocole de promotion — un script rejoint `TOOLS/` uniquement après avoir prouvé son utilité sur au moins deux projets distincts. Le journal d'activité grossit à chaque session, donnant aux agents qui arrivent à froid une image immédiate de l'activité récente cross-projets. Le fichier brain gagne des projets un par un, améliorant la capacité de chaque agent à évaluer le contexte et à éviter les doublons.

Les nouveaux agents CLI s'intègrent sans rien casser. Ajouter Codex CLI ou un futur agent signifie créer un nouveau fichier (`{AGENT}.md`) à la racine de relay, une entrée dans `relay-brain.yml`, et optionnellement une définition de slash command. Le protocole d'entrée universel dans `AGENTS.md` gère le reste — le nouvel agent hérite du framework de coordination complet dès son premier lancement.

Le framework grossit dans le cloud et reste léger dans Git.

---

## Ajouter un nouveau projet

```bash
node TOOLS/init-relay-project.mjs mon-projet
```

Cette commande crée `PROJECTS/mon-projet/` depuis le template et enregistre le projet dans `relay-brain.yml`. Remplir ensuite `PROJECTS/mon-projet/AGENTS.md` pour définir la division des rôles, la structure des fichiers et les conventions locales. Ajouter un fichier skill dans `SKILLS/mon-projet/SKILL.md` si le projet a des modes de commandes récurrents.

Alternative manuelle : copier `PROJECTS/_template/` vers `PROJECTS/{nom}/`, adapter les fichiers, et ajouter l'entrée du projet dans `relay-brain.yml` manuellement.

---

## Ajouter un nouvel agent CLI

1. Créer `{AGENT}.md` à la racine de relay — commencer par `@./AGENTS.md`, puis déclarer les capacités et contraintes spécifiques à cet agent.
2. Ajouter une entrée sous `agents:` dans `relay-brain.yml`.
3. Si l'agent supporte les slash commands personnalisés, créer `.{agent}/commands/framework-init.{ext}` en reprenant la même logique de wizard que les versions Claude et Antigravity existantes.
4. Logger l'ajout dans `RELAY_LOG.md`.

Le framework ne fait aucune hypothèse sur l'agent présent. Tout agent qui lit `AGENTS.md` et suit le protocole d'entrée est un participant de premier ordre.

---

## Exemple de configuration

Une configuration Relay typique ressemble à ceci :

- **Orchestrateur** : Claude Code — évaluation, génération PDF, merges, raisonnement long
- **Worker** : Antigravity CLI (`agy`) — scraping, traitement batch, vérifications liveness
- **Hooks** : `relay-session-hook.js` (contexte boot), `relay-stop-hook.js` (vérification log)
- **Broker** : `relay-server.py` sur le port 7432 — délégation de tâches en temps réel via `relay-chat.mjs`
- **Sync** : données personnelles (`relay-brain.yml`, `RELAY_LOG.md`, `PROJECTS/`) synchronisées via service cloud
- **RTK** : proxy tokens réduisant l'output CLI de 60-90%, configuré via hook PreToolUse
