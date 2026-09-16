#!/usr/bin/env node
/**
 * log-rotate.mjs — keeps RELAY_LOG.md down to the current month plus the previous one.
 *
 * Usage:
 *   node TOOLS/log-rotate.mjs [--dry-run] [--keep-months 2]
 *
 * Older entries move to `LOG/ARCHIVE/YYYY-MM.md`, which the RAG still indexes under
 * `--type log`. Nothing is lost; what changes is that the log the startup state
 * reads stays short.
 *
 * The log is sorted newest first and entries start with `## YYYY-MM-DD — …`. An
 * entry whose heading does not parse is kept in place rather than archived blindly.
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { writeAtomic } from './lib/atomic.mjs';

const rootDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const logPath = path.join(rootDir, 'RELAY_LOG.md');
const archiveDir = path.join(rootDir, 'LOG', 'ARCHIVE');

const args = process.argv.slice(2);
const dryRun = args.includes('--dry-run');
const keepIdx = args.indexOf('--keep-months');
const keepMonths = keepIdx >= 0 ? Math.max(1, Number(args[keepIdx + 1]) || 2) : 2;

if (!fs.existsSync(logPath)) {
  console.error('RELAY_LOG.md missing.');
  process.exit(1);
}

const content = fs.readFileSync(logPath, 'utf8');
const firstEntry = content.search(/^## \d{4}-\d{2}-\d{2}/m);
const header = firstEntry > 0 ? content.slice(0, firstEntry) : content;
const body = firstEntry > 0 ? content.slice(firstEntry) : '';

const entries = body.split(/^(?=## )/m).filter((s) => s.trim());

// Cut-off month: the current one minus (keepMonths - 1).
const now = new Date();
const cutoff = new Date(now.getFullYear(), now.getMonth() - (keepMonths - 1), 1);
const monthKey = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;

const kept = [];
const toArchive = new Map();   // "YYYY-MM" -> [entries]

for (const e of entries) {
  const m = e.match(/^## (\d{4})-(\d{2})-(\d{2})/);
  if (!m) { kept.push(e); continue; }          // unparsable heading: keep it in place
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  if (d >= cutoff) { kept.push(e); continue; }
  const key = monthKey(d);
  if (!toArchive.has(key)) toArchive.set(key, []);
  toArchive.get(key).push(e);
}

if (!toArchive.size) {
  console.log(`Nothing to rotate — ${entries.length} entries, all within the last ${keepMonths} month(s).`);
  process.exit(0);
}

const moved = [...toArchive.values()].reduce((n, a) => n + a.length, 0);
console.log(`${moved} entr${moved > 1 ? 'ies' : 'y'} → ${[...toArchive.keys()].sort().join(', ')}`);

if (dryRun) {
  console.log('[dry-run] nothing written.');
  process.exit(0);
}

fs.mkdirSync(archiveDir, { recursive: true });
for (const [key, list] of toArchive) {
  const file = path.join(archiveDir, `${key}.md`);
  // Newest first inside the archive too, and prepend so re-running stays correct.
  const existing = fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : `# RELAY_LOG archive — ${key}\n\n`;
  const split = existing.search(/^## \d{4}-\d{2}-\d{2}/m);
  const head = split > 0 ? existing.slice(0, split) : existing;
  const old = split > 0 ? existing.slice(split) : '';
  writeAtomic(file, head + list.join('') + old);
}

writeAtomic(logPath, header + kept.join(''));
console.log(`✓ RELAY_LOG.md — ${kept.length} entries kept · LOG/ARCHIVE/ updated`);
console.log('Re-index so the archive stays searchable: node TOOLS/rag-index.mjs');
