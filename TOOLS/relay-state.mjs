#!/usr/bin/env node
/**
 * relay-state.mjs — generates the pre-digested state injected at startup.
 *
 * Usage:
 *   node TOOLS/relay-state.mjs [--stdout]
 *
 * Writes `.relay-state.md` (general view) and `.relay-state/<project>.md` (one per
 * project, plus an index.json the hook reads). The UserPromptSubmit hook picks one
 * of them from the cwd and injects it once per session.
 *
 * Why: the previous hook injected an order — "read the brain, read the log" — which
 * caused 12-15k tokens of reads on every trigger. It now injects this file, capped
 * at roughly 1.4 KB, and the agent only reads the detail when it needs it, through
 * the RAG by preference.
 *
 * Budget: when the output exceeds MAX_BYTES, the least useful sections are trimmed
 * rather than letting the file grow in silence.
 */

import fs from 'fs';
import os from 'os';
import path from 'path';
import { fileURLToPath } from 'url';
import yaml from 'js-yaml';
import { writeAtomic, swapDir } from './lib/atomic.mjs';

// Windows does not distinguish path case, path.resolve() does. A harness reporting
// a cwd of "documents" while the brain says "Documents" makes every strict
// comparison fail silently: the per-project state is then never picked.
const canon = (p) => (process.platform === 'win32' ? String(p).toLowerCase() : String(p));

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.join(__dirname, '..');
const brainPath = path.join(rootDir, 'relay-brain.yml');
const derivedPath = path.join(rootDir, 'relay-projects.derived.yml');
const logPath = path.join(rootDir, 'RELAY_LOG.md');
const manifestPath = path.join(rootDir, 'SKILLS', 'skills.manifest.json');
const outPath = path.join(rootDir, '.relay-state.md');
const stateDir = path.join(rootDir, '.relay-state');

const MAX_BYTES = 1400;
const SKILLS_DESC_BUDGET = 4000;
const toStdout = process.argv.slice(2).includes('--stdout');
const today = new Intl.DateTimeFormat('en-CA').format(new Date());
const hostname = os.hostname();

function safe(fn, fallback) { try { return fn(); } catch { return fallback; } }
const clip = (s, n) => (s.length > n ? s.slice(0, n - 1).trimEnd() + '…' : s);
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// --- Recent sessions (RELAY_LOG is sorted newest first) ---
const logContent = safe(() => fs.readFileSync(logPath, 'utf8'), '');

const lastSessions = safe(() => {
  const heads = [...logContent.matchAll(/^## (\d{4}-\d{2}-\d{2})(?: \(\d+\))? — (.+)$/gm)].slice(0, 3);
  return heads.map((m) => `${m[1]} ${clip(m[2], 60)}`);
}, []);

// --- Body of the latest entry: what saves reopening the log ---
const lastEntry = safe(() => {
  const sections = logContent.split(/^(?=## )/m).filter((s) => s.startsWith('## '));
  if (!sections.length) return [];
  return sections[0].split('\n').filter((l) => l.trim().startsWith('- ')).slice(0, 4)
    .map((l) => clip(l.trim(), 110));
}, []);

// --- Projects: status derived from disk ---
const active = [], undeclared = [], conflicts = [];
let derivedAge = null;
safe(() => {
  const d = yaml.load(fs.readFileSync(derivedPath, 'utf8'));
  derivedAge = Math.floor((Date.now() - new Date(d.generated).getTime()) / 86400000);
  for (const [name, v] of Object.entries(d.projects ?? {})) {
    if (v.status === 'active') active.push(name);
    else if (v.status === 'undeclared') undeclared.push(name);
    if (v.conflict) conflicts.push(`${name} (${v.conflict})`);
  }
});

// --- Alerts: only what calls for an action ---
const alerts = [];
if (derivedAge === null) alerts.push('project status not computed → node TOOLS/project-scan.mjs');
else if (derivedAge > 7) alerts.push(`project status is ${derivedAge} d old → node TOOLS/project-scan.mjs`);
if (!fs.existsSync(path.join(rootDir, '.relay-rag.json'))) {
  alerts.push('RAG index missing → node TOOLS/rag-index.mjs');
}

// Skill descriptions enter the system prompt on EVERY session (~0.37 token/byte
// measured). Without a guard rail, adding skills silently takes back what was
// saved elsewhere.
safe(() => {
  let total = 0;
  const dir = path.join(rootDir, '.claude', 'skills');
  for (const s of fs.readdirSync(dir)) {
    const f = path.join(dir, s, 'SKILL.md');
    if (!fs.existsSync(f)) continue;
    const fm = fs.readFileSync(f, 'utf8').match(/^---\n([\s\S]*?)\n---/);
    if (!fm) continue;
    const meta = yaml.load(fm[1]);
    total += (meta?.description ?? '').length + s.length;
  }
  if (total > SKILLS_DESC_BUDGET) {
    alerts.push(`skill descriptions at ${total} B (~${Math.round(total * 0.37)} tk/session, budget ${SKILLS_DESC_BUDGET}) → shorten or unlink`);
  }
});

const brain = safe(() => yaml.load(fs.readFileSync(brainPath, 'utf8')), null);
if (!brain) alerts.push('relay-brain.yml unreadable → copy relay-brain.template.yml');
else if (!brain?.machines?.[hostname]) alerts.push(`machine ${hostname} missing from relay-brain.yml`);

const manifest = safe(() => JSON.parse(fs.readFileSync(manifestPath, 'utf8')), { skills: [] });
const onDemandSkills = (manifest.skills ?? []).filter((s) => s.load === 'rag');

// ─── General state ───────────────────────────────────────────────────────────
const lines = [`# Relay — state as of ${today} (${hostname})`, ''];
if (lastSessions.length) lines.push(`**Recent sessions**: ${lastSessions.join(' · ')}`);
if (lastEntry.length) lines.push('', ...lastEntry.map((l) => l.trim()));
lines.push('');
lines.push(`**Active projects**: ${active.length ? active.join(', ') : 'none'}`);
if (undeclared.length) lines.push(`**Undeclared**: ${undeclared.join(', ')}`);
if (conflicts.length) lines.push(`**To settle**: ${conflicts.join(' · ')}`);
if (alerts.length) lines.push(`**Alerts**: ${alerts.join(' · ')}`);

// Catalogue of skills absent from the system prompt. Without this line a
// `load: rag` skill would be invisible: you cannot search for what you do not know
// exists. ~25 tokens to keep them reachable.
if (onDemandSkills.length) {
  lines.push('');
  lines.push(`**On-demand skills** (not loaded): ${onDemandSkills.map((s) => s.name).join(' · ')} — `
    + '`node TOOLS/rag-query.mjs --query "…" --type skill`, then read its `SKILL.md`.');
}

lines.push('');
lines.push('Detail → `node TOOLS/rag-query.mjs --query "…" [--full]` (query the RAG before opening a file).');
lines.push('History → `RELAY_LOG.md`, newest first. Machines and projects → `relay-brain.yml`.');

let out = lines.join('\n') + '\n';
if (Buffer.byteLength(out, 'utf8') > MAX_BYTES) {
  // The body of the latest log entry is the first thing sacrificed.
  const keep = new Set(lastEntry.map((e) => e.trim()));
  out = lines.filter((l) => !keep.has(l)).join('\n') + '\n';
}

// ─── Per-project states ──────────────────────────────────────────────────────
//
// One conversation turn costs 4,000 to 11,000 tokens, against 614 for the whole
// context consolidation. The lever is therefore not the size of the injected block
// but the exploration turn it avoids. The hook picks the file from the cwd: at
// equal size, the block talks about the project being worked on.
// ─────────────────────────────────────────────────────────────────────────────

const slug = (s) => s.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '')
  .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

/** Log sections about this project — heading `(project:` or bullets `- [project]`. */
function projectLog(name) {
  const sections = logContent.split(/^(?=## )/m).filter((s) => s.startsWith('## '));
  const head = new RegExp(`\\(\\s*${esc(name)}\\s*[:)]`, 'i');
  const bullet = new RegExp(`^\\s*-\\s*\\[${esc(name)}\\]`, 'im');
  const hits = [];
  for (const s of sections) {
    const h = s.split('\n')[0];
    if (!head.test(h) && !bullet.test(s)) continue;
    hits.push({
      date: h.match(/^## (\d{4}-\d{2}-\d{2})/)?.[1] ?? '?',
      subject: clip(h.replace(/^## \d{4}-\d{2}-\d{2} — /, ''), 70),
      bullets: s.split('\n').filter((l) => l.trim().startsWith('- ')).slice(0, 3)
        .map((l) => clip(l.trim(), 105)),
    });
    if (hits.length === 2) break;
  }
  return hits;
}

/**
 * A short fingerprint of what the project contains: top-level file names plus
 * dependency names. Manifest entries may carry `suggest_if`, a regex tested
 * against it, to surface an on-demand skill in that project's state block.
 */
function fingerprint(dir) {
  try {
    const files = fs.readdirSync(dir);
    let deps = '';
    const pkgPath = path.join(dir, 'package.json');
    if (fs.existsSync(pkgPath)) {
      const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
      deps = Object.keys({ ...pkg.dependencies, ...pkg.devDependencies }).join(' ');
    }
    return `${files.join(' ')} ${deps}`;
  } catch { return ''; }
}

const projectStates = [];
safe(() => {
  const d = yaml.load(fs.readFileSync(derivedPath, 'utf8'));
  const machine = brain?.machines?.[hostname];

  for (const [name, v] of Object.entries(d.projects ?? {})) {
    // `elsewhere` = a project on another machine: no directory here, no state to serve.
    if (v.status === 'absent' || v.status === 'elsewhere') continue;

    let dir = null;
    const declared = brain?.projects?.[name]?.path;
    if (declared && machine?.relay_root) {
      dir = path.normalize(declared.replace(/\{\{relay_root\}\}/g, machine.relay_root.replace(/\\/g, '/')));
    } else {
      const guess = path.join(rootDir, 'PROJECTS', name);
      if (fs.existsSync(guess)) dir = guess;
    }
    if (!dir || !fs.existsSync(dir)) continue;
    // A project whose path is the Relay root would hijack every session.
    if (canon(path.resolve(dir)) === canon(path.resolve(rootDir))) continue;

    const L = [
      `# Relay — ${name} (${v.status}${v.last_activity ? `, last activity ${v.last_activity}` : ''})`,
      '',
    ];
    if (v.conflict) L.push(`**To settle**: ${v.conflict}`, '');

    const hits = projectLog(name);
    if (hits.length) {
      L.push('**Recent sessions on this project**');
      for (const h of hits) {
        L.push(`- ${h.date} — ${h.subject}`);
        for (const b of h.bullets) L.push(`  ${b}`);
      }
      L.push('');
    } else {
      L.push('_No log entry for this project yet._', '');
    }

    // Local rules: only point at what actually exists.
    const local = ['AGENTS.md', 'CLAUDE.md', 'GEMINI.md', 'README.md']
      .filter((f) => fs.existsSync(path.join(dir, f)));
    if (local.length) L.push(`**Local rules**: ${local.map((f) => '`' + f + '`').join(' · ')}`);

    // On-demand skills that fit what the project contains. A routing skill does not
    // trigger on its own (measured 0/3 projects), so this line is the only carrier of
    // the suggestion — it always arrives and costs nothing extra. Indicative only.
    const fp = fingerprint(dir);
    const suggested = onDemandSkills
      .filter((s) => s.suggest_if && safe(() => new RegExp(s.suggest_if, 'i').test(fp), false))
      .map((s) => '`' + s.name + '`' + (s.suggest_why ? ` (${s.suggest_why})` : ''));
    if (suggested.length) {
      L.push(`**Fitting methods**: ${suggested.join(' · ')} — \`rag-query.mjs --type skill\` to load one.`);
    }

    L.push(`Detail → \`node TOOLS/rag-query.mjs --query "${name}" --type project --full\``);
    L.push(`Other active projects: ${active.filter((a) => a !== name).join(', ') || 'none'}`);

    let body = L.join('\n') + '\n';
    if (Buffer.byteLength(body, 'utf8') > MAX_BYTES) {
      body = L.filter((l) => !l.startsWith('  - ')).join('\n') + '\n';
    }
    projectStates.push({ name, dir: path.resolve(dir), file: `${slug(name)}.md`, body });
  }
});

if (toStdout) {
  process.stdout.write(out);
} else {
  writeAtomic(outPath, out);
  console.log(`✓ .relay-state.md — ${Buffer.byteLength(out, 'utf8')} B (cap ${MAX_BYTES})`);

  // Full regeneration — a removed project must not leave a ghost state — but by
  // swap: the hook reads this directory on every prompt, and an in-place rmSync
  // would have shown it an empty directory while it was being refilled.
  swapDir(stateDir, (dir) => {
    for (const p of projectStates) fs.writeFileSync(path.join(dir, p.file), p.body, 'utf8');
    // Index read by the hook: saves pulling js-yaml and path resolution into it.
    fs.writeFileSync(
      path.join(dir, 'index.json'),
      JSON.stringify(projectStates.map((p) => ({ name: p.name, dir: p.dir, file: p.file }))),
      'utf8'
    );
  });

  const avg = projectStates.length
    ? Math.round(projectStates.reduce((n, p) => n + Buffer.byteLength(p.body, 'utf8'), 0) / projectStates.length)
    : 0;
  console.log(`✓ .relay-state/ — ${projectStates.length} project(s), ${avg} B on average`);
}
