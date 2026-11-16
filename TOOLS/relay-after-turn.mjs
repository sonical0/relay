#!/usr/bin/env node
/**
 * relay-after-turn.mjs — end-of-turn work, launched detached by the Stop hook.
 *
 * Usage: node TOOLS/relay-after-turn.mjs [--rotate] [--scan] [--force]
 *   --rotate   monthly rotation of this machine's log (+ cross_project_log older than 30 days)
 *   --scan     recompute project status (project-scan.mjs)
 *   --force    ignore the threshold: rerun the state and the index
 *
 * The Stop hook fires at the end of EVERY answer. Regenerating everything each time is
 * wasted work and needless rewrites of synced files, so this runs in one process, in order:
 *   1. rotation if asked (skipped on a satellite machine);
 *   2. split brain: edits of relay-brain.yml reported into BRAIN/ (opt-in, skipped on a satellite);
 *   3. log merge, LOG/*.md → RELAY_LOG.md — always: it is what brings in an entry written on
 *      another machine;
 *   4. project status if asked;
 *   5. threshold: relay-state.mjs and rag-index.mjs run only when one of their inputs changed
 *      since the last successful pass. Signature = path, size and mtime of each input, compared
 *      by EQUALITY, not "newer than": a file synced from another machine keeps the mtime of its
 *      origin, often older than the last pass here. They read RELAY_LOG.md, so they run after
 *      the merge.
 *
 * Prints one summary line on stdout; errors on stderr, exit code 1 when a step failed.
 */
import fs from 'fs';
import os from 'os';
import path from 'path';
import crypto from 'crypto';
import { execFileSync } from 'child_process';
import { fileURLToPath } from 'url';
import { ROOT_DIR, TOOLS_DIR, machineRole, samePath } from './lib/brain.mjs';
import { mergeLog } from './relay-log.mjs';
import { syncBrain } from './relay-brain.mjs';

const args = process.argv.slice(2);
const done = [];
// Scripts are launched from this file's directory; the data root travels through the environment.
const node = (script, extra = []) => execFileSync(process.execPath, [path.join(TOOLS_DIR, script), ...extra], {
  cwd: ROOT_DIR, stdio: 'ignore', timeout: 120_000, env: { ...process.env, RELAY_ROOT: ROOT_DIR },
});
const step = (name, fn) => {
  try { const r = fn(); if (r !== false) done.push(name); } catch (e) { process.exitCode = 1; console.error(`✗ ${name}: ${e.message}`); }
};

export const STAMP_PATH = path.join(os.tmpdir(),
  `relay-after-turn-${crypto.createHash('sha1').update(ROOT_DIR.toLowerCase()).digest('hex').slice(0, 12)}.stamp`);

/** Signature of what relay-state and rag-index read: path, size and mtime of each file. */
export function inputsSignature() {
  const h = crypto.createHash('sha1');
  const visit = (p, depth, filter = () => true) => {
    let st;
    try { st = fs.statSync(p); } catch { return; }
    if (!st.isDirectory()) { h.update(`${p}|${st.size}|${st.mtimeMs}\n`); return; }
    if (depth <= 0) { h.update(`${p}|d|${st.mtimeMs}\n`); return; }
    let entries = [];
    try { entries = fs.readdirSync(p).sort(); } catch {}
    for (const e of entries) {
      if (e.startsWith('.') || e === 'node_modules') continue;
      const q = path.join(p, e);
      if (!filter(q, e)) continue;
      visit(q, depth - 1, filter);
    }
  };
  // Root files: the brain, the merged log, the derived status, every root markdown
  // (AGENTS.md, {AGENT}.md, DECISIONS.md, SETUP.md…). Generated outputs start with a dot.
  visit(ROOT_DIR, 1, (q, e) => {
    try { if (fs.statSync(q).isDirectory()) return false; } catch { return false; }
    return /\.md$/.test(e) || ['relay-brain.yml', 'relay-projects.derived.yml'].includes(e);
  });
  visit(path.join(ROOT_DIR, 'LOG'), 2);
  visit(path.join(ROOT_DIR, 'BRAIN'), 2);
  visit(path.join(ROOT_DIR, 'SKILLS'), 3);
  visit(path.join(ROOT_DIR, 'RULES'), 1);
  visit(path.join(ROOT_DIR, 'INBOX'), 2);
  visit(path.join(ROOT_DIR, '.claude', 'skills'), 1);
  // Project markdown, two levels down (deeper changes are picked up at the next log entry).
  visit(path.join(ROOT_DIR, 'PROJECTS'), 3, (q, e) => {
    try { return fs.statSync(q).isDirectory() || /\.mdx?$/.test(e); } catch { return false; }
  });
  return h.digest('hex');
}

// --- CLI ---
if (process.argv[1] && samePath(fileURLToPath(import.meta.url), process.argv[1])) {
  // A satellite reads the shared files and regenerates only its local ones: rotation and the
  // brain rewrite shared files (BRAIN/, relay-brain.yml), which the workstations take care of.
  const satellite = machineRole() === 'satellite';
  if (args.includes('--rotate') && !satellite) step('rotate', () => node('log-rotate.mjs', ['--brain']));
  // Brain after the rotation, which removes old cross_project_log entries from it.
  if (!satellite) {
    step('brain', () => {
      const r = syncBrain();
      if (r.status === 'error') throw new Error(r.message);
      return r.status !== 'off';
    });
  }
  step('log', () => { mergeLog({ log: () => {} }); });
  if (args.includes('--scan')) step('scan', () => node('project-scan.mjs'));

  let last = '';
  try { last = fs.readFileSync(STAMP_PATH, 'utf8').trim(); } catch {}
  const sig = inputsSignature();
  const missing = !fs.existsSync(path.join(ROOT_DIR, '.relay-state.md')) || !fs.existsSync(path.join(ROOT_DIR, '.relay-rag.json'));
  if (args.includes('--force') || missing || sig !== last) {
    // Stamp written BEFORE running: a change during the pass yields another signature next time.
    try { fs.writeFileSync(STAMP_PATH, sig, 'utf8'); } catch {}
    let ok = true;
    const run = (name, script) => {
      try { node(script); done.push(name); } catch (e) { ok = false; process.exitCode = 1; console.error(`✗ ${name}: ${e.message}`); }
    };
    // Index first: the state reports a missing index, and would keep that alert until the next change.
    run('index', 'rag-index.mjs');
    run('state', 'relay-state.mjs');
    // On failure (lock not obtained, timeout…) put the old stamp back, otherwise the change
    // would only be picked up at the next input change.
    if (!ok) { try { fs.writeFileSync(STAMP_PATH, last, 'utf8'); } catch {} }
  } else {
    done.push('state+index skipped (inputs unchanged)');
  }
  console.log(`relay-after-turn: ${done.join(' · ')}`);
}
