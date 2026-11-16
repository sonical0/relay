/**
 * session-sheet.js — SessionStart hook of the supervision (matcher `startup|resume|clear|compact`).
 *
 * If this session is a manager or the supervisor registered on a supervision board, its GENERATED resume sheet is
 * injected at start, after /clear and after compaction: that is what makes a /clear (or a compaction) lossless.
 * Outside a supervision nothing is added (empty additionalContext).
 *
 * Which sheet: `<sup dir>/index.json` maps a wmux surface id to "<project>/<X>.md". The registered surface
 * (WMUX_SURFACE_ID) wins; without wmux (or an unregistered surface), SUP_AGENT=<project>/<X>, set when the agent
 * was launched, names the sheet directly. The sheet must stay under the supervision directory.
 * Sup dir: SUP_RELAY_STATE (alias SUP_REPRISE_DIR), else <RELAY_ROOT>/.relay-supervision — same rule as config.py.
 *
 * Input: JSON on stdin ({ session_id, cwd, source, ... }). Output: JSON
 * { hookSpecificOutput: { hookEventName: 'SessionStart', additionalContext } }.
 * Also usable as a module: require('./session-sheet.js').sheetLines() returns the lines to add (array).
 * No dependencies.
 */
const fs = require('fs');
const path = require('path');

const relayRoot = process.env.RELAY_ROOT ? path.resolve(process.env.RELAY_ROOT) : path.resolve(__dirname, '..', '..');
const canon = (p) => (process.platform === 'win32' ? String(p).toLowerCase() : String(p));
const under = (here, dir) => canon(here) === canon(dir) || canon(here).startsWith(canon(dir + path.sep));

function sheetLines(env = process.env) {
  const lines = [];
  try {
    const supDir = env.SUP_RELAY_STATE || env.SUP_REPRISE_DIR || path.join(relayRoot, '.relay-supervision');
    const surface = env.WMUX_SURFACE_ID || '';
    const agent = env.SUP_AGENT || '';
    let index = {};
    try { index = JSON.parse(fs.readFileSync(path.join(supDir, 'index.json'), 'utf8')); } catch (_) {}
    const file = (surface && index[surface]) || (/^[\w-]+\/[A-Za-z0-9]+$/.test(agent) ? `${agent}.md` : '');
    if (!file) return lines;
    const p = path.resolve(supDir, String(file));
    if (!under(p, supDir) || canon(p) === canon(supDir) || !p.endsWith('.md')) return lines;
    // Board of the project stopped: supervision over, stale registration. The sheet would make the agent believe it
    // is still supervised; one line is enough (board-alive.js).
    const project = String(file).split('/')[0];
    let rules = null;
    try { rules = JSON.parse(fs.readFileSync(path.join(supDir, `${project}.guard.json`), 'utf8')); } catch (_) {}
    let alive = null;
    try { alive = require(path.join(__dirname, 'board-alive.js')).boardAlive(rules); } catch (_) {}
    if (alive === false) {
      lines.push('', `[RELAY · supervision] This session is still registered on ${project} (${file}), but its board does not answer: supervision over, resume sheet ignored. Remove the registration: sup.py --project ${project} end.`);
    } else {
      const ageMin = Math.floor((Date.now() - fs.statSync(p).mtimeMs) / 60000);
      lines.push('', `[RELAY · supervision] Resume sheet of this session (generated ${ageMin} min ago):`, '',
        fs.readFileSync(p, 'utf8').trim());
    }
  } catch (_) {}
  return lines;
}

module.exports = { sheetLines };

if (require.main === module) {
  try { fs.readFileSync(0, 'utf8'); } catch (_) {}  // drain stdin; the payload is not needed
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: { hookEventName: 'SessionStart', additionalContext: sheetLines().join('\n').trim() },
  }));
}
