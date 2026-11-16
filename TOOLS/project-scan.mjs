#!/usr/bin/env node
/**
 * project-scan.mjs — derives real project status from disk.
 *
 * Usage:
 *   node TOOLS/project-scan.mjs [--dry-run] [--json]
 *
 * Writes `relay-projects.derived.yml`. NEVER writes to relay-brain.yml: the brain
 * stays purely manual (its `intent:` carries human intent), the derived file carries
 * the measured fact. The two therefore cannot contradict each other in silence, the
 * way a hand-typed `status:` field used to.
 *
 * Rules, in order:
 *   0. this host is not in `machines:`   → elsewhere (absence is expected)
 *   1. path missing                      → absent
 *   2. terminal manual intent            → kept as-is (delivered/abandoned/archived/done)
 *   3. last activity <= 21 d             → active
 *      22-90 d                           → idle
 *      > 90 d                            → dormant
 *   4. directory under PROJECTS/ not in the brain → undeclared
 *
 * Last activity = date of the last git commit when .git is present, otherwise the
 * newest mtime excluding node_modules/.git/dist/build/venv/output/.cache.
 */

import fs from 'fs';
import path from 'path';
import { execFileSync } from 'child_process';
import yaml from 'js-yaml';
import { writeIfChanged } from './lib/atomic.mjs';
import { ROOT_DIR as rootDir, BRAIN_PATH as brainPath, hostName, samePath, projectPath } from './lib/brain.mjs';

const outPath = path.join(rootDir, 'relay-projects.derived.yml');

const args = process.argv.slice(2);
const dryRun = args.includes('--dry-run');
const asJson = args.includes('--json');

const TERMINAL = new Set(['delivered', 'abandoned', 'archived', 'done']);
const SKIP_DIRS = new Set(['node_modules', '.git', 'dist', 'build', 'venv', '.venv', 'output', '.cache', '.next', 'target']);
const DAY = 86400000;


function gitLastCommit(dir) {
  try {
    const out = execFileSync('git', ['-C', dir, 'log', '-1', '--format=%cI'], {
      encoding: 'utf8', timeout: 5000, stdio: ['ignore', 'pipe', 'ignore'],
    }).trim();
    return out ? new Date(out) : null;
  } catch { return null; }
}

function newestMtime(dir, depth = 0) {
  if (depth > 6) return 0;
  let newest = 0;
  let entries;
  try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return 0; }
  for (const e of entries) {
    if (e.name.startsWith('.')) continue;
    if (SKIP_DIRS.has(e.name)) continue;
    const p = path.join(dir, e.name);
    try {
      if (e.isDirectory()) {
        const m = newestMtime(p, depth + 1);
        if (m > newest) newest = m;
      } else {
        const m = fs.statSync(p).mtimeMs;
        if (m > newest) newest = m;
      }
    } catch { /* file vanished mid-scan */ }
  }
  return newest;
}

const isoDay = (d) => new Intl.DateTimeFormat('en-CA').format(d);

function classify(ageDays) {
  if (ageDays <= 21) return 'active';
  if (ageDays <= 90) return 'idle';
  return 'dormant';
}

// --- MAIN ---
let brain;
try {
  brain = yaml.load(fs.readFileSync(brainPath, 'utf8'));
} catch (e) {
  console.error(`relay-brain.yml unreadable: ${e.message}`);
  console.error('First run? Copy relay-brain.template.yml to relay-brain.yml.');
  process.exit(1);
}

const hostname = hostName();
const now = Date.now();
const result = {};
const declaredPaths = new Set();

for (const [name, p] of Object.entries(brain.projects ?? {})) {
  // `paths.<host>` wins, then `path`; a missing absolute clone is looked up in the clone dirs.
  const dir = projectPath(name, brain);
  const intent = p.intent ?? null;

  if (dir && !samePath(dir, rootDir)) declaredPaths.add(path.basename(dir).toLowerCase());

  // Project restricted to certain machines (`machines:` in the brain): elsewhere its
  // absence is expected and must not read as `absent`, which stays a problem signal.
  if (Array.isArray(p.machines) && !p.machines.includes(hostname)) {
    result[name] = { status: 'elsewhere', declared: true, path: p.path ?? null, machines: p.machines };
    if (intent) result[name].intent = intent;
    continue;
  }

  if (!dir || !fs.existsSync(dir)) {
    result[name] = { status: 'absent', declared: true, path: p.path ?? null };
    if (intent) result[name].intent = intent;
    continue;
  }

  // Project with no directory of its own (its `path` is the Relay root): scanning it
  // would measure activity across the whole repository. Fall back to what was
  // declared, the only real information available.
  if (samePath(dir, rootDir)) {
    result[name] = {
      status: intent ?? 'unknown',
      path_exists: true,
      source: 'declared (no directory of its own)',
    };
    continue;
  }

  const git = gitLastCommit(dir);
  const mt = newestMtime(dir);
  // The newer of the two: a repository can have an old last commit and files
  // modified yesterday.
  const mtDate = mt ? new Date(mt) : null;
  let last = null, source = 'none';
  if (git && mtDate) {
    last = git >= mtDate ? git : mtDate;
    source = git >= mtDate ? 'git' : 'mtime';
  } else if (git) { last = git; source = 'git'; }
  else if (mtDate) { last = mtDate; source = 'mtime'; }

  const entry = { path_exists: true, source };
  if (last) {
    const ageDays = Math.floor((now - last.getTime()) / DAY);
    const derived = classify(ageDays);
    entry.last_activity = isoDay(last);
    entry.age_days = ageDays;
    entry.status = intent && TERMINAL.has(intent) ? intent : derived;
    // Manual intent wins, but the contradiction is not hidden: "delivered" with a
    // commit from three days ago deserves to be flagged.
    if (intent && TERMINAL.has(intent) && derived === 'active') {
      entry.conflict = `declared ${intent} but active ${ageDays} d ago`;
    }
  } else {
    entry.status = intent ?? 'unknown';
  }
  if (intent) entry.intent = intent;
  result[name] = entry;
}

// Directories present under PROJECTS/ but missing from the brain.
const projectsDir = path.join(rootDir, 'PROJECTS');
if (fs.existsSync(projectsDir)) {
  for (const e of fs.readdirSync(projectsDir, { withFileTypes: true })) {
    if (!e.isDirectory() || e.name === '_template') continue;
    if (declaredPaths.has(e.name.toLowerCase())) continue;
    const dir = path.join(projectsDir, e.name);
    const git = gitLastCommit(dir);
    const mt = newestMtime(dir);
    const last = git ?? (mt ? new Date(mt) : null);
    result[e.name] = {
      status: 'undeclared',
      path_exists: true,
      source: git ? 'git' : 'mtime',
      ...(last ? { last_activity: isoDay(last), age_days: Math.floor((now - last.getTime()) / DAY) } : {}),
    };
  }
}

const doc = {
  generated: new Date().toISOString(),
  machine: hostname,
  rules: 'active<=21d | idle<=90d | dormant>90d | absent | elsewhere (machines:) | undeclared — terminal manual intent wins',
  projects: result,
};

if (asJson) {
  console.log(JSON.stringify(doc, null, 2));
} else {
  const counts = {};
  for (const v of Object.values(result)) counts[v.status] = (counts[v.status] ?? 0) + 1;
  for (const [name, v] of Object.entries(result)) {
    console.log(`${v.status.padEnd(11)} ${name.padEnd(34)} ${v.last_activity ?? '—'}  ${v.age_days != null ? v.age_days + ' d' : ''}`);
  }
  console.log('\n' + Object.entries(counts).map(([k, n]) => `${k}: ${n}`).join(' · '));
  const conflicts = Object.entries(result).filter(([, v]) => v.conflict);
  if (conflicts.length) {
    console.log('\n! intent/reality conflicts:');
    for (const [name, v] of conflicts) console.log(`   ${name} — ${v.conflict}`);
  }
}

if (!dryRun) {
  const header =
    '# relay-projects.derived.yml — GENERATED by TOOLS/project-scan.mjs, do not edit.\n' +
    '# Source of truth for STATUS (measured fact). relay-brain.yml carries INTENT (intent:).\n\n';
  // The time of day alone does not make a change: an identical scan keeps the file (and its
  // mtime, which the end-of-turn threshold compares) until the date changes, so the age
  // relay-state derives from `generated` never exceeds a day after a scan.
  const wrote = writeIfChanged(outPath, header + yaml.dump(doc, { lineWidth: 120 }),
    { ignore: /T\d{2}:\d{2}:\d{2}(\.\d+)?Z/, validate: (t) => yaml.load(t) });
  console.log(`\n${wrote ? '→' : '='} ${path.basename(outPath)}${wrote ? '' : ' (unchanged)'}`);
}
