'use strict';
// Is a project's supervision board running? Read by supervision-guard.js and session-sheet.js before handling a
// pane registered in index.json: a registration outlives the end of a supervision (nobody removes it), and a stopped
// board means the supervision is over. Without this check, an ordinary session would be refused `git push` to the
// base branch and would receive, at start, a resume sheet several days old.
//
// The port comes from `<project>.guard.json` (written by reprise.py from the project config). No port (file older
// than that field): unknown, `null`, and the caller keeps its previous behaviour (the pane counts as supervised).
// Hooks are synchronous: the TCP connection is made in a child node process, with a short timeout.
const { execFileSync } = require('child_process');

const PROBE = `
const s = require('net').connect({ host: '127.0.0.1', port: Number(process.argv[1]) });
s.setTimeout(800);
s.on('connect', () => { s.destroy(); process.exit(0); });
s.on('timeout', () => { s.destroy(); process.exit(1); });
s.on('error', () => process.exit(1));
`;

/** true: the board answers; false: nothing listens on its port; null: port unknown. */
function boardAlive(rules) {
  const port = Number(rules?.port);
  if (!Number.isInteger(port) || port < 1 || port > 65535) return null;
  try {
    execFileSync(process.execPath, ['-e', PROBE, String(port)], { stdio: 'ignore', timeout: 2000, windowsHide: true });
    return true;
  } catch { return false; }
}

module.exports = { boardAlive };
