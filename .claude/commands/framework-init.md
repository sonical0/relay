# /framework-init — Wizard d'initialisation du framework Relay

Ce wizard configure Relay sur un nouveau poste. Il détecte l'OS, copie les templates, propose les outils optionnels, et prépare le framework pour une première session.

## Instructions pour Claude Code

Quand l'utilisateur tape `/framework-init`, exécuter les étapes suivantes dans l'ordre :

---

### Étape 1 — Détecter l'environnement

Exécuter les commandes suivantes et noter les résultats :

```bash
node --version      # Vérifier Node.js 18+
python3 --version   # Vérifier Python 3.8+ (pour relay-server.py)
git --version       # Vérifier Git
uname -s            # Détecter OS (Linux, Darwin, MINGW = Windows)
```

Si Node.js < 18 → signaler et s'arrêter. Le framework requiert Node.js 18+.

---

### Étape 2 — Copier le brain template

Vérifier si `relay-brain.yml` existe. Si absent :

```bash
cp relay-brain.template.yml relay-brain.yml
```

Ouvrir `relay-brain.yml` et adapter les champs suivants selon l'OS détecté :
- `agents.claude.env` : `linux-bash` | `macos-zsh` | `windows-powershell`
- `agents.antigravity.env` : même valeur
- `last_updated` : date du jour (YYYY-MM-DD)

---

### Étape 3 — Installer les hooks Claude Code

```bash
node .claude/hooks/relay-setup-hook.js
```

Ce script est idempotent. Il installe (ou vérifie) les 3 hooks dans `~/.claude/settings.json` :
- `UserPromptSubmit` → `relay-session-hook.js` (boot context au démarrage)
- `SessionStart` → `relay-setup-hook.js` (auto-vérification)
- `Stop` → `relay-stop-hook.js` (vérifie que RELAY_LOG.md est à jour)

Si des hooks ont été ajoutés → demander à l'utilisateur de relancer Claude Code pour les activer.

---

### Étape 4 — wmux (optionnel, Windows principalement)

Tester : `wmux ping`

**Windows** : si absent → proposer l'installation. Expliquer : wmux = panel navigateur visible en temps réel dans le terminal.
**macOS/Linux** : wmux non disponible nativement. Proposer tmux comme alternative (déjà utilisé par `relay-agent-loop.sh`).

Selon le résultat, mettre à jour `relay-brain.yml → wmux_status` :
- `operational` si wmux répond
- `unavailable` sinon

---

### Étape 5 — RTK (optionnel, recommandé)

Tester : `rtk --version`

Si absent → proposer l'installation. Expliquer : RTK est un proxy CLI transparent qui réduit la consommation de tokens de 60-90% (git, npm, cargo, etc.).

Si déjà présent → vérifier le hook PreToolUse dans `~/.claude/settings.json`.

Le hook RTK (PreToolUse) est distinct des hooks Relay (il peut coexister).

---

### Étape 6 — antigravity-cli / agy (optionnel)

Tester : `agy --version` ou `gemini --version`

Si aucun n'est présent → indiquer que le framework fonctionne en mode Claude-only.
Si `gemini` est présent mais pas `agy` → noter la compatibilité dans `relay-brain.yml`.
Si `agy` est présent → mettre à jour `relay-brain.yml → agents.antigravity.installed: true`.

---

### Étape 7 — Résumé de configuration

Afficher un récapitulatif :

```
✅ / ❌  Node.js <version>
✅ / ❌  Python 3 <version>
✅ / ❌  Hooks Claude Code installés
✅ / ❌  wmux (status)
✅ / ❌  RTK (status)
✅ / ❌  antigravity-cli/agy (status)
```

Indiquer les prochaines étapes :
- `cp relay-brain-ref.template.yml relay-brain-ref.yml` si souhaité
- Créer un premier projet : `node TOOLS/init-relay-project.mjs mon-projet`
- Lire `SETUP.md` pour la configuration manuelle complète
