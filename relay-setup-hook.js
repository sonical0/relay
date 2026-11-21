/**
 * relay-setup-hook.js — installs the Relay hooks.
 *
 * Usage (once per machine — `npm run init` does it for you):
 *   node relay-setup-hook.js
 *
 * It also installs itself as an async SessionStart hook, so every Claude Code start checks
 * the hooks are still present and repairs them (settings reset, repo moved). When
 * something changed, it prints a visible warning.
 *
 * Hooks installed:
 *   SessionStart  startup|resume|clear|compact  relay-start-hook.js   injected state
 *   SessionStart  (all)                          relay-setup-hook.js   this self-check, async
 *   Stop                                         relay-stop-hook.js    journal check + after-turn
 *   PreToolUse    Bash                           supervision-guard.js  push guard (exits at once
 *                                                                      outside supervision)
 * Removed when found: relay-session-hook.js (UserPromptSubmit, replaced 2026-10-14).
 *
 * Also, in the same settings.json:
 *   permissions.deny   `git add .` / `-A` / `--all` (and their `rtk` variants): a deny rule
 *                      always applies, where prose in AGENTS.md is only followed usually.
 *   statusLine         refreshInterval 2 when the status line is Relay's (progress bar).
 *
 * Merge rule: only add what is missing and fix stale paths; never touch another tool's
 * hook, never remove or override a user entry. Local to the machine, never committed.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');

// Respect CLAUDE_CONFIG_DIR: a second account runs on its own config dir.
const configDir = process.env.CLAUDE_CONFIG_DIR || path.join(os.homedir(), '.claude');
const settingsPath = path.join(configDir, 'settings.json');

// Propagation to other config dirs is opt-in (RELAY_PROPAGATE_HOOKS=1): writing into a
// config dir the user did not name is a surprising thing for a setup script to do.
const siblingConfigDirs = process.env.RELAY_PROPAGATE_HOOKS === '1'
  ? ['.claude', '.claude-work']
    .map((d) => path.join(os.homedir(), d))
    .filter((d) => path.resolve(d) !== path.resolve(configDir) && fs.existsSync(d))
  : [];

const startHook = path.join(__dirname, 'relay-start-hook.js');
const setupHook = __filename;
const stopHook = path.join(__dirname, 'relay-stop-hook.js');
const guardHook = path.join(__dirname, 'TOOLS', 'supervision', 'supervision-guard.js');
const OBSOLETE = ['relay-session-hook.js'];

const WANTED = [
  // [event, marker, definition, matcher]
  // 5 s: reads a ~1.4 KB file and exits. Synchronous: its context must be there before
  // the first prompt. `compact` re-injects it after every compaction.
  ['SessionStart', 'relay-start-hook.js', { type: 'command', command: `node "${startHook}"`, timeout: 5 }, 'startup|resume|clear|compact'],
  // Async: it may run skills-link.mjs, which touches the disk.
  ['SessionStart', 'relay-setup-hook.js', { type: 'command', command: `node "${setupHook}"`, timeout: 15, async: true }],
  // Returns at once (the work is detached); the margin only covers reading the log.
  ['Stop', 'relay-stop-hook.js', { type: 'command', command: `node "${stopHook}"`, timeout: 20 }],
  // Exits without reading anything unless the command contains "push".
  ...(fs.existsSync(guardHook)
    ? [['PreToolUse', 'supervision-guard.js', { type: 'command', command: `node "${guardHook}"`, timeout: 10 }, 'Bash']]
    : []),
];

// Two commands can name the same file with different separators or case; comparing
// strings would rewrite the settings (backup included) on every start for nothing.
function sameFile(command, expected) {
  const file = (c) => { const m = String(c).match(/"([^"]+)"|'([^']+)'/); return m ? path.resolve(m[1] ?? m[2]) : null; };
  const a = file(command), b = file(expected);
  if (!a || !b) return command === expected;
  return process.platform === 'win32' ? a.toLowerCase() === b.toLowerCase() : a === b;
}

/** Merges the Relay hooks into a `hooks` object in place. Returns the list of changes. */
function mergeHooks(hooks) {
  const changes = [];
  for (const event of Object.keys(hooks)) {
    const groups = Array.isArray(hooks[event]) ? hooks[event] : [];
    const kept = [];
    for (const g of groups) {
      const before = (g.hooks ?? []).length;
      g.hooks = (g.hooks ?? []).filter((h) => !(typeof h.command === 'string' && OBSOLETE.some((m) => h.command.includes(m))));
      if (g.hooks.length < before) changes.push(`${event} → obsolete hook removed`);
      if (g.hooks.length) kept.push(g);
    }
    if (kept.length) hooks[event] = kept; else delete hooks[event];
  }
  for (const [event, marker, def, matcher] of WANTED) {
    const groups = Array.isArray(hooks[event]) ? hooks[event] : [];
    let present = false;
    for (const g of groups) {
      for (const h of g.hooks ?? []) {
        if (typeof h.command !== 'string' || !h.command.includes(marker)) continue;
        present = true;
        if (!sameFile(h.command, def.command)) { h.command = def.command; changes.push(`${event} → ${marker} path fixed`); }
        if (matcher && g.matcher !== matcher) { g.matcher = matcher; changes.push(`${event} → matcher ${matcher}`); }
      }
    }
    if (!present) {
      hooks[event] = [{ ...(matcher ? { matcher } : {}), hooks: [def] }, ...groups];
      changes.push(`${event} → ${marker}`);
    }
  }
  return changes;
}

// "Never `git add .` or `-A`" protects a working copy shared by several sessions: a deny
// rule is deterministic. `rtk` variants included (its hook rewrites commands); the
// PowerShell tool escapes Bash(...) rules on Windows. Add only: user entries are kept.
const DENY = ['git add .', 'git add -A', 'git add -A *', 'git add --all', 'git add --all *']
  .flatMap((c) => [`Bash(${c})`, `Bash(rtk ${c})`, `PowerShell(${c})`]);
function mergeDeny(s) {
  if (s.permissions != null && (typeof s.permissions !== 'object' || Array.isArray(s.permissions))) return [];
  s.permissions ??= {};
  if (!Array.isArray(s.permissions.deny)) s.permissions.deny = [];
  const missing = DENY.filter((r) => !s.permissions.deny.includes(r));
  if (!missing.length) return [];
  s.permissions.deny.push(...missing);
  return [`permissions.deny → global git add (${missing.length})`];
}

// The progress bar (TOOLS/progress.mjs, read by TOOLS/supervision/statusline.py) only moves
// during a long Bash command if the status line redraws by itself (DECISIONS.md, 2026-11-18).
// Set only for Relay's status line, and only when missing: a user value is kept.
function mergeStatusLine(s) {
  const sl = s.statusLine;
  if (!sl || typeof sl !== 'object' || typeof sl.command !== 'string' || sl.refreshInterval != null) return [];
  if (!/supervision[\\/]+statusline\.py/i.test(sl.command)) return [];
  sl.refreshInterval = 2;
  return ['statusLine → refreshInterval 2'];
}

/** Everything Relay adds to a settings object, in place. Returns the list of changes. */
function mergeSettings(s) {
  if (!s.hooks || typeof s.hooks !== 'object') s.hooks = {};
  return [...mergeHooks(s.hooks), ...mergeDeny(s), ...mergeStatusLine(s)];
}

/** Reads a settings.json. Absent → {}. Unreadable or invalid → null (never rewrite it). */
function readSettings(file) {
  if (!fs.existsSync(file)) return {};
  try {
    const s = JSON.parse(fs.readFileSync(file, 'utf8'));
    return s && typeof s === 'object' && !Array.isArray(s) ? s : null;
  } catch (e) {
    process.stderr.write(`[RELAY] ${file} invalid (${e.message}) — hooks NOT reinstalled, fix it then run: node "${__filename}"\n`);
    return null;
  }
}

/** Dated backup, atomic write, re-read before the swap. */
function writeSettings(file, settings) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const stamp = new Intl.DateTimeFormat('en-CA').format(new Date()).replace(/-/g, '');
  if (fs.existsSync(file)) fs.copyFileSync(file, `${file}.bak-${stamp}`);
  const tmp = `${file}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(settings, null, 2), 'utf8');
  JSON.parse(fs.readFileSync(tmp, 'utf8'));
  fs.renameSync(tmp, file);
}

const settings = readSettings(settingsPath);
let changes = [];
if (settings) {
  changes = mergeSettings(settings);
  if (changes.length) {
    try { writeSettings(settingsPath, settings); } catch (e) {
      process.stderr.write(`[RELAY] Could not write ${settingsPath} (${e.message}).\n`);
      changes = [];
    }
  }
}

// Skill links and rule copies: neither junctions nor symlinks survive cloud sync, so they
// are recreated locally. Idempotent and silent. Non-blocking.
try {
  const skillsLink = path.join(__dirname, 'TOOLS', 'skills-link.mjs');
  if (fs.existsSync(skillsLink)) {
    require('child_process').execFileSync(process.execPath, [skillsLink], { stdio: 'ignore', timeout: 10000, windowsHide: true });
  }
} catch (_) {}

// Opt-in propagation, outside `if (changes)`: a sibling that lost its hooks cannot repair
// itself — its own SessionStart went with them. Same merge, nothing else touched.
for (const dir of siblingConfigDirs) {
  const sibPath = path.join(dir, 'settings.json');
  if (!fs.existsSync(sibPath)) continue;
  const sib = readSettings(sibPath);
  if (!sib) continue;
  const c = mergeSettings(sib);
  if (!c.length) continue;
  try { writeSettings(sibPath, sib); process.stderr.write(`[RELAY] Settings completed in ${sibPath}: ${c.join(', ')}\n`); }
  catch (e) { process.stderr.write(`[RELAY] Propagation to ${dir} failed: ${e.message}\n`); }
}

if (changes.length) {
  const box = (s) => `|  ${s.padEnd(56)}|`;
  process.stderr.write([
    '', '+' + '-'.repeat(58) + '+',
    box('RELAY — SETTINGS UPDATED'),
    '+' + '-'.repeat(58) + '+',
    ...changes.map((h) => box('+ ' + h)),
    '+' + '-'.repeat(58) + '+',
    box(`file: ${settingsPath.slice(-50)}`),
    box('Restart Claude Code to activate them.'),
    '+' + '-'.repeat(58) + '+', '',
  ].join('\n'));
} else if (process.stdout.isTTY) {
  console.log('[RELAY] Hooks already in place — nothing to do.');
}
