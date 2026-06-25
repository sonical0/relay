# SETUP.md — Relay sur un nouveau poste

## Prérequis

- **Node.js 18+** — pour les scripts dans `TOOLS/`
- **Git** — pour cloner/synchroniser le framework
- **Claude Code CLI** — [claude.ai/code](https://claude.ai/code)
- **Antigravity CLI** (`agy`) ou **Gemini CLI** (optionnel) — [github.com/google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli)
- **Python 3.8+** (optionnel) — pour le message broker `relay-server.py`
- **Cloud sync** (MEGA, Nextcloud, etc.) — pour les données personnelles dans `PROJECTS/`, `SKILLS/`

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
git status   # doit afficher "On branch main"
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
- Détecter l'OS et configurer `relay-brain.yml`
- Installer les hooks Claude Code (`relay-session-hook.js`, `relay-setup-hook.js`, `relay-stop-hook.js`)
- Proposer wmux (Windows) ou des alternatives (macOS/Linux)
- Proposer RTK (optimisation tokens)
- Vérifier que antigravity-cli (`agy`) ou gemini-cli est disponible

---

## 3. Configuration manuelle (alternative au wizard)

### 3a. Copier le brain template
```bash
# macOS / Linux
cp relay-brain.template.yml relay-brain.yml

# PowerShell (Windows)
Copy-Item relay-brain.template.yml relay-brain.yml
```

### 3b. Adapter relay-brain.yml
Ouvrir `relay-brain.yml` et modifier :
- `agents.claude.env` : `windows-powershell` | `macos-zsh` | `linux-bash`
- `agents.antigravity.env` : idem
- `agents.antigravity.installed` : `true` si antigravity/agy est installé
- `wmux_status` : `unknown` (configurer après install wmux)

---

## 4. Hooks Claude Code

Les hooks permettent à Claude Code d'exécuter automatiquement le protocole d'entrée Relay et de vérifier RELAY_LOG.md en fin de session.

### Installation (idempotente)
```bash
node .claude/hooks/relay-setup-hook.js
```

Ce script modifie `~/.claude/settings.json` et installe 3 hooks :

| Hook | Événement | Rôle |
|------|-----------|------|
| `relay-session-hook.js` | `UserPromptSubmit` | Boot context : injecte le protocole d'entrée Relay à chaque nouvelle session |
| `relay-setup-hook.js` | `SessionStart` | Auto-vérifie que les 3 hooks sont présents (ré-installe si nécessaire) |
| `relay-stop-hook.js` | `Stop` | Vérifie que RELAY_LOG.md est à jour avant de terminer la session |

**Relancer Claude Code** après l'installation pour activer les hooks.

---

## 5. wmux — navigateur visible dans le terminal

wmux intègre un panel navigateur en temps réel au terminal, visible pendant le travail des agents CLI.

### Windows (supporté)
```bash
wmux ping   # tester si installé
```
Si absent, le wizard `/framework-init` propose l'installation automatiquement.

### macOS / Linux (alternatives)
wmux n'est pas disponible nativement. Alternatives :
- **tmux** — déjà utilisé par `relay-agent-loop.sh`
- **iTerm2 / Warp** (macOS) — fonctionnalités browser-like

---

## 6. RTK — optimisation tokens (optionnel, recommandé)

**RTK (Rust Token Killer)** est un proxy CLI transparent qui réduit la consommation de tokens de 60-90%.

```bash
rtk --version   # vérifier si installé
rtk gain        # voir les économies réalisées
```

La configuration RTK (hook PreToolUse) est dans `~/.claude/settings.json` — **locale au poste**.

---

## 7. Message broker temps réel (optionnel)

Permet la communication asynchrone entre Claude et antigravity/agy.

### Démarrer le serveur
```bash
bash TOOLS/relay-server-start.sh   # arrière-plan, port 7432
```

### Utiliser le CLI chat
```bash
node TOOLS/relay-chat.mjs status
node TOOLS/relay-chat.mjs send claude --to agy "Lance un scan"
node TOOLS/relay-chat.mjs poll agy
```

### Agent loop (Linux, tmux requis)
```bash
bash TOOLS/relay-agent-loop.sh   # démarre automatiquement via relay-session-hook.js
```

---

## 8. Vault Obsidian (optionnel avancé)

Relay peut s'intégrer avec Obsidian pour une documentation structurée cross-agents.
Voir `SKILLS/obsidian/` pour les outils disponibles.

---

## Vérification post-setup

Ouvrir Claude Code dans relay :
```bash
claude .
```

Demander à l'agent :
> "Lis relay-brain.yml et dis-moi tes capacités."

Réponse attendue : l'agent lit `./relay-brain.yml`, rapporte les capacités Claude/antigravity, confirme que les chemins relatifs fonctionnent.

---

## Tableau Git vs Cloud

| Contenu | Git | Cloud |
|---------|-----|-------|
| AGENTS.md, CLAUDE.md, ANTIGRAVITY.md, GEMINI.md | ✅ | — |
| relay-bootstrap.md, relay-brain.template.yml, relay-brain-ref.template.yml | ✅ | — |
| relay-brain-ref.md | ✅ | — |
| SETUP.md, README*.md, .gitignore | ✅ | — |
| PROJECTS/_template/, SKILLS/_template/ | ✅ | — |
| TOOLS/README.md, TOOLS/relay-*.*, TOOLS/init-relay-project.mjs | ✅ | — |
| .claude/hooks/relay-*.js | ✅ | — |
| .claude/commands/framework-init.md | ✅ | — |
| relay-brain.yml (actif, données perso) | ❌ | ✅ |
| RELAY_LOG.md (historique complet) | ❌ | ✅ |
| RELAY_CHAT.md (canal live) | ❌ | ✅ |
| PROJECTS/{projet réel}/ | ❌ | ✅ |
| SKILLS/{projet réel}/SKILL.md | ❌ | ✅ |
