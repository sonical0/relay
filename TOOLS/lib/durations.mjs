/**
 * durations.mjs — usual durations of long tasks, for the remaining time shown by the progress bar
 * (TOOLS/progress-run.mjs, TOOLS/ci-progress.mjs).
 *
 * <tmp>/relay-progress/durations.json, local to the machine (never synced): { "<key>": [ms, …] },
 * the last 5 SUCCESSFUL durations per key. The expected duration is their median: for one and the
 * same command, runs commonly vary by a factor of two.
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { writeAtomic } from './atomic.mjs';

export const PROGRESS_DIR = path.join(os.tmpdir(), 'relay-progress');
const FILE = path.join(PROGRESS_DIR, 'durations.json');
const KEPT = 5;

function read() {
  try { return JSON.parse(fs.readFileSync(FILE, 'utf8')); } catch { return {}; }
}

/** Median of the key's last successful durations, in ms; null when there is none. */
export function expectedDuration(key) {
  const l = (read()[key] || []).filter(Number.isFinite).sort((x, y) => x - y);
  if (l.length === 0) return null;
  const m = Math.floor(l.length / 2);
  return l.length % 2 ? l[m] : Math.round((l[m - 1] + l[m]) / 2);
}

/** Records a successful duration (ms); keeps the last KEPT. */
export function recordDuration(key, ms) {
  const d = read();
  d[key] = [...(d[key] || []), Math.round(ms)].slice(-KEPT);
  fs.mkdirSync(path.dirname(FILE), { recursive: true });
  writeAtomic(FILE, JSON.stringify(d), JSON.parse);
}

/** 75 → "1:15"; 3900 → "1 h 05". */
export function mmss(s) {
  s = Math.max(0, Math.round(s));
  if (s >= 3600) return `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}`;
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}
