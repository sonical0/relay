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
 * Also injects: the session identity (warns when unnamed), and the resume card the agent wrote
 * before its last /clear (`.relay-reprise/<project>/<agent>.md`, local, < 48 h). RELAY_PROJECT
 * and RELAY_AGENT, set by whoever launches the agent, pick the targeted state and the card
 * when the agent starts from the root (to keep AGENTS.md and the skills loaded).
 *
 * Input: JSON on stdin ({ session_id, cwd, source, transcript_path, ... }). Output: JSON
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
let transcript = '';
let sessionId = '';
try {
  const raw = fs.readFileSync(0, 'utf8');
  if (raw.trim()) {
    const payload = JSON.parse(raw);
    cwd = payload.cwd ?? '';
    source = payload.source ?? '';
    transcript = String(payload.transcript_path ?? '');
    sessionId = String(payload.session_id ?? '');
  }
} catch (_) {}
const safeName = (s) => String(s).replace(/[^\w-]/g, '_');
const envProject = process.env.RELAY_PROJECT ? safeName(process.env.RELAY_PROJECT) : '';

// Session → project, read by TOOLS/token-scan.mjs before the cwd: an agent launched from the
// root with RELAY_PROJECT would otherwise be counted as "relay". Local, like the transcripts.
if (envProject && /^[\w-]{8,64}$/.test(sessionId)) {
  try {
    const dir = path.join(relayRoot, '.relay-reprise');
    fs.mkdirSync(dir, { recursive: true });
    fs.appendFileSync(path.join(dir, '_sessions.tsv'),
      `${sessionId}\t${envProject}\t${safeName(process.env.RELAY_AGENT || '')}\t${new Date().toISOString()}\n`, 'utf8');
  } catch (_) {}
}

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
// origin machine's mtime. If it changed: merge the journal then rebuild the state.
// DETACHED, off the critical path: run synchronously, the two scripts cost several
// seconds and the hook sometimes hit its 5 s timeout, starting a session with no state
// at all. The existing state is injected now; the refreshed one serves the next reply
// (the Stop hook rebuilds it after every reply anyway). See DECISIONS.md, 2026-11-19.
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
    const chain = ['relay-log.mjs', 'relay-state.mjs']
      .map((s) => path.join(relayRoot, 'TOOLS', s)).filter((p) => fs.existsSync(p))
      .map((p) => `require('child_process').execFileSync(process.execPath,[${JSON.stringify(p)}],{cwd:${JSON.stringify(relayRoot)},stdio:'ignore',timeout:60000,windowsHide:true})`)
      .join(';');
    if (!chain) return;
    require('child_process').spawn(process.execPath, ['-e', chain], { detached: true, stdio: 'ignore', cwd: relayRoot, windowsHide: true }).unref();
    fs.writeFileSync(stamp, sig, 'utf8');
  } catch (_) {}
}
refreshIfLogsChanged();

function pickState() {
  try {
    const index = JSON.parse(fs.readFileSync(path.join(stateDir, 'index.json'), 'utf8'));
    const here = cwd || process.cwd();
    // RELAY_PROJECT (set by a launcher) wins over the cwd: an agent started from the root
    // still gets its project's state.
    let best = envProject ? index.find((e) => e.name === process.env.RELAY_PROJECT) ?? null : null;
    if (!best) for (const e of index) {
      if (under(here, e.dir) && (!best || path.resolve(e.dir).length > path.resolve(best.dir).length)) best = e;
    }
    if (best) {
      const p = path.join(stateDir, best.file);
      return { text: fs.readFileSync(p, 'utf8').trim(), mtime: fs.statSync(p).mtimeMs, project: best.name };
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

// Session identity. Convention: a session is named `<project>_<X>` (`claude -n <project>_<X>`,
// or /rename), which is also its address for messages between agents. The name comes from
// the launcher (RELAY_PROJECT + RELAY_AGENT) or from the transcript's title record.
try {
  let name = envProject && process.env.RELAY_AGENT ? `${envProject}_${safeName(process.env.RELAY_AGENT)}` : '';
  if (!name && transcript && fs.existsSync(transcript)) {
    const fd = fs.openSync(transcript, 'r');
    const size = fs.fstatSync(fd).size;
    const buf = Buffer.alloc(Math.min(size, 256 * 1024));
    fs.readSync(fd, buf, 0, buf.length, Math.max(0, size - buf.length));
    fs.closeSync(fd);
    const titles = [...buf.toString('utf8').matchAll(/"customTitle"\s*:\s*"([^"]{1,80})"/g)];
    if (titles.length) name = titles[titles.length - 1][1];
  }
  const project = (picked && picked.project) || envProject || '';
  if (name) lines.push('', `[RELAY · session] ${name}${project ? ` — project ${project}` : ''}.`);
  else lines.push('', `[RELAY · session] No session name detected: name it \`${project || '<project>'}_<X>\` (\`claude -n …\` at launch, or /rename) so other agents can address it.`);
} catch (_) {}

// Short sessions: before /clear or at the end of a task, the agent writes a resume card,
// .relay-reprise/<project>/<agent>.md (<= 600 bytes; local, never synced or committed). It is
// re-injected while younger than 48 h; its path is always given so the agent knows where
// to write it. Under wmux (optional), the default agent id is per pane and the card carries
// `surface: <id>` on its first line: a card written from another pane is not resumed.
try {
  const surface = process.env.WMUX_SURFACE_ID || '';
  const project = safeName((picked && picked.project) || envProject || 'relay');
  const fallback = surface ? `pane-${surface.replace(/^surf-/, '').replace(/[^\w-]/g, '').slice(0, 8)}` : 'main';
  const agent = safeName(process.env.RELAY_AGENT || fallback);
  const card = path.join(relayRoot, '.relay-reprise', project, `${agent}.md`);
  const howTo = `resume card <= 600 bytes (goal, done / to do, last verified command, open questions) in ${card}${surface ? `, first line \`surface: ${surface}\`` : ''}`;
  if (fs.existsSync(card)) {
    const ageH = (Date.now() - fs.statSync(card).mtimeMs) / 3600000;
    const text = fs.readFileSync(card, 'utf8').trim();
    const author = (text.match(/^surface:\s*(\S+)/m) || [])[1] || '';
    if (ageH > 48) {
      lines.push('', `[RELAY · resume] Card older than 48 h ignored (${card}): rewrite it before the next /clear.`);
    } else if (surface && author && author !== surface) {
      lines.push('', `[RELAY · resume] ${card} comes from another pane (${author}): do NOT resume it. Before /clear or at the end of a task: ${howTo}.`);
    } else {
      lines.push('', `[RELAY · resume] Resume card (${Math.round(ageH)} h old) — pick up where the previous session stopped:`, text.slice(0, 1200));
    }
  } else {
    lines.push('', `[RELAY · resume] Before /clear or at the end of a task: ${howTo}.`);
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
