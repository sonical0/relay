#!/usr/bin/env node
/**
 * relay-log.mjs — the Relay log: one file per machine, RELAY_LOG.md generated.
 *
 * Usage:
 *   node TOOLS/relay-log.mjs [--dry-run]     Merge LOG/<host>.md files → RELAY_LOG.md
 *   node TOOLS/relay-log.mjs --migrate       Once: the entries of RELAY_LOG.md become LOG/<this host>.md
 *   node TOOLS/relay-log.mjs --where         Path of this machine's file
 *
 * Why: file sync clients copy a whole file without merging lines — the last copy to
 * arrive wins. On the internal system this framework comes from, two entries written on
 * one machine vanished while a session on another machine wrote the same log. Each
 * machine now writes only LOG/<host>.md (newest entry on top), and nobody writes
 * RELAY_LOG.md by hand: it is rebuilt by merging, newest first. A machine that
 * overwrites another's generated copy erases nothing: the next merge redoes it from
 * the sources.
 *
 * The generated file ends with one trailer line, the fingerprint of every header it holds:
 *   <!-- relay-log generated: <sha8> <sha8> ... -->      (sha8 = sha1(header line, trimmed))
 * An entry of RELAY_LOG.md missing from that list was written there by hand. Recent ones
 * (≤ 2 days) are moved into this machine's file, marked `<!-- adopted … -->`, instead of
 * being lost at regeneration. A RELAY_LOG.md without trailer (written before this tool)
 * has all its unknown entries adopted, whatever their date.
 *
 * No LOG/*.md and no entry in RELAY_LOG.md: nothing to do, nothing touched (exit 0).
 * No LOG/*.md but entries in RELAY_LOG.md: they are migrated into LOG/<this host>.md first.
 */
import fs from 'fs';
import path from 'path';
import crypto from 'crypto';
import { fileURLToPath } from 'url';
import { writeAtomic, writeIfChanged, withLock } from './lib/atomic.mjs';
import { ROOT_DIR, samePath, hostName } from './lib/brain.mjs';

export const LOG_PATH = path.join(ROOT_DIR, 'RELAY_LOG.md');
export const LOG_DIR = path.join(ROOT_DIR, 'LOG');
export const ARCHIVE_DIR = path.join(LOG_DIR, 'ARCHIVE');
export const hostLogPath = (host = hostName()) => path.join(LOG_DIR, `${host}.md`);

const ADOPT_DAYS = 2;
const ADOPTED = /^<!-- adopted from RELAY_LOG\.md.*?-->\n/m;
export const GENERATED = /^<!-- relay-log generated: ([0-9a-f ]*) -->\s*$/m;
export const headHash = (head) => crypto.createHash('sha1').update(head.trim()).digest('hex').slice(0, 8);

const localDay = (d = new Date()) => new Intl.DateTimeFormat('en-CA').format(d);

/** Entries of RELAY_LOG.md written by hand: missing from its fingerprint list and from every
    source or archive, and recent. Without a list (file older than this format), any age. */
export function handWritten(text, known) {
  const m = text.match(GENERATED);
  const generated = new Set(m ? m[1].split(' ').filter(Boolean) : []);
  const since = m ? localDay(new Date(Date.now() - ADOPT_DAYS * 86400000)) : '';
  return parseLog(text.replace(GENERATED, '')).sections
    .filter((s) => s.date && s.date >= since && !generated.has(headHash(s.head)) && !known.has(s.head));
}

const PREAMBLE = [
  '# RELAY_LOG — shared activity log',
  '',
  '> **Generated file — do not write here.** Each machine writes at the top of `LOG/<host>.md`;',
  '> `node TOOLS/relay-log.mjs` merges those files here, newest first (run after every answer',
  '> by the Stop hook). A direct write here is moved to your machine\'s file at the next merge.',
  '>',
  '> Entry format: a level-2 heading `YYYY-MM-DD HH:MM — Agent (project: subject)`, then at',
  '> most 5 bullets `- [project] action → measured result`. See RULES/journal.md.',
  '',
  '',
].join('\n');

const hostPreamble = (host) => [
  `# Relay log — ${host}`,
  '',
  `> Entries written on ${host}, newest on top. Only this machine writes this file;`,
  '> `RELAY_LOG.md` is generated from every `LOG/*.md`. Format: RULES/journal.md.',
  '',
  '',
].join('\n');

/** Splits a log file: preamble, then `## …` sections (one entry each). */
export function parseLog(text) {
  text = text.replace(/\r\n/g, '\n');
  const first = text.search(/^## /m);
  const preamble = first < 0 ? text : text.slice(0, first);
  const sections = first < 0 ? [] : text.slice(first).split(/^(?=## )/m).map((raw) => {
    const head = raw.slice(0, raw.indexOf('\n') < 0 ? raw.length : raw.indexOf('\n')).trim();
    const m = head.match(/^## (\d{4}-\d{2}-\d{2})(?: (\d{2}:\d{2}))?/);
    return {
      head,
      date: m?.[1] ?? '',
      time: m?.[2] ?? '',
      adopted: ADOPTED.test(raw),
      body: raw.trimEnd() + '\n',
    };
  });
  return { preamble, sections };
}

const keyOf = (s) => `${s.date} ${s.time}`;
const readOr = (p, fallback = '') => { try { return fs.readFileSync(p, 'utf8'); } catch { return fallback; } };

/** Source files: LOG/*.md (one per machine), in a stable order. */
export function sourceFiles() {
  let names = [];
  try { names = fs.readdirSync(LOG_DIR).filter((f) => f.endsWith('.md')); } catch {}
  return names.sort().map((f) => path.join(LOG_DIR, f));
}

function archivedHeads() {
  const heads = new Set();
  let names = [];
  try { names = fs.readdirSync(ARCHIVE_DIR).filter((f) => f.endsWith('.md')); } catch {}
  for (const f of names) {
    for (const m of readOr(path.join(ARCHIVE_DIR, f)).matchAll(/^## .*$/gm)) heads.add(m[0].trim());
  }
  return heads;
}

/**
 * Merge of lists already sorted newest first: each source keeps its internal order (breaks
 * included), only the heads are compared. Deduplicated by heading; an adopted copy gives way
 * to the original.
 */
export function mergeSections(lists) {
  const byHead = new Map();
  for (const list of lists) {
    for (const s of list) {
      const prev = byHead.get(s.head);
      if (!prev || (prev.adopted && !s.adopted)) byHead.set(s.head, s);
    }
  }
  const idx = lists.map(() => 0);
  const out = [];
  const seen = new Set();
  for (;;) {
    let best = -1;
    for (let i = 0; i < lists.length; i++) {
      if (idx[i] >= lists[i].length) continue;
      if (best < 0 || keyOf(lists[i][idx[i]]) > keyOf(lists[best][idx[best]])) best = i;
    }
    if (best < 0) break;
    const s = lists[best][idx[best]++];
    if (seen.has(s.head)) continue;
    seen.add(s.head);
    out.push(byHead.get(s.head));
  }
  return out;
}

/** Inserts entries into a sorted list (newest on top) without reordering the rest. */
export function insertSorted(list, items) {
  const out = [...list];
  for (const it of items) {
    const at = out.findIndex((s) => keyOf(s) < keyOf(it));
    out.splice(at < 0 ? out.length : at, 0, it);
  }
  return out;
}

/**
 * Adds entries to this machine's file at their chronological place (the merge assumes each
 * source is sorted), under the file's lock. Headings already present are skipped.
 * `mark`: line inserted under the heading. Returns the number added.
 */
export function addEntries(sections, { mark = '' } = {}) {
  const own = hostLogPath();
  return withLock(own, () => {
    const cur = parseLog(readOr(own));
    const have = new Set(cur.sections.map((s) => s.head));
    const add = sections.filter((s) => !have.has(s.head)).map((s) => {
      if (!mark) return s;
      const nl = s.body.indexOf('\n');
      return { ...s, adopted: true, body: s.body.slice(0, nl + 1) + mark + s.body.slice(nl + 1) };
    });
    if (!add.length) return 0;
    fs.mkdirSync(LOG_DIR, { recursive: true });
    writeAtomic(own, (cur.preamble.trim() ? cur.preamble : hostPreamble(hostName()))
      + insertSorted(cur.sections, add).map((s) => s.body).join('\n'));
    return add.length;
  });
}

/** Migration: the entries of RELAY_LOG.md become this machine's file. Throws when LOG/ already has sources. */
export function migrate({ log = console.log } = {}) {
  const own = hostLogPath();
  if (sourceFiles().length) throw new Error(`already migrated: ${path.relative(ROOT_DIR, LOG_DIR)}/ holds ${sourceFiles().map((f) => path.basename(f)).join(', ')}`);
  const { sections } = parseLog(readOr(LOG_PATH).replace(GENERATED, ''));
  if (!sections.length) return 0;
  fs.mkdirSync(LOG_DIR, { recursive: true });
  writeAtomic(own, hostPreamble(hostName()) + sections.map((s) => s.body).join('\n'));
  log(`✓ ${sections.length} entr${sections.length > 1 ? 'ies' : 'y'} → ${path.relative(ROOT_DIR, own)}`);
  return sections.length;
}

/**
 * Moves the entries written by hand in RELAY_LOG.md into this machine's file, then regenerates it.
 * Returns { adopted: string[], written: boolean, count: number, sources: number, skipped? }.
 */
export function mergeLog({ dryRun = false, log = console.log } = {}) {
  if (sourceFiles().length === 0) {
    // Nothing to merge: either a fresh install (leave the shipped stub alone) or a log
    // written before the per-machine files (migrate it first).
    if (!parseLog(readOr(LOG_PATH)).sections.length) {
      return { adopted: [], written: false, count: 0, sources: 0, skipped: 'no LOG/*.md and no entry — nothing to do' };
    }
    if (dryRun) return { adopted: [], written: false, count: 0, sources: 0, skipped: '[dry-run] would migrate RELAY_LOG.md entries into LOG/' };
    migrate({ log });
  }
  return withLock(LOG_PATH, () => {
    const read = () => sourceFiles().map((f) => ({ file: f, ...parseLog(readOr(f)) }));
    let parsed = read();
    const known = new Set([...parsed.flatMap((p) => p.sections.map((s) => s.head)), ...archivedHeads()]);

    // --- Entries written directly in RELAY_LOG.md ---
    const strays = handWritten(readOr(LOG_PATH), known);
    if (strays.length && !dryRun) {
      addEntries(strays, { mark: `<!-- adopted from RELAY_LOG.md on ${localDay()}: written outside the machine's file -->\n` });
      parsed = read();
    }
    for (const s of strays) log(`${dryRun ? '[dry-run] ' : ''}↺ moved into ${path.basename(hostLogPath())}: ${s.head}`);

    // --- Regeneration ---
    const merged = mergeSections(parsed.map((p) => p.sections));
    const out = PREAMBLE + merged.map((s) => s.body.replace(ADOPTED, '')).join('\n')
      + `\n<!-- relay-log generated: ${merged.map((s) => headHash(s.head)).join(' ')} -->\n`;
    const written = dryRun ? false : writeIfChanged(LOG_PATH, out, { ignore: null });
    return { adopted: strays.map((s) => s.head), written, count: merged.length, sources: parsed.length };
  }, { timeoutMs: 30_000 });
}

// --- CLI ---
if (process.argv[1] && samePath(fileURLToPath(import.meta.url), process.argv[1])) {
  const args = process.argv.slice(2);
  try {
    if (args.includes('--where')) {
      console.log(hostLogPath());
    } else {
      if (args.includes('--migrate')) migrate();
      const r = mergeLog({ dryRun: args.includes('--dry-run') });
      if (r.skipped) console.log(`= ${r.skipped}`);
      else console.log(`${r.written ? '✓' : '='} RELAY_LOG.md — ${r.count} entr${r.count === 1 ? 'y' : 'ies'} from ${r.sources} source(s)${r.written ? '' : ' (unchanged)'}`);
    }
  } catch (e) {
    console.error(`✗ ${e.message}`);
    process.exit(1);
  }
}
