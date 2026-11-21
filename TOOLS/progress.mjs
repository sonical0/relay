#!/usr/bin/env node
// progress: progress of a long task, shown live in the Claude Code status line
// (TOOLS/supervision/statusline.py), next to the context size and the 5 h / 7 d quotas.
//
//   node TOOLS/progress.mjs <k> <N> [label] [--remaining <s>]   step k of N (label: current item)
//   node TOOLS/progress.mjs done                                 task finished: the bar disappears
//
// Remaining time: the file keeps `start`, when the task began (reset when N changes or k goes
// down), and the status line derives "~m:ss left" from it proportionally. `--remaining <s>`
// overrides that when the caller knows better (ci-progress, progress-run: usual duration).
//
// One file per session: <tmp>/relay-progress/<CLAUDE_CODE_SESSION_ID>.json, local to the machine
// (never synced). Without CLAUDE_CODE_SESSION_ID (run outside Claude Code), does nothing. A bar not
// updated for 15 min is ignored by the status line (task interrupted without `done`).
import fs from 'node:fs';
import path from 'node:path';
import { writeAtomic } from './lib/atomic.mjs';
import { PROGRESS_DIR } from './lib/durations.mjs';

const session = process.env.CLAUDE_CODE_SESSION_ID;
if (!session || !/^[\w-]+$/.test(session)) process.exit(0);
const file = path.join(PROGRESS_DIR, `${session}.json`);

const argv = process.argv.slice(2);
let remaining = null;
const i = argv.indexOf('--remaining');
if (i >= 0) {
  remaining = Number(argv[i + 1]);
  argv.splice(i, 2);
}
const [a, b, ...rest] = argv;
if (a === 'done') {
  fs.rmSync(file, { force: true });
  process.exit(0);
}
const k = Number(a), n = Number(b);
if (!Number.isInteger(k) || !Number.isInteger(n) || n <= 0 || k < 0) {
  console.error('usage: node TOOLS/progress.mjs <k> <N> [label] [--remaining <s>] | done');
  process.exit(2);
}
let before = null;
try { before = JSON.parse(fs.readFileSync(file, 'utf8')); } catch { /* first step */ }
const sameTask = before && before.n === n && Number(before.k) <= k && Number.isFinite(before.start);
const state = { k: Math.min(k, n), n, label: rest.join(' ').slice(0, 40), ts: Date.now(), start: sameTask ? before.start : Date.now() };
if (Number.isFinite(remaining) && remaining >= 0) state.remaining_s = Math.round(remaining);
fs.mkdirSync(path.dirname(file), { recursive: true });
writeAtomic(file, JSON.stringify(state), JSON.parse);
