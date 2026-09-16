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
 *   rules    AGENTS.md, CLAUDE.md, GEMINI.md, {AGENT}.md, SETUP.md
 *   brain    relay-brain.yml, relay-projects.derived.yml
 *   log      RELAY_LOG.md and LOG/ARCHIVE/*.md
 *   project  PROJECTS/<name>/**.md   (also tagged with the project name)
 *   skill    SKILLS/**\/SKILL.md
 *
 * Re-run it after a batch of edits. `npm run state` does not: indexing walks the
 * whole tree, and the startup hook must stay instant.
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { tokenize } from './lib/rag.mjs';
import { writeAtomic } from './lib/atomic.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.join(__dirname, '..');
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
  if (['AGENTS.md', 'SETUP.md', 'README.md', 'RELAY_LOG.md'].includes(e)) continue;
  addFile(path.join(rootDir, e), 'rules');
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

writeAtomic(outPath, JSON.stringify(index), (t) => JSON.parse(t));
const kb = Math.round(fs.statSync(outPath).size / 1024);
console.log(`→ .relay-rag.json — ${chunks.length} chunks, ${kb} KB`);
