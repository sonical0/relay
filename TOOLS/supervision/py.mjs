// Runs a Python script with the host's interpreter: python3 (Linux, macOS), python or `py -3` (Windows).
//   node TOOLS/supervision/py.mjs TOOLS/supervision/launch.py example-app --lite
// The npm scripts `supervision` and `supervision:test` go through here: `python` does not exist everywhere.
import { spawnSync } from 'node:child_process';

const candidates = process.platform === 'win32' ? [['python'], ['py', '-3']] : [['python3'], ['python']];
for (const [cmd, ...pre] of candidates) {
  const r = spawnSync(cmd, [...pre, ...process.argv.slice(2)], { stdio: 'inherit' });
  if (r.error && r.error.code === 'ENOENT') continue;  // interpreter missing: try the next one
  process.exit(r.status ?? 1);
}
console.error('Python 3 not found (tried: ' + candidates.map(c => c.join(' ')).join(', ') + ').');
process.exit(127);
