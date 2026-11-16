/**
 * brain.mjs — reads relay-brain.yml and resolves the paths it declares.
 *
 * One owner for this logic. In the internal system this framework comes from, each
 * tool used to carry its own copy, and the copies drifted: one tested `path` without
 * substituting the root placeholder (the test always failed and the tool silently
 * fell back to a default directory), another compared paths case-sensitively, which
 * Windows does not. Every tool now goes through here.
 *
 * Environment:
 *   RELAY_ROOT        data root (default: two directories above this file)
 *   RELAY_HOST        identity of this machine (default: os.hostname())
 *   RELAY_CLONE_DIRS  where to look for a project clone missing at its declared path,
 *                     absolute dirs separated by the platform path delimiter
 *                     (default: ~/Documents/GitHub and ~/Documents/Git)
 */
import fs from 'fs';
import os from 'os';
import path from 'path';
import { fileURLToPath } from 'url';
import yaml from 'js-yaml';

/** Directory holding the tools themselves (never the data root: tests point RELAY_ROOT elsewhere). */
export const TOOLS_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const ROOT_DIR = process.env.RELAY_ROOT
  ? path.resolve(process.env.RELAY_ROOT)
  : path.resolve(TOOLS_DIR, '..');
export const BRAIN_PATH = path.join(ROOT_DIR, 'relay-brain.yml');

/** This machine's identity in the brain and in `LOG/`: RELAY_HOST when set (container, VM whose
 *  hostname does not match its entry), otherwise the hostname. */
export const hostName = () => process.env.RELAY_HOST || os.hostname();

/** Machine roles (`role:` of its entry): `workstation` (default) regenerates its local files and
 *  merges the brain; `satellite` reads everything but only regenerates local files — it never
 *  rewrites shared files such as the brain. */
export const ROLES = ['workstation', 'satellite'];

/** Windows ignores path case, path.resolve() does not: a harness may report `…/documents`
 *  while the disk says `…/Documents`. */
export const canon = (p) => {
  const r = path.resolve(String(p));
  return process.platform === 'win32' ? r.toLowerCase() : r;
};
export const samePath = (a, b) => canon(a) === canon(b);

let cached = null;
/** The brain (parsed YAML), read once per process. Throws when unreadable. */
export function loadBrain() {
  if (!cached) cached = yaml.load(fs.readFileSync(BRAIN_PATH, 'utf8')) ?? {};
  return cached;
}

/** `machines[hostname]` entry of this machine, or undefined. */
export function thisMachine(brain = loadBrain()) {
  return brain?.machines?.[hostName()];
}

/** Role of a machine (this one by default); `workstation` when unset, unknown or unreadable. */
export function machineRole(machine) {
  try {
    const role = (machine === undefined ? thisMachine() : machine)?.role;
    return ROLES.includes(role) ? role : 'workstation';
  } catch {
    return 'workstation';
  }
}

/** Replaces `{{relay_root}}` with the machine's declared root (backslashes normalised), or with
 *  ROOT_DIR when the machine declares none. */
export function resolvePlaceholders(str, machine) {
  if (!str || typeof str !== 'string') return str;
  if (machine === undefined) { try { machine = thisMachine(); } catch { machine = null; } }
  const root = (machine?.relay_root || ROOT_DIR).replace(/\\/g, '/');
  return str.replace(/\{\{relay_root\}\}/g, root);
}

/** Directories searched for a clone missing at its declared path. */
export function cloneDirs() {
  const env = process.env.RELAY_CLONE_DIRS;
  if (env !== undefined) return env.split(path.delimiter).filter((d) => d && path.isAbsolute(d));
  return ['GitHub', 'Git'].map((d) => path.join(os.homedir(), 'Documents', d));
}

/**
 * Declared path of a project for a machine, unresolved: `paths.<host>` when present (a clone in
 * an unusual place), otherwise `path`; null when there is none. The only reader of these two
 * fields. Fallback: an absolute `path` missing on this machine is looked up by its basename in
 * the clone directories (cloneDirs), so a machine with a different home directory needs no
 * `paths:` entry.
 */
export function declaredPath(entry, host = hostName()) {
  const own = entry?.paths?.[host];
  if (own) return own;
  const p = entry?.path ?? null;
  if (!p || p.includes('{{') || !path.isAbsolute(p) || fs.existsSync(p)) return p;
  for (const dir of cloneDirs()) {
    const guess = path.join(dir, path.basename(p)).replace(/\\/g, '/');
    if (fs.existsSync(guess)) return guess;
  }
  return p;
}

/** Resolved path of a project on this machine (relative paths are relative to ROOT_DIR);
 *  null when it is not declared. */
export function projectPath(name, brain = loadBrain()) {
  const declared = declaredPath(brain?.projects?.[name]);
  if (!declared) return null;
  const resolved = resolvePlaceholders(declared, thisMachine(brain) ?? null);
  return path.normalize(path.isAbsolute(resolved) ? resolved : path.join(ROOT_DIR, resolved));
}
