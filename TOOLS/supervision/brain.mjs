// Reads one project from relay-brain.yml for config.py (Python has no YAML parser in its stdlib).
//   node TOOLS/supervision/brain.mjs <relay_root> <project> [host]
// Prints JSON {path, paths, repo, vault_docs, supervision} or `null` (project absent). Exit 1 if the brain is unreadable.
// `{{relay_root}}` resolves through machines[<host>].relay_root, else <relay_root>; a relative path is relative to it.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import yaml from 'js-yaml';

const [root, name, host = os.hostname()] = process.argv.slice(2);
let brain;
try {
  brain = yaml.load(fs.readFileSync(path.join(root, 'relay-brain.yml'), 'utf8'));
} catch (e) {
  process.stderr.write(`relay-brain.yml unreadable: ${e.message}\n`);
  process.exit(1);
}
const p = brain?.projects?.[name];
if (!p) { process.stdout.write('null'); process.exit(0); }

const base = (brain?.machines?.[host]?.relay_root || root).replace(/\\/g, '/');
const resolve = (s) => {
  if (typeof s !== 'string' || !s) return s ?? null;
  const r = s.replace(/\{\{relay_root\}\}/g, base);
  return path.isAbsolute(r) ? r : path.join(root, r);
};
const paths = p.paths && typeof p.paths === 'object'
  ? Object.fromEntries(Object.entries(p.paths).map(([h, v]) => [h, resolve(v)])) : null;
process.stdout.write(JSON.stringify({
  path: resolve(p.path), paths, repo: p.repo ?? null,
  vault_docs: p.vault_docs ?? null, supervision: p.supervision ?? null,
}));
