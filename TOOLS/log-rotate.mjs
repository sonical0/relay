#!/usr/bin/env node
/**
 * log-rotate.mjs — keeps this machine's log (LOG/<host>.md) down to a useful size.
 *
 * Usage:
 *   node TOOLS/log-rotate.mjs [--dry-run] [--keep 2] [--brain]
 *
 * - Keeps the current month plus the previous one (`--keep`, default 2) in LOG/<host>.md.
 * - Moves older entries to LOG/ARCHIVE/YYYY-MM-<host>.md (newest first; the RAG still
 *   indexes them under `--type log`), then regenerates RELAY_LOG.md (relay-log.mjs).
 *   Each machine only rotates its own file and writes its own archives: two machines
 *   rewriting one synced file overwrite each other.
 * - Sections whose heading has no `## YYYY-MM-DD` date go to LOG/ARCHIVE/undated-<host>.md.
 * - `--brain` also moves `cross_project_log` entries older than 30 days from
 *   relay-brain.yml to relay-brain-history.yml. The entries are removed as text blocks,
 *   so the comments of the brain survive.
 *
 * LOSS GUARD: nothing is written unless the characters out match the characters in.
 */

import fs from 'fs';
import path from 'path';
import yaml from 'js-yaml';
import { writeAtomic as atomicWrite, withLock } from './lib/atomic.mjs';
import { ROOT_DIR, BRAIN_PATH, hostName } from './lib/brain.mjs';
import { hostLogPath, mergeLog, ARCHIVE_DIR } from './relay-log.mjs';
import { parseMark, splitBrain, withMarkOf, validBrain } from './relay-brain.mjs';

const host = hostName();
const logPath = hostLogPath(host);
const historyPath = path.join(ROOT_DIR, 'relay-brain-history.yml');
const rel = (p) => path.relative(ROOT_DIR, p).replace(/\\/g, '/');

const args = process.argv.slice(2);
const dryRun = args.includes('--dry-run');
const doBrain = args.includes('--brain');
const keepIdx = args.indexOf('--keep');
const keepMonths = keepIdx !== -1 ? Math.max(1, parseInt(args[keepIdx + 1], 10) || 2) : 2;

function monthsAgo(n) {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

function writeAtomic(target, content, validate) {
  fs.mkdirSync(path.dirname(target), { recursive: true });
  atomicWrite(target, content, validate);
}

/** Prepends sections under an archive's preamble: the archive stays newest first. */
function prepend(file, header, list) {
  const existing = fs.existsSync(file) ? fs.readFileSync(file, 'utf8').replace(/\r\n/g, '\n') : header;
  const split = existing.search(/^## /m);
  const head = split >= 0 ? existing.slice(0, split) : existing;
  const old = split >= 0 ? existing.slice(split) : '';
  return head + list.join('') + old;
}

// --- Log rotation ---
const original = fs.existsSync(logPath) ? fs.readFileSync(logPath, 'utf8').replace(/\r\n/g, '\n') : '';
if (!original) console.log(`${rel(logPath)} missing — nothing to rotate.`);

const firstHeader = original.search(/^## /m);
const preamble = firstHeader === -1 ? original : original.slice(0, firstHeader);
const body = firstHeader === -1 ? '' : original.slice(firstHeader);
const sections = body.split(/^(?=## )/m).filter((s) => s.startsWith('## '));
const cutoff = monthsAgo(keepMonths - 1);   // keep this month + (keep - 1) previous ones

const keep = [];
const byMonth = new Map();
const undated = [];
for (const s of sections) {
  const m = s.match(/^## (\d{4})-(\d{2})-\d{2}/);
  if (!m) { undated.push(s); continue; }
  const month = `${m[1]}-${m[2]}`;
  if (month >= cutoff) keep.push(s);
  else {
    if (!byMonth.has(month)) byMonth.set(month, []);
    byMonth.get(month).push(s);
  }
}

if (sections.length) {
  console.log(`Sections: ${sections.length} (kept ${keep.length} · archived ${sections.length - keep.length - undated.length} · undated ${undated.length}) — threshold: month >= ${cutoff}`);
  for (const [m, list] of [...byMonth].sort()) console.log(`  ${m} → ${list.length} section(s)`);
}

const newLog = preamble + keep.join('');
const archives = new Map();
for (const [month, list] of byMonth) {
  const name = `${month}-${host}.md`;
  archives.set(name, prepend(path.join(ARCHIVE_DIR, name),
    `# RELAY_LOG archive — ${month} (${host})\n\n> Moved out of LOG/${host}.md by TOOLS/log-rotate.mjs. Newest first.\n\n`, list));
}
if (undated.length) {
  archives.set(`undated-${host}.md`, prepend(path.join(ARCHIVE_DIR, `undated-${host}.md`),
    '# RELAY_LOG archive — undated sections\n\n> Blocks without a dated heading. Archive only.\n\n', undated));
}

// Compare CHARACTERS, not lines: splitting the body into N groups adds N-1 boundary lines.
const charsIn = body.length;
const charsOut = keep.join('').length
  + [...byMonth.values()].reduce((n, l) => n + l.join('').length, 0)
  + undated.join('').length;
if (charsIn !== charsOut) {
  console.error(`✗ ABORTED — ${charsIn} characters in, ${charsOut} out. Nothing written.`);
  process.exit(1);
}

if (!archives.size) {
  if (sections.length) console.log('Nothing to archive.');
} else if (dryRun) {
  console.log(`[dry-run] ${rel(logPath)}: ${original.length} B → ${newLog.length} B`);
  for (const [name, c] of archives) console.log(`[dry-run] LOG/ARCHIVE/${name}: ${c.length} B`);
} else {
  fs.mkdirSync(ARCHIVE_DIR, { recursive: true });
  // Under the lock of this machine's file (the same one relay-log.mjs addEntries takes), and only
  // if it has not changed since it was read: an entry added meanwhile would be lost.
  withLock(logPath, () => {
    if (fs.readFileSync(logPath, 'utf8').replace(/\r\n/g, '\n') !== original) throw new Error(`${path.basename(logPath)} changed during the rotation — run again`);
    fs.copyFileSync(logPath, path.join(ARCHIVE_DIR, `${host}.bak`));
    for (const [name, c] of archives) writeAtomic(path.join(ARCHIVE_DIR, name), c);
    writeAtomic(logPath, newLog);
  });
  console.log(`✓ ${rel(logPath)}: ${original.length} B → ${newLog.length} B`);
  for (const name of archives.keys()) console.log(`✓ LOG/ARCHIVE/${name}`);
  // Archived entries leave the generated file too.
  const r = mergeLog({ log: () => {} });
  if (!r.skipped) console.log(`✓ RELAY_LOG.md regenerated — ${r.count} entr${r.count === 1 ? 'y' : 'ies'}`);
}

// --- cross_project_log: entries older than 30 days ---
if (doBrain) {
  if (!fs.existsSync(BRAIN_PATH)) {
    console.log('relay-brain.yml missing — --brain skipped.');
  } else {
    const raw = fs.readFileSync(BRAIN_PATH, 'utf8');
    const limitStr = new Intl.DateTimeFormat('en-CA').format(new Date(Date.now() - 30 * 86400000));
    let blocks;
    try { blocks = splitBrain(parseMark(raw).body); } catch (e) {
      console.error(`✗ relay-brain.yml: ${e.message} — --brain skipped`);
      process.exit(1);
    }
    // One block per entry, named <date>-<hash> by the split; undated entries ('0000-00-00') stay.
    const isOld = (b) => {
      const m = b.id.match(/^cross_project_log\/(\d{4}-\d{2}-\d{2})-[0-9a-f]{8}\.yml$/);
      return m && m[1] !== '0000-00-00' && m[1] < limitStr;
    };
    const old = blocks.filter(isOld);
    const total = blocks.filter((b) => b.id.startsWith('cross_project_log/') && !b.id.endsWith('/_.yml')).length;
    console.log(`cross_project_log: ${total} entr${total === 1 ? 'y' : 'ies'}, ${old.length} older than 30 days (< ${limitStr})`);

    if (old.length && !dryRun) {
      const entries = old.flatMap((b) => {
        // Re-indent the block as a top-level list to parse it on its own.
        try { return yaml.load(b.text.replace(/^ {2}/gm, '')) ?? []; } catch { return []; }
      });
      const kept = blocks.filter((b) => !isOld(b)).map((b) => b.text).join('');
      let remaining = 0;
      try {
        fs.mkdirSync(ARCHIVE_DIR, { recursive: true });
        fs.copyFileSync(BRAIN_PATH, path.join(ARCHIVE_DIR, 'relay-brain.bak'));
        // The brain first: if it fails, the history is not touched and nothing is duplicated.
        writeAtomic(BRAIN_PATH, withMarkOf(raw, kept), (text) => {
          const check = validBrain(parseMark(text).body);
          remaining = Array.isArray(check.cross_project_log) ? check.cross_project_log.length : 0;
          if (remaining !== total - old.length) throw new Error(`expected ${total - old.length} entries left, found ${remaining}`);
        });
        const hist = fs.existsSync(historyPath) ? (yaml.load(fs.readFileSync(historyPath, 'utf8')) ?? {}) : {};
        hist.entries = [...(hist.entries ?? []), ...entries];
        hist.last_archived = new Intl.DateTimeFormat('en-CA').format(new Date());
        writeAtomic(historyPath,
          '# relay-brain-history.yml — cross_project_log entries older than 30 days.\n'
          + '# Fed by TOOLS/log-rotate.mjs --brain.\n\n' + yaml.dump(hist, { lineWidth: 120 }),
          (t) => yaml.load(t));
        console.log(`✓ ${old.length} entr${old.length === 1 ? 'y' : 'ies'} → relay-brain-history.yml (${remaining} left)`);
      } catch (e) {
        console.error(`✗ ${e.message} — aborted`);
        process.exitCode = 1;
      }
    }
  }
}
