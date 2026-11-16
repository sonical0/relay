/**
 * skills.mjs — reads skill frontmatter.
 *
 * A skill's loading mode lives in its own frontmatter, not in the manifest:
 *   disable-model-invocation: true   on demand — out of the system prompt, invoked as /name
 *   paths: [...]                     native, loaded when matching files are touched
 *   (nothing)                        native, its description is paid for on every session
 */
import fs from 'fs';
import path from 'path';
import yaml from 'js-yaml';

/** Parsed frontmatter of a SKILL.md, `null` when it has none. Tolerates CRLF checkouts. */
export function skillMeta(file) {
  const fm = fs.readFileSync(file, 'utf8').match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (!fm) return null;
  try { return yaml.load(fm[1]) ?? {}; } catch { return null; }
}

/** Every SKILL.md under `dir`, skipping directories whose name starts with `_` or `.`. */
export function skillFiles(dir, depth = 0) {
  const out = [];
  if (depth > 6) return out;
  let entries = [];
  try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return out; }
  for (const e of entries) {
    if (e.name.startsWith('_') || e.name.startsWith('.') || e.name === 'node_modules') continue;
    const p = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...skillFiles(p, depth + 1));
    else if (e.name === 'SKILL.md') out.push(p);
  }
  return out.sort();
}

export const isOnDemand = (meta) => meta?.['disable-model-invocation'] === true;
