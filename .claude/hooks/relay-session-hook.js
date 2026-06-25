/**
 * relay-session-hook.js — Hook UserPromptSubmit pour le framework Relay.
 * Injecte le contexte Relay au démarrage de chaque nouvelle session.
 * Installé par relay-setup-hook.js dans ~/.claude/settings.json.
 */

const fs   = require('fs');
const os   = require('os');
const path = require('path');
const cp   = require('child_process');

const SESSION_FILE     = path.join(os.tmpdir(), 'claude-relay-session.json');
const SESSION_TIMEOUT  = 30 * 60 * 1000; // 30 min

// RELAY_ROOT = dossier parent de .claude/hooks/ (i.e. la racine du framework)
const relayRoot = path.join(__dirname, '..', '..');
const relayBrain = path.join(relayRoot, 'relay-brain.yml');
const relayLog   = path.join(relayRoot, 'RELAY_LOG.md');

// ── Déduplication de session ────────────────────────────────────────────────
let isNewSession = true;
try {
  const data = JSON.parse(fs.readFileSync(SESSION_FILE, 'utf8'));
  if (Date.now() - data.timestamp < SESSION_TIMEOUT) isNewSession = false;
} catch (_) {}

if (!isNewSession) { process.stdout.write('{}'); process.exit(0); }

fs.writeFileSync(SESSION_FILE, JSON.stringify({ timestamp: Date.now() }), 'utf8');

const lines = [
  '[RELAY BOOT — ACTION OBLIGATOIRE]',
  "Avant de répondre, exécuter le protocole d'entrée Relay :",
  `1. Lire ${relayBrain} (état courant : agents, projets, claims). relay-brain-ref.md déjà chargé via CLAUDE.md.`,
  `2. Lire les 50 dernières lignes de ${relayLog}`,
];

// Vault Live-Chat (optionnel — si présent dans le dossier relay)
const chatFile = path.join(relayRoot, 'RELAY_CHAT.md');
if (fs.existsSync(chatFile)) {
  lines.push(`2.5. Lire le canal temps réel : ${chatFile} pour ingérer les derniers messages.`);
}

lines.push(
  '3. Détecter et corriger les entrées périmées (dates passées, états obsolètes)',
  "4. Ne signaler à l'utilisateur que si des corrections ont été faites",
);

// ── Statut serveur Relay (check synchrone) ──────────────────────────────────
try {
  const raw = cp.execSync('curl -sf --max-time 0.4 http://127.0.0.1:7432/status',
    { timeout: 600, encoding: 'utf8' });
  const status = JSON.parse(raw);
  const pending = status?.queue_sizes?.claude ?? 0;
  if (pending > 0) {
    lines.push(`[RELAY SERVER] Actif (uptime ${status.uptime_s}s). ${pending} message(s) en attente → node TOOLS/relay-chat.mjs poll claude`);
  } else {
    lines.push(`[RELAY SERVER] Actif (uptime ${status.uptime_s}s). Aucun message en attente.`);
  }
} catch (_) {
  const startScript = path.join(relayRoot, 'TOOLS', 'relay-server-start.sh');
  if (fs.existsSync(startScript)) {
    try {
      cp.execSync(`bash "${startScript}"`, { timeout: 4000, stdio: 'ignore' });
      lines.push('[RELAY SERVER] Démarré automatiquement.');
    } catch (_e) {
      lines.push('[RELAY SERVER] Inactif — lancer : bash TOOLS/relay-server-start.sh');
    }
  } else {
    lines.push('[RELAY SERVER] Inactif (relay-server-start.sh introuvable).');
  }
}

// ── Agent loop agy (Linux uniquement) ───────────────────────────────────────
if (os.platform() === 'linux') {
  const loopRunning = (() => {
    try {
      return cp.execSync('pgrep -f relay-agent-loop.sh', { encoding: 'utf8', timeout: 500 }).trim().length > 0;
    } catch (_) { return false; }
  })();
  if (!loopRunning) {
    try {
      const loopScript = path.join(relayRoot, 'TOOLS', 'relay-agent-loop.sh');
      cp.execSync(
        `tmux has-session -t relay 2>/dev/null || tmux new-session -d -s relay; ` +
        `tmux new-window -t relay -n "agy-loop" "bash ${loopScript}"`,
        { timeout: 3000, stdio: 'ignore' }
      );
      lines.push('[RELAY AGY LOOP] Démarré automatiquement (fenêtre tmux agy-loop).');
    } catch (_) {
      lines.push('[RELAY AGY LOOP] Non actif — démarrage auto échoué. Lancer : bash TOOLS/relay-agent-loop.sh');
    }
  } else {
    lines.push('[RELAY AGY LOOP] Actif (délégation temps réel disponible).');
  }
}

lines.push('Ce protocole est silencieux si tout est à jour.');

process.stdout.write(JSON.stringify({
  hookSpecificOutput: {
    hookEventName: 'UserPromptSubmit',
    additionalContext: lines.join('\n'),
  },
}));
