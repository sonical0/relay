#!/usr/bin/env node
/**
 * relay-brain.mjs — relay-brain.yml split into chunks (BRAIN/), and regenerated from them.
 *
 * Usage:
 *   node TOOLS/relay-brain.mjs [--dry-run]        Report edits of relay-brain.yml into BRAIN/,
 *                                                 then regenerate it
 *   node TOOLS/relay-brain.mjs --migrate          Once: split relay-brain.yml into BRAIN/
 *   node TOOLS/relay-brain.mjs --migrate --replace   Rebuild BRAIN/ from the current file
 *
 * OPT-IN. Without a BRAIN/ directory this tool does nothing (`syncBrain()` returns
 * `{status: 'off'}`): a single machine, or a brain kept in Git, does not need it.
 *
 * Why: file sync clients copy a whole file without merging. Two machines that edit the
 * brain within the same sync interval overwrite each other (the internal system this
 * framework comes from lost log entries exactly that way). So each block lives in its
 * own file:
 *   BRAIN/head.yml                      everything before the first split section
 *   BRAIN/<section>/_.yml               section header (key and comments)
 *   BRAIN/machines/<host>.yml           one machine        BRAIN/projects/<name>.yml  one project
 *   BRAIN/cross_project_log/<date>-<hash>.yml   one entry: two machines that each add one
 *                                               never write the same file
 *   BRAIN/<key>.yml                     any other top-level section, as one block
 *   BRAIN/eof.yml                       column-0 comments after the last section
 *
 * You keep editing relay-brain.yml as before. The generated file ends with the
 * fingerprint of each of its blocks. At the end of a turn a block is reported into its
 * chunk only when it differs from its fingerprint (it was edited here) AND the chunk is
 * unchanged since (nobody else touched it). Otherwise it is a conflict: the chunk wins,
 * the edited version is set aside in BRAIN/_conflicts/, and the injected state says so.
 *
 * The split is textual: comments and layout are kept. A column-0 comment banner just above
 * a top-level key belongs to that key's block. Nothing is written when the edit or the
 * result is not valid YAML, or when two keys differ only by case (Windows would make them
 * the same file): the problem is reported, to fix by hand.
 */
import fs from 'fs';
import os from 'os';
import path from 'path';
import crypto from 'crypto';
import { fileURLToPath } from 'url';
import yaml from 'js-yaml';
import { writeAtomic, writeIfChanged, withLock } from './lib/atomic.mjs';
import { ROOT_DIR, BRAIN_PATH, samePath, hostName } from './lib/brain.mjs';

export const BRAIN_DIR = path.join(ROOT_DIR, 'BRAIN');
export const CONFLICT_DIR = path.join(BRAIN_DIR, '_conflicts');
// Problems of the last pass, read by relay-state (the hook runs this without visible output).
export const ALERTS_PATH = path.join(os.tmpdir(),
  `relay-brain-alerts-${crypto.createHash('sha1').update(ROOT_DIR.toLowerCase()).digest('hex').slice(0, 12)}.json`);

const MAP_SPLIT = new Set(['machines', 'projects']);   // one second-level key = one chunk
const LIST_SPLIT = new Set(['cross_project_log']);      // one list item = one chunk
// Generation order: the order of relay-brain.template.yml, then any other key alphabetically.
const SECTION_ORDER = ['machines', 'agents', 'conventions', 'projects', 'cross_project_log'];
const TAIL_ID = 'eof.yml';                              // trailing comments of the file
export const MARK_LINE =/^# relay-brain generated: (.*)$/m;
const RESERVED = /^(con|prn|aux|nul|com\d|lpt\d)$/i;
const sha8 = (s) => crypto.createHash('sha1').update(s).digest('hex').slice(0, 8);
const lf = (s) => s.replace(/\r\n/g, '\n');
const readOr = (p, d = null) => { try { return fs.readFileSync(p, 'utf8'); } catch { return d; } };

/** Strips the fingerprint line: { body, marks: Map(id → sha8) | null }. */
export function parseMark(text) {
  text = lf(text);
  const m = text.match(MARK_LINE);
  if (!m) return { body: text, marks: null };
  const marks = new Map(m[1].trim().split(/\s+/).map((x) => x.split('=')).filter((p) => p.length === 2));
  // The line and the blank line before it; anything after it (text added below) stays.
  const body = text.slice(0, m.index).replace(/\n$/, '') + text.slice(m.index + m[0].length + 1);
  return { body, marks };
}

/** Puts back the fingerprint line of `raw` under a new body (used by log-rotate --brain). */
export function withMarkOf(raw, body) {
  const m = lf(raw).match(MARK_LINE);
  return m ? `${body}\n${m[0]}\n` : body;
}

/**
 * Splits the brain text into blocks { id, text }. Concatenating the texts in file order gives
 * back the text unchanged. Throws when two ids differ only by case.
 */
export function splitBrain(text) {
  text = lf(text);
  const lines = text.split('\n');
  if (text.endsWith('\n')) lines.pop();
  const tops = [];
  lines.forEach((l, k) => { const m = l.match(/^([A-Za-z0-9_.-]+):/); if (m) tops.push({ k, key: m[1] }); });
  // A section starts at its key, or higher when column-0 comments and blank lines introduce it.
  tops.forEach((t, n) => {
    const floor = n > 0 ? tops[n - 1].k + 1 : 0;
    let s = t.k;
    while (s > floor && (lines[s - 1].trim() === '' || lines[s - 1].startsWith('#'))) s--;
    // Leading blank lines stay with the previous block; only the banner moves.
    while (s < t.k && lines[s].trim() === '') s++;
    t.start = s;
  });
  const known = (key) => MAP_SPLIT.has(key) || LIST_SPLIT.has(key);
  const first = tops.find((t) => known(t.key));
  const blocks = [];
  const push = (id, from, to) => { if (to > from) blocks.push({ id, text: lines.slice(from, to).join('\n') + '\n' }); };
  const headEnd = first ? first.start : lines.length;
  push('head.yml', 0, headEnd);
  const rest = tops.filter((t) => t.k >= (first ? first.k : lines.length));
  // Column-0 comments after the last section (a commented-out example, a closing note) are their
  // own block, generated last: otherwise they would ride on the last entry of that section and
  // leave with it when the entry is removed or rotated out.
  let fileEnd = lines.length;
  if (rest.length && !tops.some((t) => t.key === TAIL_ID.replace(/\.yml$/, ''))) {
    let s = lines.length;
    const floor = rest[rest.length - 1].k + 1;
    while (s > floor && (lines[s - 1].trim() === '' || lines[s - 1].startsWith('#'))) s--;
    while (s < lines.length && lines[s].trim() === '') s++;
    if (s < lines.length) fileEnd = s;
  }
  rest.forEach((t, n) => {
    const end = n + 1 < rest.length ? rest[n + 1].start : fileEnd;
    if (!known(t.key)) { push(`${t.key}.yml`, t.start, end); return; }
    // List items: the indent of the first item, so a nested list is never taken for an item.
    let re = /^ {2}([A-Za-z0-9_.-]+):/;
    if (LIST_SPLIT.has(t.key)) {
      let indent = null;
      for (let k = t.k + 1; k < end && indent === null; k++) indent = lines[k].match(/^( *)- /)?.[1] ?? null;
      re = new RegExp(`^${indent ?? '  '}- `);
    }
    const kids = [];
    for (let k = t.k + 1; k < end; k++) if (re.test(lines[k])) kids.push(k);
    push(`${t.key}/_.yml`, t.start, kids.length ? kids[0] : end);
    kids.forEach((k, j) => {
      const to = j + 1 < kids.length ? kids[j + 1] : end;
      let name;
      if (MAP_SPLIT.has(t.key)) name = lines[k].match(re)[1];
      else {
        const chunk = lines.slice(k, to).join('\n');
        name = `${chunk.match(/date:\s*["']?(\d{4}-\d{2}-\d{2})/)?.[1] ?? '0000-00-00'}-${sha8(chunk)}`;
      }
      push(`${t.key}/${name}.yml`, k, to);
    });
  });
  push(TAIL_ID, fileEnd, lines.length);
  const seen = new Map();
  for (const b of blocks) {
    const low = b.id.toLowerCase();
    if (seen.has(low)) throw new Error(`keys "${seen.get(low)}" and "${b.id}" are identical ignoring case (same file on Windows) — rename one`);
    if (RESERVED.test(path.basename(b.id, '.yml'))) throw new Error(`key "${b.id}": name reserved by Windows — rename it`);
    seen.set(low, b.id);
  }
  return blocks;
}

const rank = (id) => {
  const top = id.split('/')[0].replace(/\.yml$/, '');
  if (id === 'head.yml') return '0';
  if (id === TAIL_ID) return '3';
  const k = SECTION_ORDER.indexOf(top);
  return k >= 0 ? `1${k}` : `2${top}`;
};
/** Generation order: head, known sections, other sections; inside a section `_` then the keys. */
const order = (a, b) => rank(a.id).localeCompare(rank(b.id))
  || (a.id.endsWith('/_.yml') ? -1 : b.id.endsWith('/_.yml') ? 1 : a.id.localeCompare(b.id));

/** Chunks of BRAIN/ ({ id, text }) and ignored files (sync-conflict copies, unexpected names). */
export function readFragments() {
  const frags = [], ignored = [];
  const ok = (f) => /^[A-Za-z0-9_.-]+\.yml$/.test(f) && !/sync-conflict|\.tmp\.|\.old\./i.test(f);
  let entries = [];
  try { entries = fs.readdirSync(BRAIN_DIR, { withFileTypes: true }); } catch { return { frags, ignored }; }
  for (const e of entries) {
    if (e.name.startsWith('_')) continue; // _conflicts/
    if (e.isFile()) {
      if (ok(e.name)) frags.push({ id: e.name, text: lf(fs.readFileSync(path.join(BRAIN_DIR, e.name), 'utf8')) });
      else if (!/\.\d+\.tmp$/.test(e.name)) ignored.push(e.name);
      continue;
    }
    if (!e.isDirectory()) continue;
    for (const f of fs.readdirSync(path.join(BRAIN_DIR, e.name))) {
      if (ok(f)) frags.push({ id: `${e.name}/${f}`, text: lf(fs.readFileSync(path.join(BRAIN_DIR, e.name, f), 'utf8')) });
      else if (!/\.\d+\.tmp$/.test(f)) ignored.push(`${e.name}/${f}`); // temp files of a write in progress
    }
  }
  return { frags: frags.sort(order), ignored };
}

export function generate(frags) {
  const sorted = [...frags].sort(order).map((f) => ({ ...f, text: f.text.endsWith('\n') ? f.text : f.text + '\n' }));
  const body = sorted.map((f) => f.text).join('');
  return `${body}\n# relay-brain generated: ${sorted.map((f) => `${f.id}=${sha8(f.text)}`).join(' ')}\n`;
}

/** A usable brain has both keys, even empty (the template ships `projects:` with no entry). */
export function validBrain(text) {
  const b = yaml.load(text);
  if (!b || typeof b !== 'object' || !('machines' in b) || !('projects' in b)) throw new Error('machines or projects missing');
  return b;
}
const conflictName = (id) => `${new Intl.DateTimeFormat('en-CA').format(new Date())}-${hostName()}-${id.replace(/\//g, '__')}`;

/**
 * Compares the edited file with the fingerprints it carries and with the current chunks.
 * Returns { write: [{id,text}], remove: [id], conflicts: [{id, text, why}] } — writes nothing.
 */
export function plan(body, marks, frags) {
  const cur = new Map(frags.map((f) => [f.id, f.text]));
  const edited = splitBrain(body);
  const write = [], remove = [], conflicts = [];
  for (const b of edited) {
    const mark = marks?.get(b.id);
    if (mark === sha8(b.text)) continue;                          // block as generated
    const f = cur.get(b.id);
    if (f === b.text) continue;                                    // already identical to its chunk
    if (f === undefined && mark === undefined) { write.push(b); continue; }             // new block
    if (f !== undefined && mark !== undefined && sha8(f) === mark) { write.push(b); continue; } // edited, chunk intact
    conflicts.push({ ...b, why: f === undefined ? 'removed elsewhere, edited here' : mark === undefined ? 'added here and elsewhere' : 'edited here and changed elsewhere' });
  }
  if (marks) {
    const present = new Set(edited.map((b) => b.id));
    for (const [id, mark] of marks) {
      if (present.has(id) || !cur.has(id)) continue;
      if (sha8(cur.get(id)) === mark) remove.push(id);            // removed here, intact elsewhere
      else conflicts.push({ id, text: cur.get(id), why: 'removed here but changed elsewhere: chunk kept' });
    }
  }
  return { write, remove, conflicts };
}

function saveAlerts(list) {
  try { fs.writeFileSync(ALERTS_PATH, JSON.stringify({ at: new Date().toISOString(), alerts: list }), 'utf8'); } catch {}
}

/**
 * Reports the edits of relay-brain.yml into BRAIN/, then regenerates it.
 * Returns { status: 'off'|'ok'|'error'|'retry'|'skipped', wrote, removed, conflicts, written, message? }.
 */
export function syncBrain({ dryRun = false } = {}) {
  // Opt-in: no BRAIN/ directory, nothing to do and nothing touched (not even the alerts file).
  if (!fs.existsSync(BRAIN_DIR)) return { status: 'off', wrote: [], removed: [], conflicts: [], written: false };
  return withLock(BRAIN_PATH, () => {
    const res = { status: 'ok', wrote: [], removed: [], conflicts: [], written: false };
    const alerts = [];
    const finish = (extra = {}) => { Object.assign(res, extra); if (!dryRun) saveAlerts(alerts); return res; };
    const { frags, ignored } = readFragments();
    if (ignored.length) alerts.push(`BRAIN/: ignored file(s) ${ignored.join(', ')} — sync-conflict copy or unexpected name, to sort out`);
    if (!frags.length) return finish({ status: 'skipped', message: 'BRAIN/ is empty — run --migrate' });

    const raw = readOr(BRAIN_PATH);
    let next = frags;
    if (raw !== null) {
      const { body, marks } = parseMark(raw);
      try {
        validBrain(body); // an edit that breaks the YAML is neither reported nor overwritten
        const p = plan(body, marks, frags);
        const map = new Map(frags.map((f) => [f.id, f]));
        for (const w of p.write) map.set(w.id, { id: w.id, text: w.text });
        for (const id of p.remove) map.delete(id);
        next = [...map.values()];
        res.wrote = p.write.map((w) => w.id); res.removed = p.remove; res.conflicts = p.conflicts.map((c) => `${c.id} (${c.why})`);
        if (!dryRun) {
          for (const c of p.conflicts) {
            fs.mkdirSync(CONFLICT_DIR, { recursive: true });
            writeAtomic(path.join(CONFLICT_DIR, conflictName(c.id)), c.text);
          }
        }
      } catch (e) {
        alerts.push(`relay-brain.yml: ${e.message.split('\n')[0]} — nothing reported or regenerated, fix it`);
        return finish({ status: 'error', message: e.message.split('\n')[0] });
      }
    }

    // Everything is built in memory and checked before the first write.
    const out = generate(next);
    try { validBrain(parseMark(out).body); splitBrain(parseMark(out).body); } catch (e) {
      alerts.push(`regenerated brain invalid, nothing written: ${e.message.split('\n')[0]}`);
      return finish({ status: 'error', message: e.message.split('\n')[0] });
    }
    if (dryRun) return finish();

    // An edit that arrived during this pass would be overwritten: give up, the next pass takes it.
    if (readOr(BRAIN_PATH) !== raw) return finish({ status: 'retry', message: 'relay-brain.yml changed during the pass' });
    for (const id of res.wrote) {
      const f = next.find((x) => x.id === id);
      fs.mkdirSync(path.dirname(path.join(BRAIN_DIR, id)), { recursive: true });
      writeAtomic(path.join(BRAIN_DIR, id), f.text.endsWith('\n') ? f.text : f.text + '\n');
    }
    for (const id of res.removed) fs.rmSync(path.join(BRAIN_DIR, id), { force: true });
    res.written = writeIfChanged(BRAIN_PATH, out, { ignore: null });
    return finish();
  }, { timeoutMs: 30_000 });
}

/** Number of versions set aside in BRAIN/_conflicts/ (0 when the directory is absent). */
export function conflictCount() {
  try { return fs.readdirSync(CONFLICT_DIR).filter((f) => !f.startsWith('.')).length; } catch { return 0; }
}

/** Migration (or re-split): BRAIN/ is rebuilt from the current relay-brain.yml. */
export function migrate({ replace = false } = {}) {
  const { frags } = readFragments();
  if (frags.length && !replace) throw new Error('BRAIN/ already holds chunks — migration already done (--replace to rebuild)');
  const raw = readOr(BRAIN_PATH);
  if (raw === null) throw new Error('relay-brain.yml not found');
  const { body } = parseMark(raw);
  const before = validBrain(body);
  const blocks = splitBrain(body);
  if (blocks.map((b) => b.text).join('') !== body) throw new Error('split not reversible — nothing written');
  // In-memory check: the regenerated brain has the same content (key and entry order aside).
  const after = validBrain(parseMark(generate(blocks)).body);
  const norm = (v) => (Array.isArray(v) ? v.map(norm).map((x) => JSON.stringify(x)).sort()
    : v && typeof v === 'object' && !(v instanceof Date) ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, norm(v[k])])) : v);
  if (JSON.stringify(norm(before)) !== JSON.stringify(norm(after))) throw new Error('the regenerated brain differs — nothing written');
  if (replace) for (const f of frags) fs.rmSync(path.join(BRAIN_DIR, f.id), { force: true });
  fs.mkdirSync(BRAIN_DIR, { recursive: true });
  for (const b of blocks) { fs.mkdirSync(path.dirname(path.join(BRAIN_DIR, b.id)), { recursive: true }); writeAtomic(path.join(BRAIN_DIR, b.id), b.text); }
  for (const d of fs.readdirSync(BRAIN_DIR, { withFileTypes: true })) {
    if (d.isDirectory() && !d.name.startsWith('_') && !fs.readdirSync(path.join(BRAIN_DIR, d.name)).length) fs.rmdirSync(path.join(BRAIN_DIR, d.name));
  }
  // The current file has no fingerprints yet: regenerate it right away.
  writeAtomic(BRAIN_PATH, generate(readFragments().frags), (t) => validBrain(parseMark(t).body));
  return blocks.length;
}

// --- CLI ---
if (process.argv[1] && samePath(fileURLToPath(import.meta.url), process.argv[1])) {
  const args = process.argv.slice(2);
  try {
    if (args.includes('--migrate')) console.log(`✓ ${migrate({ replace: args.includes('--replace') })} chunk(s) → BRAIN/`);
    const r = syncBrain({ dryRun: args.includes('--dry-run') });
    if (r.status === 'off') {
      console.log('= BRAIN/ absent — split brain is off (opt in with --migrate)');
    } else {
      for (const id of r.wrote) console.log(`↺ BRAIN/${id}`);
      for (const id of r.removed) console.log(`− BRAIN/${id}`);
      for (const c of r.conflicts) console.log(`! conflict: ${c} → BRAIN/_conflicts/`);
      if (r.status !== 'ok') { console.error(`✗ ${r.message}`); process.exit(1); }
      console.log(`${r.written ? '✓' : '='} relay-brain.yml${r.written ? ' regenerated' : ' (unchanged)'}`);
    }
  } catch (e) {
    console.error(`✗ ${e.message}`);
    process.exit(1);
  }
}
