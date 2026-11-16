// Shared helpers for the TOOLS tests. Every test works in a fresh temp root (RELAY_ROOT) with
// a temp home and a temp Claude config dir: the clone's own files are never read or written,
// except the tool scripts themselves and relay-brain.template.yml (read only).
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

export const TOOLS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const CLONE = path.resolve(TOOLS, '..');

export const day = (offsetDays = 0) => new Intl.DateTimeFormat('en-CA').format(new Date(Date.now() + offsetDays * 86400000));
export const monthsBack = (n) => {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
};

/** A temp sandbox: { root, home, config, write(rel, text), read(rel), exists(rel), run(script, args, env), cleanup() }. */
export function sandbox(files = {}) {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'relay-test-'));
  const root = path.join(base, 'root');
  const home = path.join(base, 'home');
  const config = path.join(base, 'claude-config');
  for (const d of [root, home, config]) fs.mkdirSync(d, { recursive: true });
  const abs = (rel) => path.join(root, rel);
  const sb = {
    base, root, home, config,
    write(rel, text) { fs.mkdirSync(path.dirname(abs(rel)), { recursive: true }); fs.writeFileSync(abs(rel), text, 'utf8'); },
    read(rel) { return fs.readFileSync(abs(rel), 'utf8'); },
    exists(rel) { return fs.existsSync(abs(rel)); },
    mtime(rel) { return fs.statSync(abs(rel)).mtimeMs; },
    env(extra = {}) {
      const env = { ...process.env };
      for (const k of Object.keys(env)) if (/^RELAY_|^CLAUDE_CONFIG_DIR$/i.test(k)) delete env[k];
      return {
        ...env, RELAY_ROOT: root, RELAY_HOST: 'HOST-A', RELAY_CLONE_DIRS: '',
        CLAUDE_CONFIG_DIR: config, HOME: home, USERPROFILE: home, ...extra,
      };
    },
    run(script, args = [], extra = {}) {
      const r = spawnSync(process.execPath, [path.join(TOOLS, script), ...args], {
        cwd: root, encoding: 'utf8', env: sb.env(extra), input: '', timeout: 120_000,
      });
      return { status: r.status, stdout: r.stdout ?? '', stderr: r.stderr ?? '', out: `${r.stdout}\n${r.stderr}` };
    },
    cleanup() {
      // Per-root files the tools keep in the temp dir (keyed by a hash of the root).
      const key = crypto.createHash('sha1').update(path.resolve(root).toLowerCase()).digest('hex').slice(0, 12);
      for (const f of fs.readdirSync(os.tmpdir())) {
        if (f.includes(key) && /^relay-(after-turn|brain-alerts)-/.test(f)) fs.rmSync(path.join(os.tmpdir(), f), { force: true });
      }
      fs.rmSync(base, { recursive: true, force: true });
    },
  };
  for (const [rel, text] of Object.entries(files)) sb.write(rel, text);
  return sb;
}

/** A minimal valid brain declaring the given machines. */
export function brainText(machines = ['HOST-A'], { projects = '', log = 'cross_project_log: []\n' } = {}) {
  return [
    '# Test brain',
    'version: 3',
    '',
    '# ─── Machines ───',
    'machines:',
    ...machines.flatMap((m) => [`  ${m}:`, '    relay_root: "/tmp/relay"', '    os: linux']),
    '',
    '# ─── Projects ───',
    'projects:',
    projects,
    '# ─── Cross-project notes ───',
    log,
  ].join('\n');
}

/** Every file under `dir` → sha1 of its content (relative paths, forward slashes). */
export function snapshot(dir) {
  const out = {};
  const walk = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isSymbolicLink()) { out[path.relative(dir, p).replace(/\\/g, '/')] = `link:${fs.readlinkSync(p)}`; continue; }
      if (e.isDirectory()) { walk(p); continue; }
      out[path.relative(dir, p).replace(/\\/g, '/')] = crypto.createHash('sha1').update(fs.readFileSync(p)).digest('hex');
    }
  };
  walk(dir);
  return out;
}
