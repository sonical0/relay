#!/usr/bin/env node
/**
 * new-project.mjs — creates PROJECTS/<name>/ from the template and registers it.
 *
 * Usage:
 *   node TOOLS/new-project.mjs <name> [--summary "one line"] [--no-register] [--dry-run]
 *
 * Registration inserts a `projects:` entry in relay-brain.yml. The brain is a manual
 * file — it carries human intent — so this is the one exception, and a narrow one:
 * it is invoked explicitly, it writes only `path:` and `summary:`, never `intent:`,
 * and it edits the text line by line so the comments in your brain survive. Anything
 * that would rewrite the file through a YAML dump would strip them.
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import yaml from 'js-yaml';
import { writeAtomic } from './lib/atomic.mjs';

const rootDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const templateDir = path.join(rootDir, 'PROJECTS', '_template');
const brainPath = path.join(rootDir, 'relay-brain.yml');

const args = process.argv.slice(2);
const name = args.find((a) => !a.startsWith('--'));
const dryRun = args.includes('--dry-run');
const register = !args.includes('--no-register');
const summaryIdx = args.indexOf('--summary');
const summary = summaryIdx >= 0 ? args[summaryIdx + 1] : 'One line: what this project is for.';

if (!name) {
  console.error('Usage: node TOOLS/new-project.mjs <name> [--summary "…"] [--no-register] [--dry-run]');
  process.exit(1);
}
if (!/^[a-z0-9][a-z0-9-_]*$/i.test(name)) {
  console.error('Project name: letters, digits, dash and underscore only.');
  process.exit(1);
}

const dest = path.join(rootDir, 'PROJECTS', name);
if (fs.existsSync(dest)) {
  console.error(`PROJECTS/${name}/ already exists.`);
  process.exit(1);
}
if (!fs.existsSync(templateDir)) {
  console.error('PROJECTS/_template/ missing.');
  process.exit(1);
}

function copyTemplate(src, out) {
  fs.mkdirSync(out, { recursive: true });
  for (const e of fs.readdirSync(src, { withFileTypes: true })) {
    const s = path.join(src, e.name);
    const d = path.join(out, e.name);
    if (e.isDirectory()) { copyTemplate(s, d); continue; }
    const text = fs.readFileSync(s, 'utf8').replace(/\{PROJECT_NAME\}/g, name);
    fs.writeFileSync(d, text, 'utf8');
  }
}

if (dryRun) {
  console.log(`[dry-run] would create PROJECTS/${name}/ from _template/`);
} else {
  copyTemplate(templateDir, dest);
  console.log(`✓ PROJECTS/${name}/`);
}

if (register) {
  if (!fs.existsSync(brainPath)) {
    console.error('relay-brain.yml missing — copy relay-brain.template.yml first. Project not registered.');
  } else {
    const raw = fs.readFileSync(brainPath, 'utf8');
    const existing = yaml.load(raw);
    if (existing?.projects?.[name]) {
      console.log(`. ${name} already registered in relay-brain.yml`);
    } else {
      const lines = raw.split('\n');
      const at = lines.findIndex((l) => /^projects: *$/.test(l));
      const block = [
        `  ${name}:`,
        `    path: "{{relay_root}}/PROJECTS/${name}"`,
        `    summary: ${JSON.stringify(summary)}`,
      ];
      if (at < 0) {
        console.error('No `projects:` key found in relay-brain.yml — add this by hand:');
        console.error('\nprojects:\n' + block.join('\n'));
      } else if (dryRun) {
        console.log('[dry-run] would insert into relay-brain.yml:\n' + block.join('\n'));
      } else {
        lines.splice(at + 1, 0, ...block);
        const next = lines.join('\n');
        writeAtomic(brainPath, next, (t) => {
          const parsed = yaml.load(t);
          if (!parsed?.projects?.[name]) throw new Error('entry not found after write');
        });
        console.log(`✓ relay-brain.yml — ${name} registered`);
      }
    }
  }
}

console.log('\nNext:');
console.log(`  1. fill in PROJECTS/${name}/AGENTS.md (role split, structure, local conventions)`);
console.log('  2. npm run state       (refresh derived status and injected state)');
console.log('  3. log the start in RELAY_LOG.md');
