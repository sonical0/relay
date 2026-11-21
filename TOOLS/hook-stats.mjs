#!/usr/bin/env node
// hook-stats: how long the Claude Code hooks take, read from the transcripts (hook attachments
// carrying durationMs). Limit: only hooks that produce output appear there.
//
//   node TOOLS/hook-stats.mjs [--days N] [--json]     (default: 7 days)
//
// Transcripts: <config>/projects for $CLAUDE_CONFIG_DIR and ~/.claude, deduplicated by real path.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const argv = process.argv.slice(2);
const opt = (name, def) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : def; };
const days = Number(opt('--days', 7));
if (!Number.isFinite(days) || days <= 0) {
  console.error('usage: node TOOLS/hook-stats.mjs [--days N] [--json]');
  process.exit(2);
}
const since = Date.now() - days * 864e5;

function transcriptRoots() {
  const seen = new Set(), out = [];
  for (const d of [process.env.CLAUDE_CONFIG_DIR, path.join(os.homedir(), '.claude')].filter(Boolean)) {
    let real; try { real = fs.realpathSync(path.join(d, 'projects')); } catch { continue; }
    if (!seen.has(real)) { seen.add(real); out.push(real); }
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
// The script name, not its full path (which would carry a home directory).
const short = (c) => c.replace(/"/g, '').trim().split(/\s+/).map((w) => w.replace(/.*[\\/]/, '')).join(' ').slice(0, 80);

const S = {};
for (const root of transcriptRoots()) {
  for (const f of jsonlFiles(root)) {
    try { if (fs.statSync(f).mtimeMs < since) continue; } catch { continue; }
    let text; try { text = fs.readFileSync(f, 'utf8'); } catch { continue; }
    for (const l of text.split('\n')) {
      if (!l.includes('"durationMs"') || !l.includes('hookEvent')) continue;
      try {
        const o = JSON.parse(l);
        const a = o.attachment;
        if (!a?.hookEvent || a.durationMs == null) continue;
        if (o.timestamp && Date.parse(o.timestamp) < since) continue;
        (S[`${a.hookEvent} | ${short(a.command || '')}`] ??= []).push([a.durationMs, a.timedOut ? 1 : 0]);
      } catch { /* partial line */ }
    }
  }
}
const q = (a, p) => a[Math.min(a.length - 1, Math.floor(p * a.length))];
const rows = Object.entries(S).map(([hook, v]) => {
  const d = v.map((x) => x[0]).sort((a, b) => a - b);
  return { hook, n: v.length, median: q(d, 0.5), p95: q(d, 0.95), max: d.at(-1), timeouts: v.reduce((s, x) => s + x[1], 0), total: d.reduce((s, x) => s + x, 0) };
}).sort((a, b) => b.total - a.total);

if (argv.includes('--json')) {
  console.log(JSON.stringify({ days, rows }, null, 1));
} else {
  console.log(`Hook durations over ${days} d (ms) · ${rows.reduce((s, r) => s + r.n, 0)} runs`);
  console.log('n\tmedian\tp95\tmax\ttimeouts\thook');
  for (const r of rows) console.log(`${r.n}\t${r.median}\t${r.p95}\t${r.max}\t${r.timeouts}\t${r.hook}`);
}
