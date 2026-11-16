/**
 * relay-stop-hook.js — Stop hook. Runs at the end of EVERY reply, not once per session.
 *
 * Two jobs:
 *   1. If this machine has no journal entry for today in LOG/<host>.md, block ONCE per
 *      session and ask for one (exit 0 + {decision:"block"}; exit 2 would make Claude Code
 *      ignore the JSON and show "Stop hook error"). A second pass only warns, so the end
 *      of a session can never loop.
 *   2. Launch TOOLS/relay-after-turn.mjs DETACHED: journal merge, brain sync, and — only
 *      when their inputs changed — state and RAG index. Project scan at most once a day,
 *      log rotation at most once a month.
 *
 * Why one detached process with a threshold (DECISIONS.md, 2026-10-13): the previous hook
 * rebuilt everything unconditionally after every reply, rewriting identical files.
 * Set RELAY_UNATTENDED=1 for scheduled, human-less runs: they are never blocked.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { spawn } = require('child_process');

const root = __dirname;
const LOG_DIR = path.join(root, 'LOG');
const RELAY_LOG = path.join(root, 'RELAY_LOG.md');
const host = process.env.RELAY_HOST || os.hostname();
const HOST_LOG = path.join(LOG_DIR, `${host}.md`);

// Silent when this is not a configured Relay root.
if (!fs.existsSync(path.join(root, 'relay-brain.yml'))) { process.stdout.write('{}'); process.exit(0); }

// LOCAL date: toISOString() is UTC and shifted "today" by a day around midnight.
const today = new Intl.DateTimeFormat('en-CA').format(new Date());
const hhmm = new Date().toTimeString().slice(0, 5);

let sessionId = 'unknown';
try {
  const raw = fs.readFileSync(0, 'utf8');
  if (raw.trim()) sessionId = JSON.parse(raw).session_id ?? 'unknown';
} catch (_) {}

// Test the HEADERS, not the whole file: a date in the body of an old entry must not count.
const heads = (file) => {
  try { return [...fs.readFileSync(file, 'utf8').matchAll(/^## .*$/gm)].map((m) => m[0].trim()); } catch (_) { return []; }
};
// Today's entry for THIS machine: in its own file, or hand-written in the generated
// RELAY_LOG.md (the merge moves it into LOG/<host>.md on the next pass). "Hand-written"
// = absent from the fingerprint trailer the generated file carries, and from every LOG/.
let mine = heads(HOST_LOG);
try {
  const text = fs.readFileSync(RELAY_LOG, 'utf8');
  const m = text.match(/^<!-- relay-log generated: ([0-9a-f ]*) -->\s*$/m);
  if (m) {
    const generated = new Set(m[1].split(' ').filter(Boolean));
    const hash = (h) => crypto.createHash('sha1').update(h.trim()).digest('hex').slice(0, 8);
    const known = new Set();
    try { for (const f of fs.readdirSync(LOG_DIR)) if (f.endsWith('.md')) heads(path.join(LOG_DIR, f)).forEach((h) => known.add(h)); } catch (_) {}
    mine = mine.concat(heads(RELAY_LOG).filter((h) => !generated.has(hash(h)) && !known.has(h)));
  }
} catch (_) {}
const logged = mine.some((h) => h.startsWith(`## ${today}`));

if (!logged && process.env.RELAY_UNATTENDED !== '1') {
  const markerDir = path.join(os.tmpdir(), 'relay-stop');
  const marker = path.join(markerDir, crypto.createHash('sha1').update(sessionId).digest('hex') + '.flag');
  if (!fs.existsSync(marker)) {
    try { fs.mkdirSync(markerDir, { recursive: true }); fs.writeFileSync(marker, today, 'utf8'); } catch (_) {}
    process.stdout.write(JSON.stringify({
      decision: 'block',
      reason:
        `[RELAY] No journal entry for ${today} on this machine.\n` +
        `Add at the TOP of ${HOST_LOG} (after its header) an entry "## ${today} ${hhmm} — Agent (project: subject)" ` +
        '(at most 5 bullets of 15 words) before finishing. Do not write to RELAY_LOG.md: it is generated.',
    }));
    process.exit(0);
  }
  process.stderr.write(`[RELAY] Journal entry for ${today} still missing — not blocking (already reported).\n`);
}

// Daily scan, monthly rotation: stamps in the temp dir, keyed by root.
const key = crypto.createHash('sha1').update(root.toLowerCase()).digest('hex').slice(0, 12);
const extra = [];
const scanStamp = path.join(os.tmpdir(), `relay-scan-${key}.stamp`);
let scanDue = true;
try { scanDue = Date.now() - fs.statSync(scanStamp).mtimeMs > 24 * 3600 * 1000; } catch (_) {}
if (scanDue) { try { fs.writeFileSync(scanStamp, String(Date.now()), 'utf8'); } catch (_) {} extra.push('--scan'); }
const rotStamp = path.join(os.tmpdir(), `relay-rotate-${key}.stamp`);
let rotDue = true;
try { rotDue = fs.readFileSync(rotStamp, 'utf8').trim() !== today.slice(0, 7); } catch (_) {}
if (rotDue) { try { fs.writeFileSync(rotStamp, today.slice(0, 7), 'utf8'); } catch (_) {} extra.push('--rotate'); }

// Detached: the end of a reply has no reason to wait for an index rebuild.
try {
  const script = path.join(root, 'TOOLS', 'relay-after-turn.mjs');
  if (fs.existsSync(script)) spawn(process.execPath, [script, ...extra], { detached: true, stdio: 'ignore', cwd: root }).unref();
} catch (_) {}

process.stdout.write('{}');
