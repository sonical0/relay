#!/usr/bin/env node
// progress-run: progress bar for one long command (install, build, tests), with an estimated
// time. The command keeps its output and its exit code; the bar shows "~m:ss left" from the
// median of its last 5 successful durations.
//
//   node TOOLS/progress-run.mjs "<label>" [--key <key>] -- <command> [args…]
//
// Default key: the command and the current directory; --key groups runs (e.g. "app-check").
// First run: "(unknown duration)", a bar that moves without promising an end.
// Longer than usual: "(longer than usual)". The bar is cleared in every case; only a successful
// duration (exit 0) is recorded. Without CLAUDE_CODE_SESSION_ID, just runs the command.
import { spawn, execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expectedDuration, recordDuration } from './lib/durations.mjs';

const PROGRESS = path.join(path.dirname(fileURLToPath(import.meta.url)), 'progress.mjs');

const argv = process.argv.slice(2);
const sep = argv.indexOf('--');
if (sep < 1 || sep === argv.length - 1) {
  console.error('usage: node TOOLS/progress-run.mjs "<label>" [--key <key>] -- <command> [args…]');
  process.exit(2);
}
const head = argv.slice(0, sep), command = argv.slice(sep + 1);
const label = head[0];
const ik = head.indexOf('--key');
const key = `run:${ik >= 0 ? head[ik + 1] : `${process.cwd()}|${command.join(' ')}`}`;

const expected = expectedDuration(key); // ms or null
const start = Date.now();
const bar = (...a) => { try { execFileSync(process.execPath, [PROGRESS, ...a], { stdio: 'ignore' }); } catch { /* the bar is optional */ } };

function tick() {
  const elapsed = Math.round((Date.now() - start) / 1000);
  if (expected == null) return bar(String(elapsed), String(elapsed + 30), `${label} (unknown duration)`);
  const n = Math.round(expected / 1000);
  if (elapsed < n) return bar(String(elapsed), String(n), label, '--remaining', String(n - elapsed));
  bar(String(elapsed), String(elapsed + 15), `${label} (longer than usual)`);
}

tick();
const timer = setInterval(tick, 2000);
let finished = false;
const finish = (code) => {
  if (finished) return;
  finished = true;
  clearInterval(timer);
  if (code === 0) recordDuration(key, Date.now() - start);
  bar('done');
  process.exit(code ?? 1);
};

// Direct spawn (arguments passed as they are). Windows: npm, npx… are .cmd files, which spawn
// cannot start without a shell (ENOENT or EINVAL); we then go through cmd.exe with ONE command
// line in which every argument is quoted, otherwise `>` or `&` would be interpreted.
const quote = (a) => (/^[\w./:=@+-]+$/.test(a) ? a : `"${a.replace(/"/g, '\\"')}"`);
let child;
function launch(viaShell) {
  child = viaShell
    ? spawn(command.map(quote).join(' '), { stdio: 'inherit', shell: true })
    : spawn(command[0], command.slice(1), { stdio: 'inherit' });
  child.on('exit', (code, signal) => finish(signal ? 1 : code));
  child.on('error', (e) => {
    if (!viaShell && process.platform === 'win32' && ['ENOENT', 'EINVAL'].includes(e.code)) return launch(true);
    console.error(`progress-run: ${e.message}`);
    finish(127);
  });
}
launch(false);
for (const s of ['SIGINT', 'SIGTERM']) process.on(s, () => { child?.kill(s); finish(130); });
