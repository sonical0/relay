#!/usr/bin/env node
// ci-progress: follows a GitHub CI run in the status-line progress bar (TOOLS/progress.mjs),
// optionally pushing first, opening the PR and merging it. Meant to run in the background
// (run_in_background): the session is notified at the end, the user watches the status line.
//
//   node TOOLS/ci-progress.mjs --repo <owner/repo> --branch <b> [--dir <clone>]
//        [--base <b>] [--push] [--title "…" --body <file>] [--pr <n>] [--merge "<subject>"]
//
//   --push              pushes the branch (git push -u origin <b>) first
//   --title/--body      opens the PR against --base (default: the repo's default branch);
//                       otherwise --pr <n> points at an existing PR, otherwise the CI of the
//                       branch's last commit is followed without a PR
//   --merge "<subject>" merges (merge commit, given subject) if ALL runs are green.
//                       Only after the user approved the merge in the chat.
//
// Bar on 100: 2 push · 5 PR · 8 waiting for runs · 10 jobs starting ·
// 10 → 90 finished steps / total steps ("CI 12/34: <current step>") · 93 merge · 100 done,
// then `done`. The 10 → 90 part only starts once every job is running (before that the total
// grows and the bar would go back); it never goes backwards. Failure: "FAILED: …", no merge,
// failed steps' log printed, exit code 1. Guards: 2 min 30 without a run, 25 min of CI.
// Remaining time: median of the repo's last 5 green CIs (TOOLS/lib/durations.mjs), rather
// than the status line's proportional rule, which steps of very unequal length mislead.
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expectedDuration, recordDuration } from './lib/durations.mjs';

const PROGRESS = path.join(path.dirname(fileURLToPath(import.meta.url)), 'progress.mjs');

function args(argv) {
  const o = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) continue;
    const k = a.slice(2);
    if (k === 'push') { o.push = true; continue; }
    o[k] = argv[++i];
  }
  return o;
}

const o = args(process.argv.slice(2));
if (!o.repo || !o.branch) {
  console.error('usage: node TOOLS/ci-progress.mjs --repo <owner/repo> --branch <b> [--dir <clone>] [--base <b>] [--push] [--title … --body <file>] [--pr <n>] [--merge "<subject>"]');
  process.exit(2);
}
const dir = o.dir || process.cwd();
const sh = (cmd, a) => execFileSync(cmd, a, { cwd: dir, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
const gh = (a) => sh('gh', [...a, '-R', o.repo]);
let max = 0, remaining = null;
const bar = (k, label) => {
  max = Math.max(max, k);
  const r = remaining == null ? [] : ['--remaining', String(remaining)];
  try { sh(process.execPath, [PROGRESS, String(max), '100', label, ...r]); } catch { /* the bar is optional */ }
};
const KEY = `ci:${o.repo}`;
const expected = expectedDuration(KEY); // ms or null
const log = (m) => console.log(`[${new Date().toLocaleTimeString()}] ${m}`);
const pause = (ms) => new Promise((r) => setTimeout(r, ms));

/* A job is running once it has left the queue and announces its steps. */
const started = (j) => j.status !== 'queued' && j.status !== 'waiting' && (j.steps || []).length > 0;

async function main() {
  if (o.push) {
    bar(2, 'CI: push');
    sh('git', ['push', '-q', '-u', 'origin', o.branch]);
    log('branch pushed');
  }
  let num = o.pr;
  if (o.title && o.body) {
    bar(5, 'CI: opening PR');
    const base = o.base || gh(['repo', 'view', '--json', 'defaultBranchRef', '--jq', '.defaultBranchRef.name']);
    const url = gh(['pr', 'create', '--base', base, '--head', o.branch, '--title', o.title, '--body-file', o.body]);
    num = url.split('/').pop();
    log(`PR opened: ${url}`);
  }
  const sha = num
    ? gh(['pr', 'view', String(num), '--json', 'headRefOid', '--jq', '.headRefOid'])
    : sh('git', ['rev-parse', `origin/${o.branch}`]);

  let runs = [];
  for (let i = 0; i < 30 && runs.length === 0; i++) {
    bar(8, 'CI: waiting for a runner');
    await pause(5000);
    runs = JSON.parse(gh(['run', 'list', '--commit', sha, '--json', 'databaseId']));
  }
  if (runs.length === 0) throw new Error('no run after 2 min 30');
  log(`${runs.length} run(s): ${runs.map((r) => r.databaseId).join(', ')}`);

  const start = Date.now();
  let last = '', allStarted = false;
  const estimate = () => { if (expected != null) remaining = Math.max(5, Math.round((expected - (Date.now() - start)) / 1000)); };
  for (;;) {
    if (Date.now() - start > 25 * 60 * 1000) throw new Error('CI longer than 25 min');
    let total = 0, done = 0, current = '', finished = true, failure = null, jobs = 0, jobsStarted = 0;
    for (const r of runs) {
      const v = JSON.parse(gh(['run', 'view', String(r.databaseId), '--json', 'status,conclusion,jobs']));
      if (v.status !== 'completed') finished = false;
      else if (v.conclusion !== 'success') failure = { id: r.databaseId, conclusion: v.conclusion };
      for (const j of v.jobs || []) {
        jobs++;
        if (started(j) || j.status === 'completed') jobsStarted++;
        for (const s of j.steps || []) {
          total++;
          if (s.status === 'completed') done++;
          else if (s.status === 'in_progress' && !current) current = s.name;
        }
      }
    }
    if (failure) {
      let failedLog = '';
      try { failedLog = gh(['run', 'view', String(failure.id), '--log-failed']).split('\n').slice(-40).join('\n'); } catch { /* log is optional */ }
      throw new Error(`run ${failure.id}: ${failure.conclusion}${failedLog ? `\n${failedLog}` : ''}`);
    }
    allStarted = allStarted || (jobs > 0 && jobsStarted === jobs);
    estimate();
    const label = !allStarted ? 'CI: jobs starting' : `CI ${done}/${total}: ${current || (finished ? 'finished' : 'running')}`;
    bar(allStarted && total ? Math.round(10 + (done / total) * 80) : 10, label);
    if (label !== last) { log(label); last = label; }
    if (finished) { recordDuration(KEY, Date.now() - start); remaining = null; break; }
    await pause(10000);
  }

  if (o.merge && num) {
    bar(93, 'CI green: merging');
    gh(['pr', 'merge', String(num), '--merge', '--subject', o.merge, '--body', '']);
    log(`PR #${num}: ${gh(['pr', 'view', String(num), '--json', 'state', '--jq', '.state'])}`);
    bar(100, `PR #${num} merged`);
  } else {
    bar(100, 'CI green');
    log('CI green');
  }
  await pause(4000);
  try { sh(process.execPath, [PROGRESS, 'done']); } catch { /* the bar is optional */ }
  log('DONE');
}

main().catch((e) => {
  bar(100, `FAILED: ${String(e.message).split('\n')[0].slice(0, 30)}`);
  log(`FAILED: ${e.stderr || e.message}`);
  process.exitCode = 1;
});
