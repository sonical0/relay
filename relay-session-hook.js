/**
 * relay-session-hook.js — UserPromptSubmit hook.
 *
 * Injects the pre-digested Relay state ONCE per session.
 *
 * It used to be a "read the brain, read the log" order: ~600 tokens injected, but
 * 12-15k tokens of reads provoked. It now injects a file capped at ~1.4 KB, and the
 * agent reads the detail only when it needs it — through the RAG by preference.
 *
 * Guard rail: one marker per session_id (received on stdin), not a 30-minute window
 * shared by the whole machine. The old mechanism meant two concurrent sessions stole
 * each other's injection, a 3-hour session re-injected six times, and a restart five
 * minutes after a stop injected nothing at all.
 *
 * Installed by relay-setup-hook.js. No dependencies: it runs before anything else.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');

const relayRoot = __dirname;
const statePath = path.join(relayRoot, '.relay-state.md');
const stateDir = path.join(relayRoot, '.relay-state');
const MARKER_DIR = path.join(os.tmpdir(), 'relay-sessions');
const MARKER_TTL_MS = 24 * 60 * 60 * 1000;
const STALE_STATE_DAYS = 7;

const nothing = () => { process.stdout.write('{}'); process.exit(0); };

// --- Session identity (JSON on stdin) ---
let sessionId = null;
let cwd = '';
try {
  const raw = fs.readFileSync(0, 'utf8');
  if (raw.trim()) {
    const payload = JSON.parse(raw);
    sessionId = payload.session_id ?? null;
    cwd = payload.cwd ?? '';
  }
} catch (_) {}

// Without a session_id we cannot deduplicate: fall back to a short window rather
// than injecting on every prompt.
const key = sessionId
  ? crypto.createHash('sha1').update(`${sessionId}::${cwd}`).digest('hex')
  : `fallback-${crypto.createHash('sha1').update(cwd).digest('hex')}`;
const marker = path.join(MARKER_DIR, `${key}.json`);

try {
  if (fs.existsSync(marker)) {
    if (sessionId) nothing();                        // already injected for this session
    if (Date.now() - fs.statSync(marker).mtimeMs < 30 * 60 * 1000) nothing();
  }
} catch (_) {}

try {
  fs.mkdirSync(MARKER_DIR, { recursive: true });
  fs.writeFileSync(marker, JSON.stringify({ sessionId, cwd, at: Date.now() }), 'utf8');
  for (const f of fs.readdirSync(MARKER_DIR)) {
    const p = path.join(MARKER_DIR, f);
    try { if (Date.now() - fs.statSync(p).mtimeMs > MARKER_TTL_MS) fs.unlinkSync(p); } catch (_) {}
  }
} catch (_) {
  // No marker possible: inject anyway — once too often beats never.
}

// --- Pre-digested state, targeted at the current project when possible ---
//
// One conversation turn costs 4,000 to 11,000 tokens, against 614 for the whole
// context consolidation. At equal block size, talking about the project you are
// standing in avoids the initial exploration turn.
// Windows does not distinguish path case, path.resolve() does — a harness reporting
// a cwd of "documents" while the brain says "Documents" makes a strict comparison
// fail in silence, and the per-project state is then never picked.
const canon = (p) => (process.platform === 'win32' ? String(p).toLowerCase() : String(p));

/**
 * Both the resolved path and its real form. On Windows a cwd can arrive in 8.3 short
 * form (C:\Users\ALEXAN~1\…) while the index holds the long one, and a junction or a
 * mapped drive can name the same directory two ways. Comparing both forms costs one
 * syscall and removes a class of silent misses.
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

function pickState() {
  try {
    const index = JSON.parse(fs.readFileSync(path.join(stateDir, 'index.json'), 'utf8'));
    const hereForms = forms(cwd || process.cwd());
    let best = null;
    for (const e of index) {
      const dirForms = forms(e.dir);
      // Path prefix, bounded on the separator so PROJECTS/radio is not confused
      // with PROJECTS/radio-v2.
      const match = hereForms.some((h) => dirForms.some(
        (d) => canon(h) === canon(d) || canon(h).startsWith(canon(d + path.sep))
      ));
      if (match) {
        const dir = dirForms[0];
        if (!best || dir.length > path.resolve(best.dir).length) best = e;
      }
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
  lines.push('[RELAY] State missing — regenerate: node TOOLS/relay-state.mjs', `Relay root: ${relayRoot}`);
} else {
  lines.push(picked.text);
  const ageDays = Math.floor((Date.now() - picked.mtime) / 86400000);
  if (ageDays > STALE_STATE_DAYS) {
    lines.push('', `[RELAY] This state is ${ageDays} d old — refresh: npm run state`);
  }
}

process.stdout.write(JSON.stringify({
  hookSpecificOutput: {
    hookEventName: 'UserPromptSubmit',
    additionalContext: lines.join('\n'),
  },
}));
