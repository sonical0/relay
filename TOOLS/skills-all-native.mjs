#!/usr/bin/env node
/**
 * skills-all-native.mjs — rollback: puts every skill back to `load: native`.
 *
 * Usage:
 *   node TOOLS/skills-all-native.mjs && node TOOLS/skills-link.mjs
 *
 * `rag` mode is an experiment, not a settled state. If it gets in the way — a skill
 * you can no longer reach, friction on load, a trigger that never fires — this
 * brings back the state where every skill is available to the agent by itself. The
 * cost of coming back is the total size of the descriptions, reloaded into the
 * system prompt on every session; the script prints it.
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { writeAtomic } from './lib/atomic.mjs';

const rootDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const manifestPath = path.join(rootDir, 'SKILLS', 'skills.manifest.json');

let raw;
try { raw = fs.readFileSync(manifestPath, 'utf8'); } catch (e) {
  console.error(`Manifest unreadable: ${e.message}`);
  process.exit(1);
}

const manifest = JSON.parse(raw);
const changed = (manifest.skills ?? []).filter((s) => s.load === 'rag');
for (const s of manifest.skills ?? []) s.load = 'native';

if (!changed.length) {
  console.log('Every skill is already native — nothing to do.');
  process.exit(0);
}

// Rough estimate of what goes back into the system prompt: the description of each
// skill that was on demand until now.
let bytes = 0;
for (const s of changed) {
  const f = path.join(rootDir, 'SKILLS', s.src, 'SKILL.md');
  try {
    const fm = fs.readFileSync(f, 'utf8').match(/^---\n([\s\S]*?)\n---/);
    const desc = fm?.[1].match(/^description: *(.*)$/m)?.[1] ?? '';
    bytes += desc.length + s.name.length;
  } catch { /* source missing: skills-link will report it */ }
}

writeAtomic(manifestPath, JSON.stringify(manifest, null, 2) + '\n', (t) => JSON.parse(t));

console.log(`${changed.length} skill(s) back to native: ${changed.map((s) => s.name).join(', ')}`);
console.log(`Cost: ~${bytes} B of descriptions (~${Math.round(bytes * 0.37)} tokens) on every session.`);
console.log('Now run: node TOOLS/skills-link.mjs');
