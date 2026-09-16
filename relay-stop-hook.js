/**
 * relay-stop-hook.js — Stop hook.
 *
 * Two jobs at the end of a session, neither of which may block:
 *   1. refresh the derived project status and the injected state, detached, so the
 *      next session starts from something current rather than paying for a scan;
 *   2. note in passing if nothing was written to RELAY_LOG.md today.
 *
 * It never returns a blocking decision. A session-end hook that holds the agent back
 * to demand a log entry costs more than the missing entry.
 */

const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const relayRoot = __dirname;
const logPath = path.join(relayRoot, 'RELAY_LOG.md');

// --- 1. Detached refresh -----------------------------------------------------
// Detached + unref: the hook returns immediately, the scan finishes on its own.
// project-scan and relay-state both write atomically, so crossing a manual
// `npm run state` is safe.
try {
  const scan = path.join(relayRoot, 'TOOLS', 'project-scan.mjs');
  const state = path.join(relayRoot, 'TOOLS', 'relay-state.mjs');
  if (fs.existsSync(scan) && fs.existsSync(state)) {
    const child = spawn(
      process.execPath,
      ['-e', `require('child_process').execFileSync(process.execPath,[${JSON.stringify(scan)}],{stdio:'ignore'});`
           + `require('child_process').execFileSync(process.execPath,[${JSON.stringify(state)}],{stdio:'ignore'});`],
      { detached: true, stdio: 'ignore', cwd: relayRoot }
    );
    child.unref();
  }
} catch (_) { /* refresh is best-effort */ }

// --- 2. Was anything logged today? ------------------------------------------
let note = null;
try {
  const today = new Intl.DateTimeFormat('en-CA').format(new Date());
  const content = fs.readFileSync(logPath, 'utf8');
  const latest = content.match(/^## (\d{4}-\d{2}-\d{2})/m)?.[1];
  if (latest !== today) note = `[RELAY] Nothing logged in RELAY_LOG.md today (latest: ${latest ?? 'none'}).`;
} catch (_) { /* no log yet */ }

if (note) process.stderr.write(note + '\n');
process.stdout.write('{}');
