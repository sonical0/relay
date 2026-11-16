#!/usr/bin/env node
/**
 * rag-query.mjs — query the local index before opening a file.
 *
 * Usage:
 *   node TOOLS/rag-query.mjs --query "how does the log rotate" [options]
 *
 * Options:
 *   --type <kind>       restrict to rules | convention | project | skill | log | brain
 *   --project <name>    restrict to one project
 *   --limit <n>         number of results (default 5)
 *   --full              print the whole matching section instead of a snippet
 *   --json              machine-readable output
 *
 * An excerpt that forces you to open the file behind it costs more than no RAG at
 * all. When in doubt use `--full`: a whole section is capped at 4 KB, far below what
 * reading the source file would cost.
 */

import fs from 'fs';
import path from 'path';
import { ROOT_DIR as rootDir } from './lib/brain.mjs';
import { tokenize, bm25, bestWindow } from './lib/rag.mjs';

const indexPath = path.join(rootDir, '.relay-rag.json');

const args = process.argv.slice(2);
const flag = (name, fallback = null) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] && !args[i + 1].startsWith('--') ? args[i + 1] : fallback;
};

const query = flag('--query') ?? args.filter((a) => !a.startsWith('--'))[0];
const type = flag('--type');
const project = flag('--project');
const limit = Number(flag('--limit', '5')) || 5;
const full = args.includes('--full');
const asJson = args.includes('--json');

if (!query) {
  console.error('Usage: node TOOLS/rag-query.mjs --query "…" [--type rules|convention|project|skill|log|brain] [--project <name>] [--limit n] [--full] [--json]');
  process.exit(1);
}

if (!fs.existsSync(indexPath)) {
  console.error('No index — run: node TOOLS/rag-index.mjs');
  process.exit(1);
}

let index;
try {
  index = JSON.parse(fs.readFileSync(indexPath, 'utf8'));
} catch (e) {
  console.error(`Index unreadable (${e.message}) — rebuild: node TOOLS/rag-index.mjs`);
  process.exit(1);
}

const ageDays = Math.floor((Date.now() - new Date(index.generated).getTime()) / 86400000);

let pool = index.chunks;
if (type) pool = pool.filter((c) => c.type === type);
if (project) pool = pool.filter((c) => c.project === project);

const terms = tokenize(query);
const ranked = bm25(terms, pool, index.df, index.avgdl, index.chunks.length).slice(0, limit);

if (asJson) {
  console.log(JSON.stringify({
    status: 'ok',
    query, index_age_days: ageDays, searched: pool.length,
    results: ranked.map(({ chunk, score }) => ({
      score: Number(score.toFixed(3)),
      type: chunk.type, path: chunk.path, title: chunk.title,
      project: chunk.project ?? null, skill: chunk.skill ?? null,
      text: full ? chunk.text : bestWindow(chunk.text, terms),
    })),
  }, null, 2));
  process.exit(0);
}

if (!ranked.length) {
  console.log(`No result for "${query}"${type ? ` (--type ${type})` : ''} across ${pool.length} chunks.`);
  if (ageDays > 7) console.log(`Index is ${ageDays} d old — node TOOLS/rag-index.mjs`);
  process.exit(0);
}

for (const [i, { chunk, score }] of ranked.entries()) {
  const tag = chunk.project ?? chunk.skill ?? chunk.type;
  console.log(`\n${i + 1}. [${tag}] ${chunk.title}  ·  ${chunk.path}  (${score.toFixed(2)})`);
  console.log(full ? chunk.text : bestWindow(chunk.text, terms));
}

console.log(`\n${ranked.length}/${pool.length} chunks · index ${ageDays} d old`);
if (!full) console.log('Not enough? Re-run with --full before opening the source file.');
