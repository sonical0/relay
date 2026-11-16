/**
 * relay-start-hook.js — SessionStart hook (matcher `startup|resume|clear|compact`).
 *
 * Injects the pre-digested Relay state, targeted at the project of the `cwd` when one
 * matches (`.relay-state/<project>.md`), otherwise the general view (`.relay-state.md`).
 *
 * Replaces relay-session-hook.js (UserPromptSubmit). Why (DECISIONS.md, 2026-10-14):
 *  - no more per-session_id markers in os.tmpdir(): SessionStart only fires at startup,
 *    resume, after /clear and after compaction;
 *  - the context of a UserPromptSubmit hook was summarised away at compaction; the
 *    `compact` matcher re-injects it, so the agent still knows where it is late in a
 *    long session.
 *
 * Input: JSON on stdin ({ session_id, cwd, source, ... }). Output: JSON
 * { hookSpecificOutput: { hookEventName: 'SessionStart', additionalContext } }.
 * No dependencies: it runs before anything else.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');

const relayRoot = __dirname;
const statePath = path.join(relayRoot, '.relay-state.md');
const stateDir = path.join(relayRoot, '.relay-state');
const STALE_STATE_DAYS = 7;
const host = process.env.RELAY_HOST || os.hostname();

let cwd = '';
let source = '';
try {
  const raw = fs.readFileSync(0, 'utf8');
  if (raw.trim()) {
    const payload = JSON.parse(raw);
    cwd = payload.cwd ?? '';
    source = payload.source ?? '';
  }
} catch (_) {}

// Windows does not distinguish path case; path.resolve() does. A harness reporting a cwd
// of "documents" while the index says "Documents" would otherwise miss in silence.
const canon = (p) => (process.platform === 'win32' ? String(p).toLowerCase() : String(p));

/**
 * Both the resolved path and its real form. On Windows a cwd can arrive in 8.3 short
 * form (C:\Users\JOHNDO~1\…) while the index holds the long one, and a junction or a
 * mapped drive can name the same directory two ways.
 */
function forms(p) {
  const resolved = path.resolve(p);
  const out = [resolved];
  try {
    const real = fs.realpathSync.native(resolved);
    if (real !== resolved) out.push(real);
  } catch (_) { /* path gone: the resolved form still stands */ }
  return out;
}
// Prefix bounded on the separator, so PROJECTS/radio is not PROJECTS/radio-v2. Fallback:
// realpath does not always expand an 8.3 name, so walk up from `here` comparing file
// identity (dev + ino) with `dir` — same directory whatever spelling either side uses.
function under(here, dir) {
  if (forms(here).some((h) => forms(dir).some(
    (d) => canon(h) === canon(d) || canon(h).startsWith(canon(d + path.sep))))) return true;
  let target;
  try { target = fs.statSync(dir, { bigint: true }); } catch (_) { return false; }
  if (!target.ino) return false;
  for (let p = path.resolve(here); ; p = path.dirname(p)) {
    try {
      const st = fs.statSync(p, { bigint: true });
      if (st.ino === target.ino && st.dev === target.dev) return true;
    } catch (_) {}
    if (path.dirname(p) === p) return false;
  }
}

// Journal entries that arrived from another machine since the last regeneration. The
// state used to be rebuilt only at the end of a reply (Stop hook), so a machine switched
// back on injected a state without the sessions done elsewhere meanwhile. Signature of
// LOG/*.md (name, size, mtime) compared by EQUALITY, not "newer": sync tools keep the
// origin machine's mtime. If it changed: merge the journal then rebuild the state, under
// a 2 s budget each. Failure or timeout: inject the state as it is.
function refreshIfLogsChanged() {
  const stamp = path.join(os.tmpdir(), `relay-start-logs-${crypto.createHash('sha1')
    .update(relayRoot.toLowerCase()).digest('hex').slice(0, 12)}.stamp`);
  let sig = '';
  try {
    const dir = path.join(relayRoot, 'LOG');
    sig = fs.readdirSync(dir).filter((f) => f.endsWith('.md')).sort()
      .map((f) => { const st = fs.statSync(path.join(dir, f)); return `${f}|${st.size}|${st.mtimeMs}`; }).join('\n');
  } catch (_) { return; }
  let last = '';
  try { last = fs.readFileSync(stamp, 'utf8'); } catch (_) {}
  if (sig === last) return;
  try {
    const { execFileSync } = require('child_process');
    for (const script of ['relay-log.mjs', 'relay-state.mjs']) {
      const p = path.join(relayRoot, 'TOOLS', script);
      if (fs.existsSync(p)) execFileSync(process.execPath, [p], { cwd: relayRoot, stdio: 'ignore', timeout: 2000 });
    }
    fs.writeFileSync(stamp, sig, 'utf8');
  } catch (_) {}
}
refreshIfLogsChanged();

function pickState() {
  try {
    const index = JSON.parse(fs.readFileSync(path.join(stateDir, 'index.json'), 'utf8'));
    const here = cwd || process.cwd();
    let best = null;
    for (const e of index) {
      if (under(here, e.dir) && (!best || path.resolve(e.dir).length > path.resolve(best.dir).length)) best = e;
    }
    if (best) {
      const p = path.join(stateDir, best.file);
      return { text: fs.readFileSync(p, 'utf8').trim(), mtime: fs.statSync(p).mtimeMs };
    }
  } catch (_) {}
  try {
    return { text: fs.readFileSync(statePath, 'utf8').trim(), mtime: fs.statSync(statePath).mtimeMs };
  } catch (_) { return null; }
}

const picked = pickState();
const lines = [];

if (!picked) {
  // Explicit fallback: a warning beats a silently empty context.
  lines.push('[RELAY] State missing — regenerate: npm run state', `Relay root: ${relayRoot}`);
} else {
  if (source === 'compact') lines.push('[RELAY] Context compacted — state re-injected:', '');
  lines.push(picked.text);
  const ageDays = Math.floor((Date.now() - picked.mtime) / 86400000);
  if (ageDays > STALE_STATE_DAYS) lines.push('', `[RELAY] This state is ${ageDays} d old — refresh: npm run state`);
}

// Multi-agent supervision (TOOLS/supervision): if this session is a manager or the
// supervisor registered on a board, its GENERATED resume sheet follows the state. That is
// what makes /clear or a compaction lossless. Outside supervision, nothing is added.
// See DECISIONS.md, 2026-10-15.
try {
  // A directory of its own, outside .relay-state/ (which relay-state.mjs rebuilds whole).
  const supDir = process.env.SUP_RELAY_STATE || process.env.SUP_REPRISE_DIR || path.join(relayRoot, '.relay-supervision');
  const surface = process.env.WMUX_SURFACE_ID || '';
  // Without wmux: SUP_AGENT=<project>/<X>, set when the agent is launched, names its sheet.
  const agent = process.env.SUP_AGENT || '';
  let index = {};
  try { index = JSON.parse(fs.readFileSync(path.join(supDir, 'index.json'), 'utf8')); } catch (_) {}
  const file = (surface && index[surface]) || (/^[\w-]+\/[A-Za-z0-9]+$/.test(agent) ? `${agent}.md` : '');
  if (file) {
    const p = path.resolve(supDir, String(file));
    if (!under(p, supDir) || canon(p) === canon(path.resolve(supDir)) || !p.endsWith('.md')) throw new Error('sheet outside supervision dir');
    // Board stopped: supervision is over and the registration is stale. Injecting the
    // sheet would make the agent believe it still supervises; one line is enough.
    const project = String(file).split('/')[0];
    let rules = null;
    try { rules = JSON.parse(fs.readFileSync(path.join(supDir, `${project}.guard.json`), 'utf8')); } catch (_) {}
    let alive = null;
    try { alive = require(path.join(relayRoot, 'TOOLS', 'supervision', 'board-alive.js')).boardAlive(rules); } catch (_) {}
    if (alive === false) {
      lines.push('', `[RELAY · supervision] Still registered on ${project} (${file}), but its board does not answer: supervision over, resume sheet ignored. Unregister: sup.py --project ${project} end.`);
    } else {
      const ageMin = Math.floor((Date.now() - fs.statSync(p).mtimeMs) / 60000);
      lines.push('', `[RELAY · supervision] Resume sheet for this session (generated ${ageMin} min ago):`, '',
        fs.readFileSync(p, 'utf8').trim());
    }
  }
} catch (_) {}

// Outside the Relay tree (git clones elsewhere), AGENTS.md is not loaded. The two rules an
// agent breaks most there fit in three lines.
if (!under(cwd || process.cwd(), relayRoot)) {
  const hostLog = path.join(relayRoot, 'LOG', `${host}.md`);
  lines.push(
    '',
    `[RELAY] Outside the Relay tree (${relayRoot}): AGENTS.md is not loaded. Two rules still apply:`,
    `- Journal at the end of the session, at the TOP of ${hostLog}: \`## YYYY-MM-DD HH:MM — Agent (project: subject)\` then ≤ 5 bullets \`- [project] action → measured result\`, time read from the clock. Never in RELAY_LOG.md (generated).`,
    '- Commits: `git add` with explicit paths, never `.` or `-A`; before `git push`, `git log origin/<branch>..HEAD` — other sessions\' commits would leave too.',
  );
}

// Proposals waiting for review (INBOX/<host>/*.md): a count, so they are not forgotten.
try {
  const inbox = path.join(relayRoot, 'INBOX');
  if (fs.existsSync(inbox)) {
    let n = 0;
    for (const h of fs.readdirSync(inbox)) {
      if (h.startsWith('_') || h.startsWith('.')) continue;
      try { n += fs.readdirSync(path.join(inbox, h)).filter((f) => f.endsWith('.md')).length; } catch (_) {}
    }
    if (n) lines.push('', `[RELAY] ${n} proposal(s) waiting in INBOX/ — review them with /inbox.`);
  }
} catch (_) {}

process.stdout.write(JSON.stringify({
  hookSpecificOutput: { hookEventName: 'SessionStart', additionalContext: lines.join('\n') },
}));
