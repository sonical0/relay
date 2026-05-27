Lance le wizard d'initialisation du Relay pour un nouveau poste.

## Étapes du wizard

### Étape 1 — Détecter l'OS

Détecter l'OS en cours :
- Windows : `$env:OS` contient "Windows" ou PowerShell est disponible
- macOS : `uname -s` retourne `Darwin`
- Linux : `uname -s` retourne `Linux`

Confirmer à l'utilisateur : "OS détecté : [Windows/macOS/Linux]"

---

### Étape 2 — Configurer tandem-brain.yml

Vérifier si `./tandem-brain.yml` existe.

Si **absent** : copier `./tandem-brain.template.yml` → `./tandem-brain.yml`
```powershell
# Windows
Copy-Item tandem-brain.template.yml tandem-brain.yml
```
```bash
# macOS / Linux
cp tandem-brain.template.yml tandem-brain.yml
```

Configurer dans `tandem-brain.yml` :
- `agents.claude.env` : `windows-powershell` | `macos-zsh` | `linux-bash` (selon OS détecté)
- `agents.gemini.env` : idem
- `last_updated` : date du jour (YYYY-MM-DD)
- `last_agent` : claude

---

### Étape 3 — Configurer wmux

**Windows** :
1. Tenter `wmux ping`
2. Si succès → mettre `wmux_status: operational` dans tandem-brain.yml. Confirmer à l'utilisateur.
3. Si échec → expliquer :
   > "wmux est un panel navigateur visible en temps réel, intégré au terminal. Il permet aux agents CLI de naviguer sur le web pendant que tu vois ce qui se passe en direct. Sur Windows, il s'intègre nativement avec Claude Code et Gemini CLI."
   Proposer l'installation. Si accepté : guider l'installation (chercher la documentation wmux). Si refusé : mettre `wmux_status: unavailable`.

**macOS** :
> "wmux n'est pas disponible nativement sur macOS. Des alternatives existent (Warp, iTerm2 + AppleScript, etc.) mais ne sont pas encore intégrées officiellement. Veux-tu qu'on explore les options disponibles sur ton poste ?"
Si oui : rechercher et proposer des alternatives. Si non : mettre `wmux_status: unavailable`.

**Linux** :
> "wmux n'est pas disponible nativement sur Linux. Des alternatives comme tmux + xdotool sont possibles mais non intégrées officiellement. Veux-tu qu'on explore les options ?"
Si oui : rechercher et proposer. Si non : mettre `wmux_status: unavailable`.

---

### Étape 4 — Vérifier l'accessibilité des dossiers

Vérifier que ces dossiers existent et sont accessibles :
- `./PROJECTS/` — lister les projets disponibles (depuis cloud sync si présents)
- `./SKILLS/` — lister les skills disponibles
- `./TOOLS/` — lister les scripts disponibles (*.mjs)

Informer l'utilisateur de ce qui est disponible. Si les dossiers sont vides (pas encore synchronisés depuis le cloud) : l'indiquer et proposer de créer un premier projet avec `node TOOLS/init-tandem-project.mjs {nom}` (si init-tandem-project.mjs est disponible).

---

### Étape 5 — RTK (Rust Token Killer)

Vérifier : `rtk --version`

Si **absent** : présenter RTK à l'utilisateur :
> "RTK (Rust Token Killer) est un proxy CLI transparent qui réduit la consommation de tokens de 60 à 90% sur les opérations courantes comme git, npm, etc. Il fonctionne via un hook PreToolUse — par exemple, quand tu tapes `git status`, Claude Code exécute automatiquement `rtk git status` à la place, sans que tu aies à changer quoi que ce soit. Les économies sont visibles via `rtk gain`."

Détecter quels CLI agents sont installés :
- `claude --version` → Claude Code présent ?
- `gemini --version` → Gemini CLI présent ?

Proposer : "Veux-tu installer RTK et configurer le hook pour [liste des CLI détectés] ?"

Si **accepté** : guider l'installation de RTK, puis ajouter le hook PreToolUse dans `~/.claude/settings.json` (et équivalent Gemini si applicable). Mettre `rtk_status: operational` dans tandem-brain.yml.

Si **refusé** : mettre `rtk_status: declined` dans tandem-brain.yml et continuer.

Si **déjà installé** : confirmer, mettre `rtk_status: operational` dans tandem-brain.yml.

---

### Étape 6 — Résumé et log

Afficher un résumé de la configuration :

```
Relay — Configuration terminée
======================================
OS           : [détecté]
wmux         : [operational / unavailable]
RTK          : [operational / declined / non installé]

Projets disponibles    : [liste depuis PROJECTS/ ou "aucun"]
Skills disponibles     : [liste depuis SKILLS/ ou "aucun"]
Scripts disponibles    : [liste depuis TOOLS/ ou "aucun"]

Commandes disponibles  :
  /framework-init   — Relancer ce wizard
```

Écrire dans `./TANDEM_LOG.md` :
```
{date} [claude] framework — Setup initial terminé. OS: [OS], wmux: [status], RTK: [status]
```
