#!/usr/bin/env node
// token-scan: tokens actually spent, read from the Claude Code transcripts.
//
//   node TOOLS/token-scan.mjs [--today | --since YYYY-MM-DD | --days N] [--project <name>] [--session <id>]
//                             [--by project|session|model|day] [--sort reread] [--json]
//     reread/call = cache read per API call (⚠ beyond 150 k: every call re-reads that whole context);
//       --sort reread puts the heaviest sessions first
//     weighted = relative cost in input-token equivalents (input 1, output 5, cache read 0.1,
//       cache write 5 min 1.25, cache write 1 h 2)
//     breaks = calls where input + cache write > 100 k (cache rebuilt: /clear, compaction, cache expired)
//   node TOOLS/token-scan.mjs --state <project>          → one short line for the injected state (or nothing)
//   node TOOLS/token-scan.mjs --skills [--days N] [--json] → actual usage of skills and rules (retirement pass)
//   node TOOLS/token-scan.mjs --paths                    → paths quoted in AGENTS.md and RULES/ that no longer exist
//
// Transcripts: <config>/projects for $CLAUDE_CONFIG_DIR and ~/.claude, deduplicated by real path.
// Incremental: an index under <tmp>/relay-token-scan/<hash of the root>/index.json (local to the
// machine, rebuilt from scratch if deleted) keeps, per file, its size, mtime and the byte read so far;
// only what was appended since is read. A message is counted once (by message.id); when it shows up
// again (copy in a resumed or forked session), the most complete line wins.
// Project attribution: `.relay-reprise/_sessions.tsv` (session → project, written by the start hook)
// wins; otherwise the message's `cwd` is matched against the projects' `path` in relay-brain.yml.
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { loadBrain, projectPath, canon, ROOT_DIR } from './lib/brain.mjs';

const INDEX_DIR = path.join(os.tmpdir(), 'relay-token-scan',
  createHash('sha1').update(canon(ROOT_DIR).toLowerCase()).digest('hex').slice(0, 12));
const INDEX = path.join(INDEX_DIR, 'index.json');
const VERSION = 1;
const ROOT_NAME = 'relay', OTHER = 'other';

// ---------- Arguments ----------
const argv = process.argv.slice(2);
const opt = (name, def) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : def; };
const flag = (name) => argv.includes(name);

// ---------- Brain projects: canonical paths, longest first ----------
function projectRoots() {
  const roots = [];
  try {
    const brain = loadBrain();
    for (const name of Object.keys(brain.projects ?? {})) {
      let p; try { p = projectPath(name, brain); } catch { continue; }
      if (!p || p.includes('{{')) continue;
      // A project declared on the root itself does not swallow every session run from the root.
      if (canon(p) === canon(ROOT_DIR)) continue;
      roots.push([canon(p), name]);
    }
  } catch { /* unreadable brain: everything falls into the root or "other" */ }
  roots.push([canon(ROOT_DIR), ROOT_NAME]);
  return roots.sort((a, b) => b[0].length - a[0].length);
}
const ROOTS = projectRoots();
const projectOf = (cwd) => {
  if (!cwd) return OTHER;
  const c = canon(cwd);
  for (const [root, name] of ROOTS) if (c === root || c.startsWith(root + path.sep)) return name;
  return OTHER;
};

// Sessions whose project was recorded by the start hook: they win over the cwd (the agent may start
// from the root). Applied at aggregation time, so also to messages already indexed.
const SESSION_PROJECT = (() => {
  const m = new Map();
  try {
    for (const l of fs.readFileSync(path.join(ROOT_DIR, '.relay-reprise', '_sessions.tsv'), 'utf8').split('\n')) {
      const [sid, proj] = l.trim().split('\t');
      if (sid && proj) m.set(sid, proj);
    }
  } catch { /* no session recorded on this machine */ }
  return m;
})();

// ---------- Transcript directories ----------
function transcriptDirs() {
  const seen = new Set(), out = [];
  for (const d of [process.env.CLAUDE_CONFIG_DIR, path.join(os.homedir(), '.claude')].filter(Boolean)) {
    let real; try { real = fs.realpathSync(path.join(d, 'projects')); } catch { continue; }
    if (!seen.has(canon(real))) { seen.add(canon(real)); out.push(real); }
  }
  return out;
}
function* jsonlFiles(dir) {
  let ents; try { ents = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
  for (const e of ents) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) yield* jsonlFiles(p);
    else if (e.name.endsWith('.jsonl')) yield p;
  }
}

// ---------- Index ----------
function loadIndex() {
  try {
    const j = JSON.parse(fs.readFileSync(INDEX, 'utf8'));
    if (j.version === VERSION) return j;
  } catch { /* missing or unreadable: start from scratch */ }
  return { version: VERSION, files: {}, msgs: {}, uses: {} };
}
function saveIndex(idx) {
  fs.mkdirSync(INDEX_DIR, { recursive: true });
  const tmp = `${INDEX}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(idx), 'utf8');
  fs.renameSync(tmp, INDEX);
}

// uses[id] = [day, type, name]. Type "skill": Skill tool, /command, read of SKILLS/<x>/SKILL.md;
// type "rule": RULES/<x>.md read, or .claude/rules/<x>.md injected by Claude Code. Once per line.
const USE_HINT = /"name":"Skill"|SKILL\.md|command-name|rules[\\/]+[\w-]+\.md/;
const RE_SKILL_FILE = /SKILLS[\\/]+([\w-]+)[\\/]+SKILL\.md/g;
const RE_RULE_FILE = /RULES[\\/]+([\w-]+)\.md/g;
const RE_COMMAND = /<command-name>\/([\w:-]+)<\/command-name>/g;
// Claude Code built-in commands: not skills.
const BUILTIN = new Set(['clear', 'exit', 'quit', 'model', 'login', 'logout', 'compact', 'resume', 'config', 'context', 'cost', 'usage', 'help', 'fast', 'advisor', 'status', 'memory', 'permissions', 'hooks', 'mcp', 'agents', 'plugin', 'doctor', 'init', 'rewind', 'export', 'insights', 'effort', 'theme', 'statusline', 'ide', 'add-dir', 'skills', 'artifacts', 'workflows', 'remote-control', 'terminal-setup', 'vim', 'bug', 'feedback', 'release-notes', 'upgrade']);
const RE_RULE_LOADED = /Contents of [^"]*?\.claude[\\/]+rules[\\/]+([\w-]+)\.md/g;
function recordUses(idx, j, line) {
  const day = String(j.timestamp ?? '').slice(0, 10);
  const id = j.uuid ?? j.message?.id;
  if (!day || !id) return;
  const add = (type, name) => { if (name) idx.uses[`${id}|${type}|${name}`] = [day, type, name]; };
  if (j.type === 'assistant') {
    for (const b of Array.isArray(j.message?.content) ? j.message.content : []) {
      if (b?.type !== 'tool_use') continue;
      if (b.name === 'Skill') add('skill', String(b.input?.skill ?? '').split(':').pop());
      const txt = String(b.input?.file_path ?? b.input?.command ?? '');
      for (const m of txt.matchAll(RE_SKILL_FILE)) add('skill', m[1]);
      for (const m of txt.matchAll(RE_RULE_FILE)) add('rule', m[1]);
    }
  } else if (j.type === 'user') {
    for (const m of line.matchAll(RE_COMMAND)) { const n = m[1].split(':').pop(); if (!BUILTIN.has(n)) add('skill', n); }
    for (const m of line.matchAll(RE_RULE_LOADED)) add('rule', m[1]);
  }
}

// msgs[id] = [day, project, session, model, input, output, cache read, cache write 5 min, cache write 1 h]
function record(idx, line) {
  const hint = USE_HINT.test(line);
  if (!hint && !line.includes('"usage"')) return;
  let j; try { j = JSON.parse(line); } catch { return; }
  if (hint) recordUses(idx, j, line);
  const m = j.message, u = m?.usage;
  if (j.type !== 'assistant' || !m?.id || !u) return;
  const cw1h = u.cache_creation?.ephemeral_1h_input_tokens ?? 0;
  const cwTot = u.cache_creation_input_tokens ?? 0;
  const row = [String(j.timestamp ?? '').slice(0, 10), projectOf(j.cwd), j.sessionId ?? '', m.model ?? '',
    u.input_tokens ?? 0, u.output_tokens ?? 0, u.cache_read_input_tokens ?? 0, Math.max(0, cwTot - cw1h), cw1h];
  const prev = idx.msgs[m.id];
  // The most complete line wins (one answer can be written in several blocks).
  if (!prev || row[5] + row[4] >= prev[5] + prev[4]) idx.msgs[m.id] = row;
}

function scan() {
  const t0 = Date.now();
  const idx = loadIndex();
  let readBytes = 0, files = 0;
  for (const dir of transcriptDirs()) {
    for (const f of jsonlFiles(dir)) {
      files++;
      let st; try { st = fs.statSync(f); } catch { continue; }
      const id = canon(f);
      const known = idx.files[id];
      if (known && known.size === st.size && known.mtime === st.mtimeMs) continue;
      // File shortened or rewritten: read it whole again (duplicates are absorbed by message.id).
      const from = known && st.size >= known.offset ? known.offset : 0;
      const fd = fs.openSync(f, 'r');
      try {
        const len = st.size - from;
        const buf = Buffer.alloc(len);
        fs.readSync(fd, buf, 0, len, from);
        const text = buf.toString('utf8');
        const lastNl = text.lastIndexOf('\n');
        const complete = lastNl >= 0 ? text.slice(0, lastNl) : '';
        for (const line of complete.split('\n')) record(idx, line);
        const consumed = lastNl >= 0 ? Buffer.byteLength(text.slice(0, lastNl + 1)) : 0;
        idx.files[id] = { size: st.size, mtime: st.mtimeMs, offset: from + consumed };
        readBytes += len;
      } finally { fs.closeSync(fd); }
    }
  }
  saveIndex(idx);
  return { idx, ms: Date.now() - t0, readBytes, files };
}

// ---------- Aggregation ----------
const COLS = ['input', 'output', 'cache read', 'cache write 5 min', 'cache write 1 h'];
const BREAK = 100e3;
const WEIGHTS = [1, 5, 0.1, 1.25, 2];
const HEAVY = 150e3;
function aggregate(idx, { since, project, session, by }) {
  const groups = new Map();
  for (const r of Object.values(idx.msgs)) {
    const [day, , sess, model] = r;
    const proj = SESSION_PROJECT.get(sess) ?? r[1];
    if (since && day < since) continue;
    if (project && proj !== project) continue;
    if (session && !sess.startsWith(session)) continue;
    const g0 = by === 'session' ? `${proj} ${sess.slice(0, 8)}` : by === 'model' ? model : by === 'day' ? day : proj;
    const g = groups.get(g0) ?? { key: g0, msgs: 0, t: [0, 0, 0, 0, 0], breaks: 0 };
    g.msgs++;
    if (r[4] + r[7] + r[8] > BREAK) g.breaks++;
    for (let i = 0; i < 5; i++) g.t[i] += r[4 + i];
    groups.set(g0, g);
  }
  return [...groups.values()].sort((a, b) => sum(b.t) - sum(a.t));
}
const sum = (t) => t.reduce((a, b) => a + b, 0);
const weighted = (t) => Math.round(t.reduce((a, v, i) => a + v * WEIGHTS[i], 0));
const reread = (g) => (g.msgs ? g.t[2] / g.msgs : 0);
const k = (n) => n >= 1e6 ? `${(n / 1e6).toFixed(1)} M` : n >= 1e3 ? `${Math.round(n / 1e3)} k` : String(n);
// Cache share: reads over everything that goes in (input + reads + writes).
const cacheShare = (t) => { const inAll = t[0] + t[2] + t[3] + t[4]; return inAll ? Math.round((100 * t[2]) / inAll) : 0; };
const daysAgo = (n) => new Date(Date.now() - n * 864e5).toISOString().slice(0, 10);

// ---------- Output ----------
if (flag('--paths')) {
  // Dead paths: every backticked path in AGENTS.md and RULES/*.md that no longer exists under the root.
  // Ignored: templates (<x>, {x}, *, YYYY), URLs, absolute or ~ paths (machine-specific), names without
  // "/" (often a file of a project, not of the root).
  let rules = [];
  try { rules = fs.readdirSync(path.join(ROOT_DIR, 'RULES')).filter((f) => f.endsWith('.md')).map((f) => `RULES/${f}`); } catch { /* no RULES/ */ }
  const dead = [];
  for (const src of ['AGENTS.md', ...rules]) {
    let lines; try { lines = fs.readFileSync(path.join(ROOT_DIR, src), 'utf8').split('\n'); } catch { continue; }
    lines.forEach((l, i) => {
      for (const [, raw] of l.matchAll(/`([^`\s]+)`/g)) {
        const p = raw.replace(/[:,.;)]+$/, '').replace(/:\d+(-\d+)?$/, '');
        if (!p.includes('/') || /[<>{}*]|YYYY|^\w+:\/\/|^[~/]|^[A-Za-z]:|^\.\.?\//.test(p)) continue;
        if (!/\.\w{1,5}$|\/$/.test(p)) continue;
        if (!fs.existsSync(path.join(ROOT_DIR, p))) dead.push(`${src}:${i + 1}\t${p}`);
      }
    });
  }
  console.log(dead.length ? `Paths not found under the root (${dead.length}) — check them, then refine or remove:\n${dead.join('\n')}` : 'No dead path.');
  process.exit(0);
}

const { idx, ms, readBytes, files } = scan();

if (flag('--state')) {
  // One line for the injected state: the project's last 7 days; nothing when no token was spent.
  const proj = opt('--state');
  const [g] = aggregate(idx, { since: daysAgo(7), project: proj, by: 'project' });
  if (g) process.stdout.write(`Tokens 7 d (${proj}): ${k(sum(g.t))}, output ${k(g.t[1])}, cache read ${cacheShare(g.t)} %\n`);
  process.exit(0);
}

if (flag('--skills')) {
  // Actual use per skill and per rule over the period, plus what exists and never served (retirement candidates).
  const days = Number(opt('--days', 60));
  const from = daysAgo(days);
  const use = new Map();
  for (const [day, type, name] of Object.values(idx.uses ?? {})) {
    if (day < from) continue;
    const id = `${type}|${name}`;
    const u = use.get(id) ?? { type, name, n: 0, last: '' };
    u.n++; if (day > u.last) u.last = day;
    use.set(id, u);
  }
  const existing = [];
  try { for (const e of fs.readdirSync(path.join(ROOT_DIR, 'SKILLS'), { withFileTypes: true })) if (e.isDirectory() && !e.name.startsWith('_') && fs.existsSync(path.join(ROOT_DIR, 'SKILLS', e.name, 'SKILL.md'))) existing.push(['skill', e.name]); } catch { /* no SKILLS/ */ }
  try { for (const f of fs.readdirSync(path.join(ROOT_DIR, 'RULES'))) if (f.endsWith('.md') && f !== 'README.md') existing.push(['rule', f.slice(0, -3)]); } catch { /* no RULES/ */ }
  const unused = existing.filter(([t, n]) => !use.has(`${t}|${n}`)).map(([type, name]) => ({ type, name }));
  const used = [...use.values()].sort((a, b) => a.type.localeCompare(b.type) || b.n - a.n);
  if (flag('--json')) {
    console.log(JSON.stringify({ days, since: from, used, unused }, null, 1));
  } else {
    // Transcripts only go back as far as Claude Code keeps them (cleanupPeriodDays, 30 d by default).
    let oldest = '9999';
    for (const r of Object.values(idx.msgs)) if (r[0] && r[0] < oldest) oldest = r[0];
    console.log(`Skill and rule usage since ${from} (${days} d) · transcripts available since ${oldest === '9999' ? '-' : oldest} · scan ${ms} ms`);
    console.log(['type', 'name', 'uses', 'last'].join('\t'));
    for (const u of used) console.log([u.type, u.name, u.n, u.last].join('\t'));
    console.log(`\nNever seen over the period (retirement candidates): ${unused.length ? unused.map((u) => `${u.type} ${u.name}`).join(', ') : 'none'}`);
  }
  process.exit(0);
}

const since = flag('--today') ? new Date().toISOString().slice(0, 10) : opt('--since') ?? (opt('--days') ? daysAgo(Number(opt('--days'))) : undefined);
const rows = aggregate(idx, { since, project: opt('--project'), session: opt('--session'), by: opt('--by', 'project') });
if (flag('--json')) {
  console.log(JSON.stringify({
    since: since ?? null, scan: { ms, readBytes, files },
    rows: rows.map((g) => ({ key: g.key, msgs: g.msgs, input: g.t[0], output: g.t[1], cache_read: g.t[2], cache_write_5m: g.t[3], cache_write_1h: g.t[4], total: sum(g.t), weighted: weighted(g.t), reread_per_call: Math.round(reread(g)), breaks: g.breaks })),
    models: Object.fromEntries(aggregate(idx, { since, by: 'model' }).map((g) => [g.key, g.msgs])),
  }, null, 1));
} else {
  console.log(`Since ${since ?? 'the beginning'} · scan ${ms} ms, ${k(readBytes)}B read, ${files} files`);
  const shown = opt('--sort') === 'reread' ? [...rows].sort((a, b) => reread(b) - reread(a)) : rows;
  console.log(['', 'messages', ...COLS, 'total', 'weighted', 'cache read %', 'reread/call', 'breaks'].join('\t'));
  for (const g of shown.slice(0, 30)) {
    console.log([g.key, g.msgs, ...g.t.map(k), k(sum(g.t)), k(weighted(g.t)), cacheShare(g.t), k(Math.round(reread(g))) + (reread(g) >= HEAVY ? ' ⚠' : ''), g.breaks].join('\t'));
  }
}
