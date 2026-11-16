#!/usr/bin/env node
/**
 * relay-state.mjs — generates the pre-digested state injected at startup.
 *
 * Usage:
 *   node TOOLS/relay-state.mjs [--stdout]
 *
 * Writes `.relay-state.md` (general view) and `.relay-state/<project>.md` (one per
 * project, plus an index.json — an array of {name, dir, file} — the hook reads). The
 * start hook picks one of them from the cwd and injects it once per session.
 *
 * Why: the previous hook injected an order — "read the brain, read the log" — which
 * caused 12-15k tokens of reads on every trigger. It now injects this file, capped
 * at roughly 1.4 KB, and the agent only reads the detail when it needs it, through
 * the RAG by preference.
 *
 * Reads the merged RELAY_LOG.md (relay-log.mjs). Writes nothing when the output is
 * unchanged: the Stop hook may run it after every answer.
 *
 * Budget: when the output exceeds MAX_BYTES, the least useful sections are trimmed
 * rather than letting the file grow in silence.
 */

import fs from 'fs';
import path from 'path';
import yaml from 'js-yaml';
import { writeIfChanged, swapDir } from './lib/atomic.mjs';
import { ROOT_DIR, BRAIN_PATH, hostName, canon, projectPath } from './lib/brain.mjs';
import { skillMeta, skillFiles, isOnDemand } from './lib/skills.mjs';
import { ALERTS_PATH, BRAIN_DIR, conflictCount } from './relay-brain.mjs';

const derivedPath = path.join(ROOT_DIR, 'relay-projects.derived.yml');
const logPath = path.join(ROOT_DIR, 'RELAY_LOG.md');
const manifestPath = path.join(ROOT_DIR, 'SKILLS', 'skills.manifest.json');
const outPath = path.join(ROOT_DIR, '.relay-state.md');
const stateDir = path.join(ROOT_DIR, '.relay-state');

const MAX_BYTES = 1400;
const SKILLS_DESC_BUDGET = 4000;
const TERMINAL_INTENT = new Set(['archived', 'delivered', 'done', 'abandoned']);
const toStdout = process.argv.slice(2).includes('--stdout');
const today = new Intl.DateTimeFormat('en-CA').format(new Date());
const hostname = hostName();

function safe(fn, fallback) { try { return fn(); } catch { return fallback; } }
const clip = (s, n) => (s.length > n ? s.slice(0, n - 1).trimEnd() + '…' : s);
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
// Three heading formats coexist: with time (current), with an old `(n)` suffix, or bare.
const HEAD_PREFIX = /^## \d{4}-\d{2}-\d{2}(?: \d{2}:\d{2})?(?: \(\d+\))? — /;

// --- Recent sessions (RELAY_LOG is sorted newest first) ---
const logContent = safe(() => fs.readFileSync(logPath, 'utf8').replace(/\r\n/g, '\n'), '');
const logSections = logContent.split(/^(?=## )/m).filter((s) => s.startsWith('## '));

const lastSessions = logSections.slice(0, 3).map((s) => {
  const h = s.split('\n')[0];
  return `${h.match(/^## (\d{4}-\d{2}-\d{2})/)?.[1] ?? '?'} ${clip(h.replace(HEAD_PREFIX, ''), 60)}`;
});

// --- Body of the latest entry: what saves reopening the log ---
const lastEntry = logSections.length
  ? logSections[0].split('\n').filter((l) => l.trim().startsWith('- ')).slice(0, 4).map((l) => clip(l.trim(), 110))
  : [];

// --- Projects: status derived from disk ---
const active = [], undeclared = [], conflicts = [];
let derivedAge = null;
const derived = safe(() => yaml.load(fs.readFileSync(derivedPath, 'utf8')), null);
if (derived) {
  derivedAge = Math.floor((Date.now() - new Date(derived.generated).getTime()) / 86400000);
  for (const [name, v] of Object.entries(derived.projects ?? {})) {
    if (v.status === 'active') active.push(name);
    else if (v.status === 'undeclared') undeclared.push(name);
    if (v.conflict) conflicts.push(`${name} (${v.conflict})`);
  }
}

// --- Alerts: only what calls for an action ---
const alerts = [];
if (derivedAge === null) alerts.push('project status not computed → node TOOLS/project-scan.mjs');
else if (derivedAge > 7) alerts.push(`project status is ${derivedAge} d old → node TOOLS/project-scan.mjs`);
if (!fs.existsSync(path.join(ROOT_DIR, '.relay-rag.json'))) alerts.push('RAG index missing → node TOOLS/rag-index.mjs');

const brain = safe(() => yaml.load(fs.readFileSync(BRAIN_PATH, 'utf8')), null);
if (!brain) alerts.push('relay-brain.yml unreadable → npm run init (or copy relay-brain.template.yml)');
else if (!brain?.machines?.[hostname]) alerts.push(`machine ${hostname} missing from relay-brain.yml → npm run init`);

// Split brain (opt-in): problems reported by relay-brain.mjs, which the hook runs without
// visible output, plus the versions set aside on conflict. Without this line they stay silent.
if (fs.existsSync(BRAIN_DIR)) {
  safe(() => { for (const a of JSON.parse(fs.readFileSync(ALERTS_PATH, 'utf8')).alerts ?? []) alerts.push(a); });
  const n = conflictCount();
  if (n) alerts.push(`${n} brain version(s) in conflict in BRAIN/_conflicts/ — compare with the brain, merge by hand, delete`);
}

// Skill descriptions enter the system prompt on EVERY session (~0.37 token/byte
// measured). Without a guard rail, adding skills silently takes back what was saved
// elsewhere. On-demand skills (`disable-model-invocation: true`) cost nothing there.
safe(() => {
  let total = 0;
  const dir = path.join(ROOT_DIR, '.claude', 'skills');
  for (const s of fs.readdirSync(dir)) {
    const f = path.join(dir, s, 'SKILL.md');
    if (!fs.existsSync(f)) continue;
    const meta = skillMeta(f);
    if (!meta || isOnDemand(meta)) continue;
    total += Buffer.byteLength(String(meta.description ?? ''), 'utf8') + s.length;
  }
  if (total > SKILLS_DESC_BUDGET) {
    alerts.push(`skill descriptions at ${total} B (~${Math.round(total * 0.37)} tk/session, budget ${SKILLS_DESC_BUDGET}) → shorten, or make some on demand`);
  }
});

// Proposals from agents waiting for review (INBOX/<host>/*.md). Count only.
safe(() => {
  let n = 0;
  const inbox = path.join(ROOT_DIR, 'INBOX');
  for (const d of fs.readdirSync(inbox, { withFileTypes: true })) {
    if (!d.isDirectory() || d.name.startsWith('_') || d.name.startsWith('.')) continue;
    n += fs.readdirSync(path.join(inbox, d.name)).filter((f) => f.endsWith('.md')).length;
  }
  if (n) alerts.push(`${n} proposal(s) pending in INBOX/ → /inbox`);
});

// --- On-demand skills: read from each SKILL.md frontmatter ---
const manifest = safe(() => JSON.parse(fs.readFileSync(manifestPath, 'utf8')), { skills: [] });
const manifestBySrc = new Map((manifest.skills ?? []).map((s) => [canon(path.join(ROOT_DIR, 'SKILLS', s.src)), s]));
const onDemandSkills = [];
for (const f of skillFiles(path.join(ROOT_DIR, 'SKILLS'))) {
  const meta = safe(() => skillMeta(f), null);
  if (!isOnDemand(meta)) continue;
  const entry = manifestBySrc.get(canon(path.dirname(f)));
  onDemandSkills.push({
    name: entry?.name || (typeof meta.name === 'string' && meta.name) || path.basename(path.dirname(f)),
    src: path.relative(path.join(ROOT_DIR, 'SKILLS'), path.dirname(f)).replace(/\\/g, '/'),
    // Optional manifest hint: a regex tested against a project's file and dependency names.
    suggest_if: entry?.suggest_if, suggest_why: entry?.suggest_why,
  });
}

// ─── General state ───────────────────────────────────────────────────────────
const lines = [`# Relay — state as of ${today} (${hostname})`, ''];
if (lastSessions.length) lines.push(`**Recent sessions**: ${lastSessions.join(' · ')}`);
if (lastEntry.length) lines.push('', ...lastEntry);
lines.push('');
lines.push(`**Active projects**: ${active.length ? active.join(', ') : 'none'}`);
if (undeclared.length) lines.push(`**Undeclared**: ${undeclared.join(', ')}`);
if (conflicts.length) lines.push(`**To settle**: ${conflicts.join(' · ')}`);
if (alerts.length) lines.push(`**Alerts**: ${alerts.join(' · ')}`);

// Catalogue of skills absent from the system prompt. Without this line an on-demand
// skill would be invisible: you cannot search for what you do not know exists.
if (onDemandSkills.length) {
  lines.push('');
  lines.push(`**On-demand skills** (not in the system prompt): ${onDemandSkills.map((s) => s.name).join(' · ')} — `
    + 'the user types `/name`; an agent reads `SKILLS/<src>/SKILL.md` (`rag-query.mjs --type skill` to search).');
}

lines.push('');
lines.push('Detail → `node TOOLS/rag-query.mjs --query "…" [--full]` (query the RAG before opening a file).');
lines.push('History → `RELAY_LOG.md`, newest first (write in `LOG/<host>.md`). Machines and projects → `relay-brain.yml`.');

let out = lines.join('\n') + '\n';
if (Buffer.byteLength(out, 'utf8') > MAX_BYTES) {
  // The body of the latest log entry is the first thing sacrificed.
  const drop = new Set(lastEntry);
  out = lines.filter((l) => !drop.has(l)).join('\n') + '\n';
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
  const head = new RegExp(`\\(\\s*${esc(name)}\\s*[:)]`, 'i');
  const tag = new RegExp(`^\\s*-\\s*\\[${esc(name)}\\]`, 'i');
  const hits = [];
  for (const s of logSections) {
    const h = s.split('\n')[0];
    const all = s.split('\n').filter((l) => l.trim().startsWith('- '));
    const own = all.filter((l) => tag.test(l));
    if (!head.test(h) && !own.length) continue;
    // A session often touches several projects: serve this project's tagged bullets first.
    const bullets = [...own, ...all.filter((l) => !own.includes(l))].slice(0, 3).map((l) => clip(l.trim(), 105));
    hits.push({ date: h.match(/^## (\d{4}-\d{2}-\d{2})/)?.[1] ?? '?', subject: clip(h.replace(HEAD_PREFIX, ''), 70), bullets });
    if (hits.length === 2) break;
  }
  return hits;
}

/** A short fingerprint of what the project contains: top-level file names plus dependency names. */
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
for (const [name, v] of Object.entries(derived?.projects ?? {})) {
  // `elsewhere` = a project on another machine: no directory here, no state to serve.
  if (v.status === 'absent' || v.status === 'elsewhere') continue;
  // A closed project has no resumption to prepare: the directory may be there, the decision is made.
  if (TERMINAL_INTENT.has(v.intent) || TERMINAL_INTENT.has(v.status)) continue;

  let dir = safe(() => projectPath(name, brain ?? {}), null);
  if (!dir) {
    const guess = path.join(ROOT_DIR, 'PROJECTS', name);
    if (fs.existsSync(guess)) dir = guess;
  }
  if (!dir || !fs.existsSync(dir)) continue;
  // A project whose path is the Relay root would hijack every session.
  if (canon(dir) === canon(ROOT_DIR)) continue;

  const L = [`# Relay — ${name} (${v.status}${v.last_activity ? `, last activity ${v.last_activity}` : ''})`, ''];
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
  const local = ['AGENTS.md', 'CLAUDE.md', 'GEMINI.md', 'README.md'].filter((f) => fs.existsSync(path.join(dir, f)));
  if (local.length) L.push(`**Local rules**: ${local.map((f) => '`' + f + '`').join(' · ')}`);

  // On-demand skills that fit what the project contains. A routing skill does not trigger on
  // its own (measured 0/3 projects), so this line is the only carrier of the suggestion.
  const fp = fingerprint(dir);
  const suggested = onDemandSkills
    .filter((s) => s.suggest_if && safe(() => new RegExp(s.suggest_if, 'i').test(fp), false))
    .map((s) => '`' + s.name + '`' + (s.suggest_why ? ` (${s.suggest_why})` : ''));
  if (suggested.length) L.push(`**Fitting methods**: ${suggested.join(' · ')} — \`/name\`, or read its \`SKILL.md\`.`);

  L.push(`Detail → \`node TOOLS/rag-query.mjs --query "${name}" --type project --full\``);
  L.push(`Other active projects: ${active.filter((a) => a !== name).join(', ') || 'none'}`);

  let body = L.join('\n') + '\n';
  if (Buffer.byteLength(body, 'utf8') > MAX_BYTES) body = L.filter((l) => !l.startsWith('  - ')).join('\n') + '\n';
  projectStates.push({ name, dir: path.resolve(dir), file: `${slug(name)}.md`, body });
}

if (toStdout) {
  process.stdout.write(out);
} else {
  const wrote = writeIfChanged(outPath, out, { ignore: null });
  console.log(`${wrote ? '✓' : '='} .relay-state.md — ${Buffer.byteLength(out, 'utf8')} B (cap ${MAX_BYTES})`);

  // Index read by the hook: saves pulling js-yaml and path resolution into it.
  const wanted = new Map(projectStates.map((p) => [p.file, p.body]));
  wanted.set('index.json', JSON.stringify(projectStates.map((p) => ({ name: p.name, dir: p.dir, file: p.file }))));

  const unchanged = safe(() => {
    const present = fs.readdirSync(stateDir);
    return present.length === wanted.size
      && present.every((f) => wanted.has(f) && fs.readFileSync(path.join(stateDir, f), 'utf8') === wanted.get(f));
  }, false);

  // Full regeneration — a removed project must not leave a ghost state — but by swap: the
  // hook reads this directory on every session start, and an in-place rmSync would show it
  // an empty directory while it was being refilled.
  if (!unchanged) {
    swapDir(stateDir, (dir) => {
      for (const [file, body] of wanted) fs.writeFileSync(path.join(dir, file), body, 'utf8');
    });
  }

  const avg = projectStates.length
    ? Math.round(projectStates.reduce((n, p) => n + Buffer.byteLength(p.body, 'utf8'), 0) / projectStates.length)
    : 0;
  console.log(`${unchanged ? '=' : '✓'} .relay-state/ — ${projectStates.length} project(s), ${avg} B on average${unchanged ? ' (unchanged)' : ''}`);
}
