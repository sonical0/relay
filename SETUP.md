# SETUP.md — Relay sur un nouveau poste

## Prérequis

- **Node.js 18+** — pour les scripts dans `TOOLS/`
- **Git** — pour cloner/synchroniser le framework
- **Claude Code CLI** — [claude.ai/code](https://claude.ai/code)
- **Gemini CLI** (optionnel) — [github.com/google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli)
- **Cloud sync** (MEGA, Nextcloud, etc.) — pour les données personnelles dans `PROJECTS/`, `SKILLS/`, `TOOLS/`

---

## 1. Cloner / synchroniser

### Option A : Git uniquement (framework sans données personnelles)
```bash
git clone <repo-url> relay
cd relay
```

### Option B : Cloud sync (données personnelles incluses)
Laisser MEGA/Nextcloud synchroniser le dossier complet, puis vérifier que git est initialisé :
```bash
cd relay
git status   # doit afficher "On branch main" (ou "master")
```

---

## 2. Lancer le wizard d'initialisation

Ouvrir Claude Code dans le dossier relay :
```bash
claude .
```

Puis taper :
```
/framework-init
```

Le wizard va automatiquement :
- Détecter l'OS et configurer `tandem-brain.yml`
- Proposer wmux (Windows) ou des alternatives (macOS/Linux)
- Proposer RTK (optimisation tokens)
- Vérifier que les dossiers sont accessibles
- Fournir un résumé de la configuration

---

## 3. Configuration manuelle (alternative au wizard)

### 3a. Copier le template du brain
```powershell
# PowerShell (Windows)
Copy-Item tandem-brain.template.yml tandem-brain.yml
```
```bash
# macOS / Linux
cp tandem-brain.template.yml tandem-brain.yml
```

### 3b. Adapter tandem-brain.yml
Ouvrir `tandem-brain.yml` et modifier :
- `agents.claude.env` : `windows-powershell` | `macos-zsh` | `linux-bash`
- `agents.gemini.env` : idem
- `wmux_status` : `unknown` (configurer après install wmux)

---

## wmux — navigateur visible dans le terminal

wmux intègre un panel navigateur en temps réel au terminal, visible pendant le travail des agents CLI.

### Windows (supporté)
```bash
wmux ping   # tester si installé
```
Si absent, le wizard `/framework-init` propose l'installation automatiquement.
Installation manuelle : [lien à compléter lors du premier setup Windows]

### macOS (alternatives à explorer)
wmux n'est pas disponible nativement sur macOS. Alternatives potentielles :
- **Warp** (terminal avec IA intégrée) — fonctionnalités partiellement similaires
- **iTerm2 + AppleScript** — automation browser externe possible
- D'autres alternatives seront documentées lors de la première session macOS

Le wizard `/framework-init` propose de rechercher des alternatives adaptées à ton environnement.

### Linux (alternatives à explorer)
wmux n'est pas disponible nativement sur Linux. Alternatives potentielles :
- **tmux + xdotool** — automation browser externe
- D'autres alternatives seront documentées lors de la première session Linux

---

## RTK — optimisation tokens (optionnel, recommandé)

**RTK (Rust Token Killer)** est un proxy CLI transparent qui réduit la consommation de tokens de 60-90% sur les opérations courantes (git, npm, etc.).

Fonctionnement : un hook PreToolUse intercepte les commandes et les route via RTK.
Exemple : `git status` → `rtk git status` (transparent, 0 overhead visible).

```bash
rtk --version   # vérifier si installé
rtk gain        # voir les économies réalisées
```

Le wizard `/framework-init` propose l'installation automatiquement si RTK n'est pas détecté.

La configuration RTK (hook PreToolUse) est dans `~/.claude/settings.json` — **locale au poste**, jamais dans le framework git.

---

## Variable d'environnement TANDEM_ROOT (optionnel)

Le framework résout ses chemins de manière relative — `$TANDEM_ROOT` n'est pas requis mais peut être utile dans des scripts ou hooks personnalisés.

```powershell
# PowerShell (Windows) — ajouter dans ton profil $PROFILE
$env:TANDEM_ROOT = "C:\chemin\vers\relay"
```
```bash
# macOS / Linux — ajouter dans ~/.zshrc ou ~/.bashrc
export TANDEM_ROOT="/chemin/vers/relay"
```

---

## Vérification post-setup

Ouvrir Claude Code dans relay :
```bash
claude .
```

Demander à l'agent :
> "Lis tandem-brain.yml et dis-moi tes capacités."

Réponse attendue : l'agent lit `./tandem-brain.yml`, rapporte les capacités Claude/Gemini, confirme que les chemins relatifs fonctionnent.

---

## Récupérer des données personnelles (PROJECTS, SKILLS, TOOLS)

Les données personnelles (projets, skill files, scripts) ne sont **pas** dans le repo git — elles sont synchronisées via cloud.

Sur un nouveau poste sans accès cloud immédiat :
```bash
# Créer un nouveau projet vide
/framework-init   # puis suivre les étapes

# Ou directement via le script (si TOOLS/ scripts disponibles depuis le cloud)
node TOOLS/init-tandem-project.mjs mon-projet
```

---

## Tableau Git vs Cloud

| Contenu | Git | Cloud |
|---------|-----|-------|
| AGENTS.md, CLAUDE.md, GEMINI.md | ✅ | — |
| tandem-bootstrap.md, tandem-brain.template.yml | ✅ | — |
| SETUP.md, README.md, .gitignore | ✅ | — |
| PROJECTS/_template/, SKILLS/_template/ | ✅ | — |
| TOOLS/README.md | ✅ | — |
| tandem-brain.yml (actif, données perso) | ❌ | ✅ |
| TANDEM_LOG.md (historique complet) | ❌ | ✅ |
| PROJECTS/*/ (données projets) | ❌ | ✅ |
| TOOLS/*.mjs (scripts) | ❌ | ✅ |
| SKILLS/**/SKILL.md (réels) | ❌ | ✅ |
| ~/.claude/settings.json (hooks wmux, RTK) | ❌ | optionnel |
