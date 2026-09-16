/**
 * relay-setup-hook.js — installs the Relay hooks.
 *
 * Usage (once per machine):
 *   node relay-setup-hook.js
 *
 * It also installs itself as a SessionStart hook, so every Claude Code start checks
 * the hooks are still present and re-adds them if not (after a settings reset, for
 * instance). When something was missing, it prints a visible warning.
 *
 * Hook configuration is local to the machine and never committed.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');

// Respect CLAUDE_CONFIG_DIR: a second account runs on its own config dir.
const configDir = process.env.CLAUDE_CONFIG_DIR || path.join(os.homedir(), '.claude');
const settingsPath = path.join(configDir, 'settings.json');

// Propagation to the other config dirs is opt-in. If you run two profiles — a
// personal and a work one — set RELAY_PROPAGATE_HOOKS=1 and the hooks, and only the
// hooks, are copied across. It is off by default because overwriting the `hooks` key
// of a config dir the user did not name is a surprising thing for a setup script to do.
const siblingConfigDirs = process.env.RELAY_PROPAGATE_HOOKS === '1'
  ? ['.claude', '.claude-work']
    .map((d) => path.join(os.homedir(), d))
    .filter((d) => path.resolve(d) !== path.resolve(configDir) && fs.existsSync(d))
  : [];

const sessionHook = path.join(__dirname, 'relay-session-hook.js');   // UserPromptSubmit
const setupHook = __filename;                                       // SessionStart (this file)
const stopHook = path.join(__dirname, 'relay-stop-hook.js');        // Stop

// ── Read existing settings ───────────────────────────────────────────────────
// An unreadable settings.json must NEVER restart from {}: the rewrite below would
// then erase enabledPlugins, mcpServers, theme, permissions… Absent → {} is
// legitimate (first run). Present but invalid → bail out.
let settings = {};
if (fs.existsSync(settingsPath)) {
  let raw;
  try {
    raw = fs.readFileSync(settingsPath, 'utf8');
  } catch (e) {
    process.stderr.write(`[RELAY] settings.json unreadable (${e.message}) — no change.\n`);
    process.exit(0);
  }
  try {
    settings = JSON.parse(raw);
  } catch (e) {
    process.stderr.write(
      `[RELAY] settings.json invalid (${e.message}).\n` +
      `        Hooks NOT reinstalled, so your settings are not overwritten.\n` +
      `        Fix ${settingsPath} then run: node "${__filename}"\n`
    );
    process.exit(0);
  }
  if (settings === null || typeof settings !== 'object' || Array.isArray(settings)) {
    process.stderr.write('[RELAY] settings.json is not an object — no change.\n');
    process.exit(0);
  }
}

if (!settings.hooks) settings.hooks = {};

let modified = false;
const added = [];

function ensure(event, script, entry, label) {
  const groups = settings.hooks[event] || [];
  const present = groups.some((g) => g.hooks?.some((h) => h.command?.includes(script)));
  if (present) return;
  settings.hooks[event] = [{ hooks: [entry] }, ...groups];
  added.push(`${event} → ${script}`);
  modified = true;
}

// 5 s: the hook reads a 1.4 KB file and exits, measured at ~70 ms.
ensure('UserPromptSubmit', 'relay-session-hook.js',
  { type: 'command', command: `node "${sessionHook}"`, timeout: 5 });

ensure('SessionStart', 'relay-setup-hook.js',
  { type: 'command', command: `node "${setupHook}"`, timeout: 15, async: true });

if (fs.existsSync(stopHook)) {
  ensure('Stop', 'relay-stop-hook.js',
    { type: 'command', command: `node "${stopHook}"`, timeout: 20 });
}

// ── Skill links → .claude/skills/ ────────────────────────────────────────────
// Neither NTFS junctions nor symlinks survive cloud sync: they have to be recreated
// locally on each machine. Idempotent, silent when everything is already in place.
try {
  const skillsLink = path.join(__dirname, 'TOOLS', 'skills-link.mjs');
  if (fs.existsSync(skillsLink)) {
    require('child_process').execFileSync(process.execPath, [skillsLink], {
      stdio: 'ignore', timeout: 10000,
    });
  }
} catch (_) {
  // Non-blocking: missing links degrade skills, they do not break the session.
}

// ── Save + visible warning ───────────────────────────────────────────────────
if (modified) {
  fs.mkdirSync(path.dirname(settingsPath), { recursive: true });

  // Dated backup, then an atomic write: settings.json must never be left half
  // written if the process is killed mid-way.
  const stamp = new Intl.DateTimeFormat('en-CA').format(new Date()).replace(/-/g, '');
  try {
    if (fs.existsSync(settingsPath)) fs.copyFileSync(settingsPath, `${settingsPath}.bak-${stamp}`);
  } catch (e) {
    process.stderr.write(`[RELAY] Backup failed (${e.message}) — write cancelled.\n`);
    process.exit(0);
  }

  const tmpPath = `${settingsPath}.tmp`;
  fs.writeFileSync(tmpPath, JSON.stringify(settings, null, 2), 'utf8');
  JSON.parse(fs.readFileSync(tmpPath, 'utf8'));   // never replace a valid file with an invalid one
  fs.renameSync(tmpPath, settingsPath);

  // Propagate the hooks to the other config dirs without touching the rest of their
  // settings — theme, model, permissions may legitimately differ.
  for (const dir of siblingConfigDirs) {
    const sibPath = path.join(dir, 'settings.json');
    try {
      if (!fs.existsSync(sibPath)) continue;
      const sib = JSON.parse(fs.readFileSync(sibPath, 'utf8'));
      sib.hooks = settings.hooks;
      const sibTmp = `${sibPath}.tmp`;
      fs.writeFileSync(sibTmp, JSON.stringify(sib, null, 2), 'utf8');
      JSON.parse(fs.readFileSync(sibTmp, 'utf8'));
      fs.renameSync(sibTmp, sibPath);
      added.push(`hooks propagated → ${dir}`);
    } catch (e) {
      process.stderr.write(`[RELAY] Propagation to ${dir} failed: ${e.message}\n`);
    }
  }

  const box = (s) => `|  ${s.padEnd(52)}|`;
  process.stderr.write([
    '',
    '+' + '-'.repeat(54) + '+',
    box('RELAY — HOOKS WERE MISSING, REINSTALLED'),
    '+' + '-'.repeat(54) + '+',
    ...added.map((h) => box('+ ' + h)),
    '+' + '-'.repeat(54) + '+',
    box(`file: ${settingsPath.slice(-44)}`),
    box('Restart Claude Code to activate them.'),
    '+' + '-'.repeat(54) + '+',
    '',
  ].join('\n'));
} else if (process.env.CLAUDE_HOOK_EVENT !== 'SessionStart' && process.stdout.isTTY) {
  console.log('[RELAY] Hooks already in place — nothing to do.');
}
