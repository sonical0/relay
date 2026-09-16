# Relay

Un framework de coordination portable pour agents CLI multiples, construit autour
d'une seule contrainte : **le contexte est le budget**.

[Read in English](README.md)

---

## Vue d'ensemble

Les agents CLI — Claude Code, Gemini CLI et leurs pairs — traitent chacun très bien
des tâches complexes en plusieurs étapes. Le problème apparaît quand plusieurs d'entre
eux travaillent sur les mêmes projets pendant des mois : aucune mémoire partagée,
aucune idée de ce que l'autre a fait, aucun moyen économique d'arriver à froid et de
savoir où en sont les choses.

La correction évidente consiste à faire lire l'état partagé à chaque démarrage. C'est
ce que faisait la première version de Relay, et c'est la mauvaise correction : un
protocole d'entrée obligatoire « lis le brain, lis le log » coûtait 12 000 à 15 000
tokens par session avant le moindre travail, et grossissait à chaque fois que le
framework grossissait.

Relay fait désormais l'inverse. Un hook injecte un **état pré-digéré plafonné à
1,4 Ko, une seule fois par session**, choisi selon le dossier où l'on se trouve. Tout
le reste est à la demande, via un index de recherche local plutôt qu'en ouvrant des
fichiers. Le statut des projets est dérivé du disque au lieu d'être tenu à la main :
il ne peut donc pas se périmer en silence. Ce que l'agent lit, c'est la réponse, pas
le corpus.

---

## Fonctionnement

**`AGENTS.md` est la seule source des règles.** Une règle a exactement un fichier
propriétaire ; toute duplication est un bug. Les capacités propres à chaque agent
vivent dans `CLAUDE.md` ou `GEMINI.md`, fichiers minces qui en héritent. Aucun second
document ne redit le protocole — c'est précisément ce qu'avait la version précédente,
à trois endroits, et ils avaient divergé.

**L'état est injecté, pas cherché.** `TOOLS/relay-state.mjs` construit
`.relay-state.md` (vue générale) et un `.relay-state/<projet>.md` par projet : statut
courant, dernières sessions *sur ce projet*, fichiers de règles locales existants,
skills à la demande adaptées à ce que contient le projet. Un hook `UserPromptSubmit`
en choisit un selon le `cwd` et l'injecte une fois par session, indexé sur le
session_id.

**Le statut est dérivé, l'intention est déclarée.** `relay-brain.yml` est manuel et
porte l'intention humaine — un projet est `delivered` ou `abandoned` parce que vous
l'avez dit. `TOOLS/project-scan.mjs` dérive active / idle / dormant du dernier commit
git ou du mtime le plus récent et écrit `relay-projects.derived.yml`. Les deux
fichiers ne peuvent pas se contredire en silence : déclarez un projet terminé alors
qu'il bouge encore, et le scan signale la contradiction.

**Chercher avant de lire.** `TOOLS/rag-index.mjs` indexe chaque section markdown et
chaque bloc YAML dans un index BM25 ; `rag-query.mjs` y répond. JavaScript pur, aucune
dépendance native, donc un clone tourne partout. Utiliser `--full` quand l'extrait ne
suffit pas : un extrait qui oblige à ouvrir le fichier derrière coûte plus cher que
pas d'index du tout.

**Les skills ont un budget.** Le `description:` d'une skill entre dans le prompt
système à chaque session, à environ 0,37 token par octet. D'où deux modes de
chargement : `native` (liée dans `.claude/skills/`, se déclenche seule, payée à chaque
session) ou `rag` (absente du prompt, trouvée via l'index, lue à la demande). Le
générateur d'état alerte au-delà de 4 000 octets de descriptions liées.

---

## Ce qui a été mesuré

Tout ce qui précède vient de la mesure de la version d'avant. Les chiffres proviennent
du système interne dont Relay est la forme publique :

| Constat | Mesure |
|---|---|
| Le tour de conversation est l'unité coûteuse | 4 000 à 11 000 tokens par tour, contre 614 pour toute la consolidation du contexte |
| Travailler depuis le dossier du projet bat la racine | Même question : 3 tours / 96 665 tokens depuis la racine, **1 tour / 25 728 tokens** depuis le projet |
| Un ordre au démarrage coûte plus que ce qu'il injecte | ~600 tokens injectés, 12-15 k tokens de lectures provoquées |
| Les descriptions de skills sont un coût récurrent | ~0,37 token par octet, à chaque session, que la skill serve ou non |
| Le contexte dérive quand personne ne regarde | La chaîne `@include` avait atteint 21 Ko sans que personne ne le voie |
| Une skill de routage ne s'auto-déclenche pas | 0 déclenchement sur 3 projets — retirée le jour même |

Les deux derniers justifient la règle qui compte le plus : **avant d'ajouter quoi que
ce soit au contexte chargé à chaque session, mesurer.**

---

## Démarrage rapide

```bash
git clone <repo-url> relay && cd relay
cp relay-brain.template.yml relay-brain.yml
npm run bootstrap
npm run hooks
```

Ajouter ensuite votre machine et vos projets dans `relay-brain.yml`, puis lancer
`npm run state`. Procédure complète, permissions comprises : [`SETUP.md`](SETUP.md).

| Commande | Effet |
|---|---|
| `npm run bootstrap` | Installe, lie les skills, dérive les statuts, construit état et index |
| `npm run hooks` | Installe les trois hooks dans la config de l'agent |
| `npm run state` | Rafraîchit les statuts dérivés et l'état injecté |
| `npm run index` | Reconstruit l'index de recherche |
| `npm run rag -- --query "…"` | L'interroge |
| `npm run rotate` | Archive les entrées de log de plus de deux mois |

`npm run hooks` sauvegarde vos réglages avant d'y toucher, n'ajoute que les hooks —
thème, permissions et hooks existants sont préservés — refuse un `settings.json`
invalide plutôt que de l'écraser, et se relance sans risque. **La configuration des
hooks est locale à chaque poste** : ni git ni la synchro cloud ne la transportent, pas
plus que les jonctions NTFS — c'est aussi pourquoi `skills-link.mjs` doit tourner sur
chaque machine. Si vous faites tourner deux profils (un `CLAUDE_CONFIG_DIR` perso et
un pro), `RELAY_PROPAGATE_HOOKS=1` copie les hooks — et rien qu'eux — vers l'autre ;
désactivé par défaut, parce qu'écraser la clé `hooks` d'un profil que l'utilisateur
n'a pas nommé est une chose surprenante de la part d'un script d'installation.

---

## Arborescence

```
relay/
  AGENTS.md                  Seule source des règles — lue en premier par tout agent
  CLAUDE.md / GEMINI.md      Capacités et limites par agent (minces)
  relay-brain.template.yml   Machines, agents, projets, intention — à copier
  RELAY_LOG.md               Journal partagé, plus récent en haut, rotation mensuelle
  relay-session-hook.js      UserPromptSubmit — injecte l'état une fois par session
  relay-setup-hook.js        Installe les hooks ; les revérifie au SessionStart
  relay-stop-hook.js         Rafraîchit les statuts en arrière-plan en fin de session

  TOOLS/                     Committés : le framework ne fonctionne pas sans eux
    project-scan.mjs         Dérive le statut des projets depuis le disque
    relay-state.mjs          Construit l'état injecté
    rag-index.mjs            Construit l'index BM25
    rag-query.mjs            L'interroge
    skills-link.mjs          Lie les skills natives dans .claude/skills/
    skills-all-native.mjs    Retour arrière : tout en natif
    log-rotate.mjs           Archive les vieilles entrées de log
    new-project.mjs          Crée et déclare un projet
    lib/atomic.mjs           Écriture atomique (retry sur verrou Windows)
    lib/rag.mjs              Tokenizer et BM25

  PROJECTS/_template/        Fichiers de départ d'un projet
  SKILLS/skills.manifest.json  Quelles skills existent et comment chacune se charge
  SKILLS/_template/SKILL.md  Skill de départ

  générés, jamais committés :
  relay-projects.derived.yml  .relay-state.md  .relay-state/  .relay-rag.json
```

---

## Règles de conception

Elles sont porteuses. Chacune existe parce que son contraire a été essayé.

- **Un propriétaire par règle.** Trois documents décrivant le même protocole
  divergeront.
- **Généré et manuel ne se mélangent pas.** Un fichier est écrit par un script ou par
  un humain, jamais les deux. Le fait mesuré va dans les fichiers générés,
  l'intention reste manuelle.
- **Écrire atomiquement.** `lib/atomic.mjs` : temporaire suffixé par le PID, retry sur
  verrou Windows. Sans ça, environ une exécution sur six sortait en `EPERM` pendant
  que la synchro cloud tenait le fichier.
- **Valider avant la bascule.** Le temporaire est relu et parsé ; un résultat invalide
  ne remplace jamais un original valide.
- **Aucun chemin absolu.** `RELAY_ROOT` se résout depuis l'emplacement du script.
- **Comparer les deux formes d'un chemin.** Sous Windows, le même dossier arrive tantôt
  en forme courte 8.3 (`C:\Users\JOHNDO~1\…`), tantôt en forme longue, et la casse ne
  distingue rien alors que `path.resolve()` la conserve. Le hook compare les deux
  formes, en minuscules : sans ça l'état par projet n'est jamais choisi, et l'échec est
  silencieux — on croit que le mécanisme marche, il sert la vue générale.
- **Mesurer avant d'ajouter.** Le contexte chargé à chaque session est la seule chose
  qui grossit sans que personne ait décidé de la faire grossir.

---

## Ajouter un agent CLI

1. Créer `{AGENT}.md` à la racine — inclure `AGENTS.md`, puis déclarer capacités et
   limites.
2. Ajouter une entrée sous `agents:` dans `relay-brain.yml`.
3. Si le CLI gère les slash commands, ajouter
   `.{agent}/commands/framework-init.{ext}`.
4. Le consigner dans `RELAY_LOG.md`.

Relay ne présuppose rien des agents présents. Tout agent qui lit `AGENTS.md` est un
participant de plein droit. Les agents non-Claude ne lisent pas `.claude/skills/` :
leur indiquer `SKILLS/<nom>/SKILL.md` par son chemin.

---

## Ce qui a été retiré, et pourquoi

Conservé ici parce qu'un framework qui ne documente que ses ajouts n'apprend rien.

- **`claims[]`** — protocole de réservation de tâches dans le brain. Aucun écrivain en
  quatre mois. La coordination passait par le log, que les agents lisaient vraiment.
- **`needs_bootstrap_sync`** — drapeau de propagation des changements de bootstrap.
  Personne ne l'a jamais lu.
- **`tandem-bootstrap.md`** — seconde copie du protocole d'entrée, fusionnée dans
  `AGENTS.md`.
- **Le protocole d'entrée obligatoire en 7 étapes** — remplacé par l'état injecté.
- **Le scope des skills par projet** — `git status -uall` suit les jonctions, donc un
  `git add .` aurait committé une copie divergente de chaque skill dans chaque dépôt.
- **Une skill de routage vers les skills à la demande** — ne s'est jamais
  auto-déclenchée. La suggestion voyage désormais dans l'état du projet, qui arrive à
  coup sûr.

---

## Licence

MIT.
