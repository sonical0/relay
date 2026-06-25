/**
 * relay-setup-hook.js — Auto-installeur des hooks Relay.
 *
 * Usage (une seule fois sur chaque nouveau poste) :
 *   node .claude/hooks/relay-setup-hook.js
 *
 * S'installe comme hook SessionStart. À chaque démarrage de Claude Code,
 * vérifie que les 3 hooks sont présents et les (ré)ajoute si nécessaire.
 * Idempotent — sans effet si les hooks sont déjà en place.
 */

const fs   = require('fs');
const os   = require('os');
const path = require('path');

const settingsPath  = path.join(os.homedir(), '.claude', 'settings.json');
const hooksDir      = __dirname; // .claude/hooks/
const sessionHook   = path.join(hooksDir, 'relay-session-hook.js');
const setupHook     = __filename;
const stopHook      = path.join(hooksDir, 'relay-stop-hook.js');

// ── Lecture des settings ─────────────────────────────────────────────────────
let settings = {};
try { settings = JSON.parse(fs.readFileSync(settingsPath, 'utf8')); } catch (_) {}
if (!settings.hooks) settings.hooks = {};

let modified = false;
const added  = [];

// ── UserPromptSubmit → relay-session-hook.js ─────────────────────────────────
const upHooks = settings.hooks.UserPromptSubmit || [];
if (!upHooks.some(g => g.hooks?.some(h => h.command?.includes('relay-session-hook.js')))) {
  settings.hooks.UserPromptSubmit = [
    { hooks: [{ type: 'command', command: `node "${sessionHook}"`, timeout: 10 }] },
    ...upHooks,
  ];
  added.push('UserPromptSubmit → relay-session-hook.js');
  modified = true;
}

// ── SessionStart → relay-setup-hook.js (auto-vérification) ──────────────────
const ssHooks = settings.hooks.SessionStart || [];
if (!ssHooks.some(g => g.hooks?.some(h => h.command?.includes('relay-setup-hook.js')))) {
  settings.hooks.SessionStart = [
    { hooks: [{ type: 'command', command: `node "${setupHook}"`, timeout: 15, async: true }] },
    ...ssHooks,
  ];
  added.push('SessionStart → relay-setup-hook.js');
  modified = true;
}

// ── Stop → relay-stop-hook.js ────────────────────────────────────────────────
const stHooks = settings.hooks.Stop || [];
if (!stHooks.some(g => g.hooks?.some(h => h.command?.includes('relay-stop-hook.js')))) {
  settings.hooks.Stop = [
    { hooks: [{ type: 'command', command: `node "${stopHook}"`, timeout: 10 }] },
    ...stHooks,
  ];
  added.push('Stop → relay-stop-hook.js');
  modified = true;
}

// ── Sauvegarde + avertissement visible ───────────────────────────────────────
if (modified) {
  fs.mkdirSync(path.dirname(settingsPath), { recursive: true });
  fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2), 'utf8');

  process.stderr.write([
    '',
    '╔══════════════════════════════════════════════════════╗',
    '║  ⚠️  RELAY — HOOKS MANQUANTS, RÉINSTALLÉS            ║',
    '╠══════════════════════════════════════════════════════╣',
    ...added.map(h => `║  + ${h.padEnd(49)}║`),
    '╠══════════════════════════════════════════════════════╣',
    `║  ${settingsPath.slice(-52).padEnd(52)}  ║`,
    '║  Relancez Claude Code pour activer les hooks.        ║',
    '╚══════════════════════════════════════════════════════╝',
    '',
  ].join('\n'));
}
