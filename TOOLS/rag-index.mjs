#!/usr/bin/env node
/**
 * rag-index.mjs — builds the local search index read by rag-query.mjs.
 *
 * Usage:
 *   node TOOLS/rag-index.mjs [--stats] [--json]
 *
 * Writes `.relay-rag.json`: one entry per markdown section, with term frequencies
 * and the document frequencies BM25 needs. No native dependency — see lib/rag.mjs
 * for why.
 *
 * What is indexed, and under which `--type`:
 *   rules       AGENTS.md, CLAUDE.md, GEMINI.md, {AGENT}.md, SETUP.md, RULES/*.md
 *   convention  DECISIONS.md — one chunk per `## ` entry (dated decisions and their reasons)
 *   brain       relay-brain.yml, relay-projects.derived.yml
 *   log         RELAY_LOG.md and LOG/ARCHIVE/*.md
 *   project     PROJECTS/<name>/**.md   (also tagged with the project name)
 *   skill       SKILLS/**\/SKILL.md
 *
 * The end-of-turn hook (relay-after-turn.mjs) re-runs it only when one of its inputs
 * changed; the file is not rewritten when the index is identical.
 */

import fs from 'fs';
import path from 'path';
import { tokenize } from './lib/rag.mjs';
import { writeIfChanged } from './lib/atomic.mjs';
import { ROOT_DIR as rootDir } from './lib/brain.mjs';

const outPath = path.join(rootDir, '.relay-rag.json');

const args = process.argv.slice(2);
const showStats = args.includes('--stats');
const asJson = args.includes('--json');

const SKIP_DIRS = new Set(['node_modules', '.git', 'dist', 'build', 'venv', '.venv',
  'output', '.cache', '.next', 'target', '.claude', '.gemini', '.relay-state']);
const MAX_CHUNK_CHARS = 4000;   // a section longer than this is split
const MIN_CHUNK_CHARS = 40;     // a heading with no body carries nothing

const chunks = [];

/** Splits markdown on `##`/`###` headings; long sections are cut on paragraph breaks. */
function splitMarkdown(text, fallbackTitle) {
  const parts = [];
  const sections = text.split(/^(?=#{2,3} )/m);
  for (const s of sections) {
    const trimmed = s.trim();
    if (trimmed.length < MIN_CHUNK_CHARS) continue;
    const title = trimmed.match(/^#{1,6} +(.+)$/m)?.[1]?.trim() ?? fallbackTitle;
    if (trimmed.length <= MAX_CHUNK_CHARS) { parts.push({ title, text: trimmed }); continue; }
    let buf = '';
    for (const para of trimmed.split(/\n{2,}/)) {
      if (buf && buf.length + para.length > MAX_CHUNK_CHARS) {
        parts.push({ title, text: buf.trim() });
        buf = '';
      }
      buf += para + '\n\n';
    }
    if (buf.trim().length >= MIN_CHUNK_CHARS) parts.push({ title, text: buf.trim() });
  }
  return parts;
}

/** Splits YAML on top-level keys, so a query lands on one section of the brain. */
function splitYaml(text, fallbackTitle) {
  const parts = [];
  const sections = text.split(/^(?=[a-zA-Z_][\w-]*:)/m);
  for (const s of sections) {
    const trimmed = s.trim();
    if (trimmed.length < MIN_CHUNK_CHARS) continue;
    const title = trimmed.match(/^([a-zA-Z_][\w-]*):/)?.[1] ?? fallbackTitle;
    parts.push({ title: `${fallbackTitle} → ${title}`, text: trimmed.slice(0, MAX_CHUNK_CHARS) });
  }
  return parts.length ? parts : [{ title: fallbackTitle, text: text.slice(0, MAX_CHUNK_CHARS) }];
}

function addFile(file, type, extra = {}) {
  let text;
  try { text = fs.readFileSync(file, 'utf8'); } catch { return; }
  if (!text.trim()) return;
  const rel = path.relative(rootDir, file).replace(/\\/g, '/');
  const base = path.basename(file);
  const parts = /\.ya?ml$/.test(file) ? splitYaml(text, base) : splitMarkdown(text, base);
  for (const [i, p] of parts.entries()) {
    chunks.push({ id: `${rel}#${i}`, type, path: rel, title: p.title, text: p.text, ...extra });
  }
}

function walkMarkdown(dir, fn, depth = 0) {
  if (depth > 6) return;
  let entries;
  try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
  for (const e of entries) {
    if (SKIP_DIRS.has(e.name)) continue;
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walkMarkdown(p, fn, depth + 1);
    else if (/\.mdx?$/.test(e.name)) fn(p);
  }
}

// --- rules: the agent-facing documents at the root ---
for (const f of ['AGENTS.md', 'SETUP.md']) {
  const p = path.join(rootDir, f);
  if (fs.existsSync(p)) addFile(p, 'rules');
}
for (const e of fs.readdirSync(rootDir)) {
  // {AGENT}.md — uppercase basename, excluding the ones already handled and the READMEs.
  if (!/^[A-Z][A-Z0-9_]*\.md$/.test(e)) continue;
  if (['AGENTS.md', 'SETUP.md', 'README.md', 'RELAY_LOG.md', 'DECISIONS.md'].includes(e)) continue;
  addFile(path.join(rootDir, e), 'rules');
}
// RULES/*.md — rules scoped to an area, copied into .claude/rules/ by skills-link.mjs.
const rulesDir = path.join(rootDir, 'RULES');
if (fs.existsSync(rulesDir)) {
  for (const f of fs.readdirSync(rulesDir).sort()) {
    if (f.endsWith('.md')) addFile(path.join(rulesDir, f), 'rules');
  }
}

// --- convention: DECISIONS.md, one chunk per `## ` entry ---
// A decision is read whole or not at all: `###` sub-headings stay inside their entry. An entry
// longer than MAX_CHUNK_CHARS is still cut on paragraph breaks, under the same title.
const decisionsPath = path.join(rootDir, 'DECISIONS.md');
if (fs.existsSync(decisionsPath)) {
  const text = fs.readFileSync(decisionsPath, 'utf8').replace(/\r\n/g, '\n');
  const entries = text.split(/^(?=## )/m).filter((s) => s.startsWith('## '));
  let i = 0;
  for (const entry of entries) {
    const title = entry.split('\n')[0].replace(/^## +/, '').trim();
    const trimmed = entry.trim();
    const pieces = [];
    if (trimmed.length <= MAX_CHUNK_CHARS) pieces.push(trimmed);
    else {
      let buf = '';
      for (const para of trimmed.split(/\n{2,}/)) {
        if (buf && buf.length + para.length > MAX_CHUNK_CHARS) { pieces.push(buf.trim()); buf = ''; }
        buf += para + '\n\n';
      }
      if (buf.trim()) pieces.push(buf.trim());
    }
    for (const p of pieces) {
      chunks.push({ id: `DECISIONS.md#${i++}`, type: 'convention', path: 'DECISIONS.md', title, text: p });
    }
  }
}

// --- brain ---
for (const f of ['relay-brain.yml', 'relay-projects.derived.yml']) {
  const p = path.join(rootDir, f);
  if (fs.existsSync(p)) addFile(p, 'brain');
}

// --- log ---
const logPath = path.join(rootDir, 'RELAY_LOG.md');
if (fs.existsSync(logPath)) addFile(logPath, 'log');
const archiveDir = path.join(rootDir, 'LOG', 'ARCHIVE');
if (fs.existsSync(archiveDir)) {
  for (const f of fs.readdirSync(archiveDir)) {
    if (f.endsWith('.md')) addFile(path.join(archiveDir, f), 'log');
  }
}

// --- projects ---
const projectsDir = path.join(rootDir, 'PROJECTS');
if (fs.existsSync(projectsDir)) {
  for (const e of fs.readdirSync(projectsDir, { withFileTypes: true })) {
    if (!e.isDirectory() || e.name === '_template') continue;
    walkMarkdown(path.join(projectsDir, e.name), (f) => addFile(f, 'project', { project: e.name }));
  }
}

// --- skills ---
const skillsDir = path.join(rootDir, 'SKILLS');
if (fs.existsSync(skillsDir)) {
  walkMarkdown(skillsDir, (f) => {
    if (path.basename(f) !== 'SKILL.md') return;
    if (f.includes(`${path.sep}_template${path.sep}`)) return;
    const name = path.basename(path.dirname(f));
    addFile(f, 'skill', { skill: name });
  });
}

// --- term frequencies and document frequencies ---
const df = Object.create(null);
let totalLen = 0;
for (const c of chunks) {
  const terms = tokenize(`${c.title} ${c.text}`);
  const tf = Object.create(null);
  for (const t of terms) tf[t] = (tf[t] ?? 0) + 1;
  c.tf = tf;
  c.len = terms.length;
  c.titleTokens = tokenize(c.title);
  totalLen += terms.length;
  for (const t of Object.keys(tf)) df[t] = (df[t] ?? 0) + 1;
}

const index = {
  generated: new Date().toISOString(),
  count: chunks.length,
  avgdl: chunks.length ? totalLen / chunks.length : 1,
  df,
  chunks,
};

if (asJson) {
  console.log(JSON.stringify({ status: 'ok', count: chunks.length }));
} else {
  const byType = {};
  for (const c of chunks) byType[c.type] = (byType[c.type] ?? 0) + 1;
  console.log(Object.entries(byType).map(([k, n]) => `${k}: ${n}`).join(' · ') || 'nothing indexed');
  if (showStats) {
    const terms = Object.keys(df).length;
    console.log(`${terms} distinct terms · average chunk ${Math.round(index.avgdl)} terms`);
  }
}

// The time of day alone does not make a change: an identical index keeps the file and its mtime
// for the day (rag-query reports the index age from the date).
const wrote = writeIfChanged(outPath, JSON.stringify(index),
  { ignore: /T\d{2}:\d{2}:\d{2}(\.\d+)?Z/, validate: (t) => JSON.parse(t) });
const kb = Math.round(fs.statSync(outPath).size / 1024);
if (!wrote) console.log('= index unchanged');
console.log(`→ .relay-rag.json — ${chunks.length} chunks, ${kb} KB`);
