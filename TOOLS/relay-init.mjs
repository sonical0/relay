#!/usr/bin/env node
/**
 * relay-init.mjs — one-command machine setup.
 *
 *   node TOOLS/relay-init.mjs [--dry-run] [--yes] [--root <dir>] [--config-dir <dir>]
 *
 * In order, each step skipped when already done (a re-run changes nothing):
 *   1. Node >= 20;
 *   2. relay-brain.yml: copied from relay-brain.template.yml when missing, and this machine
 *      (RELAY_HOST, else the hostname) added under `machines:` with its `relay_root` — written
 *      into BRAIN/machines/<host>.yml instead when the split brain is on;
 *   3. dependencies: skipped when js-yaml is installed, else `npm ci` (fallback `npm install`);
 *   4. Claude Code hooks: `node relay-setup-hook.js` (respects CLAUDE_CONFIG_DIR);
 *   5. skills-link, project-scan, rag-index, relay-state;
 *   6. prints the `permissions.additionalDirectories` snippet for settings.json (never written).
 *
 * --root        data root (default: $RELAY_ROOT, else the directory above TOOLS/)
 * --config-dir  Claude Code config dir (default: $CLAUDE_CONFIG_DIR, else ~/.claude)
 * --yes         do not ask for confirmation (it is only asked in an interactive terminal)
 * --dry-run     write nothing, print what would be done
 *
 * Built-in modules only at load time: it runs before the dependencies exist.
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));

const say = (m = '') => console.log(m);
const ok = (m) => say(`  OK   ${m}`);
const info = (m) => say(`  ..   ${m}`);
const todo = (m) => say(`  >>   ${m}`);
const warn = (m) => say(`  !!   ${m}`);

export const hostName = (env = process.env) => env.RELAY_HOST || os.hostname();

function parseArgs(argv) {
  const o = { flags: new Set(), val: {} };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) continue;
    const k = a.slice(2);
    if (['root', 'config-dir'].includes(k)) o.val[k] = argv[++i];
    else o.flags.add(k);
  }
  return o;
}

const osName = (platform = process.platform) => (platform === 'win32' ? 'windows' : platform === 'darwin' ? 'macos' : 'linux');
const shellName = (platform = process.platform, env = process.env) =>
  (platform === 'win32' ? 'powershell' : path.basename(env.SHELL || 'bash'));

/** `machines:` entry of a machine, at the brain's indentation (two spaces). */
export function machineBlock(host, root, platform = process.platform, env = process.env) {
  return [
    `  ${host}:`,
    `    relay_root: "${root.replace(/\\/g, '/')}"`,
    `    os: ${osName(platform)}`,
    `    shell: ${shellName(platform, env)}`,
    '    role: workstation',
    '',
  ].join('\n');
}

/**
 * Adds a machine block to brain text, line by line so comments survive. Replaces the
 * template's MY-MACHINE placeholder when present. Returns the new text, or null when there
 * is no `machines:` key to insert under.
 */
export function addMachine(text, host, block) {
  const eol = text.includes('\r\n') ? '\r\n' : '\n';
  const lines = text.split(/\r?\n/);
  const blockLines = block.replace(/\n$/, '').split('\n');
  const ph = lines.findIndex((l) => /^ {2}MY-MACHINE:\s*$/.test(l));
  if (ph >= 0) {
    let end = ph + 1;
    while (end < lines.length && (/^ {4,}\S/.test(lines[end]))) end++;
    lines.splice(ph, end - ph, ...blockLines);
    return lines.join(eol);
  }
  const at = lines.findIndex((l) => /^machines:\s*(\{\s*\})?\s*(#.*)?$/.test(l));
  if (at < 0) return null;
  if (/\{\s*\}/.test(lines[at])) lines[at] = 'machines:';
  lines.splice(at + 1, 0, ...blockLines);
  return lines.join(eol);
}

function atomicWrite(file, text) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, text, 'utf8');
  fs.renameSync(tmp, file);
}

function run(cmd, args, opts = {}) {
  return spawnSync(cmd, args, {
    encoding: 'utf8',
    shell: process.platform === 'win32' && /^npm$/.test(cmd),
    ...opts,
  });
}

async function loadYaml() {
  try { return (await import('js-yaml')).default; } catch { return null; }
}

function confirm(question) {
  process.stdout.write(`${question} [Y/n] `);
  const buf = Buffer.alloc(256);
  let n = 0;
  try { n = fs.readSync(0, buf, 0, buf.length, null); } catch { return true; }
  const answer = buf.toString('utf8', 0, n).trim().toLowerCase();
  return answer === '' || answer === 'y' || answer === 'yes';
}

async function brainStep(ctx) {
  const { root, dry, host } = ctx;
  const brainPath = path.join(root, 'relay-brain.yml');
  const templatePath = path.join(root, 'relay-brain.template.yml');
  const brainDir = path.join(root, 'BRAIN');
  const block = machineBlock(host, root);
  const yaml = await loadYaml();
  const check = (text) => {
    if (!yaml) return;
    const b = yaml.load(text);
    if (!b?.machines?.[host]?.relay_root) throw new Error(`machines.${host}.relay_root not found after the edit`);
  };

  // Split brain: this machine's entry is its own chunk; relay-brain.mjs regenerates the file.
  if (fs.existsSync(brainDir)) {
    const piece = path.join(brainDir, 'machines', `${host}.yml`);
    if (fs.existsSync(piece)) return ok(`${host} already in the brain (BRAIN/machines/${host}.yml)`);
    if (dry) return info(`would create BRAIN/machines/${host}.yml`);
    atomicWrite(piece, block);
    const r = run(process.execPath, [path.join(HERE, 'relay-brain.mjs')], { cwd: root, env: ctx.env });
    return r.status === 0 ? ok(`${host} added to the brain (BRAIN/machines/${host}.yml)`)
      : warn(`brain not regenerated: ${(r.stderr || r.stdout || '').trim().slice(-200)}`);
  }

  let text;
  let created = false;
  if (fs.existsSync(brainPath)) {
    text = fs.readFileSync(brainPath, 'utf8');
  } else if (fs.existsSync(templatePath)) {
    text = fs.readFileSync(templatePath, 'utf8');
    created = true;
  } else {
    return warn('relay-brain.yml and relay-brain.template.yml both missing — brain not created');
  }

  let present = false;
  if (yaml) {
    try { present = !!yaml.load(text)?.machines?.[host]; } catch (e) {
      return warn(`relay-brain.yml is not valid YAML (${e.message.split('\n')[0]}) — fix it, then run again`);
    }
  } else {
    present = new RegExp(`^ {2}${host.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}:`, 'm').test(text);
  }
  if (present && !created) return ok(`${host} already in relay-brain.yml`);

  const next = present ? text : addMachine(text, host, block);
  if (next === null) {
    warn('no `machines:` key in relay-brain.yml — add this machine by hand:');
    return info(`\nmachines:\n${block}`);
  }
  if (dry) return info(created ? `would create relay-brain.yml from the template, with ${host}` : `would add ${host} to relay-brain.yml`);
  check(next);
  atomicWrite(brainPath, next);
  ok(created ? `relay-brain.yml created from the template, with ${host}` : `${host} added to relay-brain.yml`);
}

function depsStep(ctx) {
  const { root, dry } = ctx;
  if (fs.existsSync(path.join(root, 'node_modules', 'js-yaml', 'package.json'))) return ok('dependencies already installed');
  if (dry) return info('would run npm ci (fallback npm install)');
  let r = { status: 1 };
  if (fs.existsSync(path.join(root, 'package-lock.json'))) {
    info('npm ci …');
    r = run('npm', ['ci', '--no-audit', '--no-fund'], { cwd: root });
  }
  if (r.status !== 0) {
    info('npm install …');
    r = run('npm', ['install', '--no-audit', '--no-fund'], { cwd: root });
  }
  r.status === 0 ? ok('dependencies installed') : warn(`dependencies not installed: ${(r.stderr || '').slice(-200)}`);
}

function hooksStep(ctx) {
  const { root, dry, configDir } = ctx;
  const setup = path.join(root, 'relay-setup-hook.js');
  if (!fs.existsSync(setup)) return warn(`relay-setup-hook.js missing in ${root} — hooks not installed`);
  if (dry) return info(`would run relay-setup-hook.js (hooks in ${path.join(configDir, 'settings.json')})`);
  // Compared before/after rather than parsed from the hook's output, which is free to change.
  const settings = path.join(configDir, 'settings.json');
  const readOr = () => { try { return fs.readFileSync(settings, 'utf8'); } catch { return null; } };
  const before = readOr();
  const r = run(process.execPath, [setup], { cwd: root, input: '{}', env: ctx.env });
  if (r.status !== 0) return warn(`relay-setup-hook.js failed: ${(r.stderr || r.stdout || '').trim().slice(-200)}`);
  const added = readOr() !== before;
  ok(`Claude Code hooks ${added ? 'installed' : 'already in place'} — ${path.join(configDir, 'settings.json')}`);
}

function toolsStep(ctx) {
  const steps = [
    ['skills-link.mjs', 'skill links and rule copies'],
    ['project-scan.mjs', 'project status'],
    ['rag-index.mjs', 'search index'],
    ['relay-state.mjs', 'state injected at startup'], // after the index: it alerts when the index is missing
  ];
  for (const [f, label] of steps) {
    if (ctx.dry) { info(`would run ${f}`); continue; }
    const r = run(process.execPath, [path.join(HERE, f)], { cwd: ctx.root, env: ctx.env });
    r.status === 0 ? ok(label) : warn(`${label}: ${(r.stderr || r.stdout || '').trim().slice(-200)}`);
  }
}

function permissionsHint(ctx) {
  const r = ctx.root.replace(/\\/g, '/');
  let have = false;
  try {
    const s = JSON.parse(fs.readFileSync(path.join(ctx.configDir, 'settings.json'), 'utf8'));
    have = (s.permissions?.additionalDirectories ?? []).some((d) => String(d).replace(/\\/g, '/').toLowerCase() === r.toLowerCase());
  } catch {}
  if (have) return ok('Relay root already in permissions.additionalDirectories');
  todo(`To work from a project directory with the Relay tools in reach, add to ${path.join(ctx.configDir, 'settings.json')}:`);
  info(`  "permissions": { "additionalDirectories": ["${r}"] }`);
}

export async function main(argv = process.argv.slice(2), env = process.env) {
  const args = parseArgs(argv);
  if (args.flags.has('help') || args.flags.has('h')) {
    say(fs.readFileSync(fileURLToPath(import.meta.url), 'utf8').split('*/')[0].replace(/^[#!/ *]+/gm, ''));
    return 0;
  }
  const major = Number(process.versions.node.split('.')[0]);
  if (major < 20) { warn(`Node ${process.versions.node}: Node 20 or later is required (https://nodejs.org)`); return 1; }

  const root = path.resolve(args.val.root || env.RELAY_ROOT || path.resolve(HERE, '..'));
  const configDir = path.resolve(args.val['config-dir'] || env.CLAUDE_CONFIG_DIR || path.join(os.homedir(), '.claude'));
  const dry = args.flags.has('dry-run');
  const host = hostName(env);
  const ctx = { root, configDir, dry, host, env: { ...env, RELAY_ROOT: root, CLAUDE_CONFIG_DIR: configDir } };

  say(`Relay — setting up ${host} (${process.platform}, Node ${process.versions.node}) · root ${root}${dry ? ' · DRY RUN (nothing written)' : ''}`);
  if (!fs.existsSync(root)) { warn(`${root} does not exist`); return 1; }
  if (!dry && !args.flags.has('yes') && process.stdin.isTTY && !confirm(`Set up Relay in ${root} (hooks in ${configDir})?`)) {
    say('Cancelled.');
    return 0;
  }

  await brainStep(ctx);
  depsStep(ctx);
  hooksStep(ctx);
  toolsStep(ctx);
  permissionsHint(ctx);
  say();
  say(dry ? 'Dry run done.' : 'Done. Running this again is safe: it only redoes what is missing.');
  return 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().then((c) => process.exit(c), (e) => { console.error(`relay-init: ${e.message}`); process.exit(1); });
}
