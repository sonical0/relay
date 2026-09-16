#!/usr/bin/env node
/**
 * skills-link.mjs — exposes `load: native` skills into .claude/skills/.
 *
 * Claude Code only discovers skills in `.claude/skills/`. The sources live in
 * `SKILLS/` (synced between machines); the links are recreated locally because
 * neither NTFS junctions nor symlinks survive cloud sync.
 *
 * Two modes, via `load` in the manifest:
 *   native — linked at the root. Auto-triggering, but its `description:` enters the
 *            system prompt on EVERY session (~0.37 token/byte measured).
 *   rag    — no link. The skill is found with
 *            `node TOOLS/rag-query.mjs --query "…" --type skill` and read on demand.
 *            Zero cost at rest, no auto-triggering.
 *
 * Per-project scope (links under `PROJECTS/<x>/.claude/skills/`) was tried and
 * abandoned: `git status -uall` follows junctions and saw `SKILL.md`, so a
 * `git add .` would have committed a copy of the skill into the project repo, bound
 * to diverge from `SKILLS/`. This script cleans up links left by that attempt.
 *
 * Usage:
 *   node TOOLS/skills-link.mjs [--dry-run] [--prune] [--force]
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const rootDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const manifestPath = path.join(rootDir, 'SKILLS', 'skills.manifest.json');

const args = process.argv.slice(2);
const dryRun = args.includes('--dry-run');
const prune = args.includes('--prune');
const force = args.includes('--force');

if (!fs.existsSync(manifestPath)) {
  console.error(`Manifest missing: ${manifestPath}`);
  process.exit(1);
}

let manifest;
try {
  manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
} catch (e) {
  console.error(`Manifest unreadable: ${e.message}`);
  process.exit(1);
}

const rootTarget = path.join(rootDir, manifest.target || '.claude/skills');
const skills = manifest.skills || [];
const linkType = process.platform === 'win32' ? 'junction' : 'dir';

function linkTarget(p) {
  try {
    if (!fs.lstatSync(p).isSymbolicLink()) return null;
    return path.resolve(path.dirname(p), fs.readlinkSync(p));
  } catch { return null; }
}

/**
 * Presence of the entry itself, broken link included.
 * `fs.existsSync` follows the link: on a junction whose target is gone it returns
 * false, and the orphan entry then survived `--prune`.
 */
function entryExists(p) {
  try { fs.lstatSync(p); return true; } catch { return false; }
}

/** Removes an entry, including a broken junction that rmSync leaves in place. */
function removeEntry(p) {
  try {
    if (fs.lstatSync(p).isSymbolicLink()) { fs.unlinkSync(p); return; }
  } catch { return; }
  fs.rmSync(p, { recursive: true, force: true });
}

function copyDir(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const s = path.join(src, entry.name);
    const d = path.join(dest, entry.name);
    if (entry.isDirectory()) copyDir(s, d);
    else fs.copyFileSync(s, d);
  }
}

const stats = { created: 0, kept: 0, repaired: 0, failed: 0, copied: 0, removed: 0, rag: 0 };
const expectedAtRoot = new Set();
const skillNames = new Set(skills.map((s) => s.name));

for (const skill of skills) {
  const src = path.join(rootDir, 'SKILLS', skill.src);
  if (!fs.existsSync(src)) {
    console.error(`x ${skill.name} — source missing: SKILLS/${skill.src}`);
    stats.failed++;
    continue;
  }

  const dest = path.join(rootTarget, skill.name);

  if (skill.load === 'rag') {
    stats.rag++;
    if (entryExists(dest)) {
      console.log(`- ${skill.name} — removed from the root (load: rag)`);
      if (!dryRun) removeEntry(dest);
      stats.removed++;
    }
    continue;
  }

  expectedAtRoot.add(skill.name);

  const exists = entryExists(dest);
  if (exists && !force) {
    const current = linkTarget(dest);
    if (current && path.resolve(current) === path.resolve(src)) { stats.kept++; continue; }
    if (!current) {
      console.log(`. ${skill.name} — real directory in place, left alone (--force to replace)`);
      stats.kept++;
      continue;
    }
    console.log(`~ ${skill.name} — pointed elsewhere, recreated`);
    if (!dryRun) removeEntry(dest);
    stats.repaired++;
  } else if (exists && force) {
    if (!dryRun) removeEntry(dest);
    stats.repaired++;
  } else {
    stats.created++;
  }

  if (dryRun) { console.log(`+ ${skill.name}`); continue; }

  try {
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.symlinkSync(src, dest, linkType);
    console.log(`+ ${skill.name}`);
  } catch (e) {
    try {
      copyDir(src, dest);
      fs.writeFileSync(path.join(dest, '.skill-copied-from'), `SKILLS/${skill.src}\n`, 'utf8');
      console.log(`+ ${skill.name} (copy — link refused: ${e.code})`);
      stats.copied++;
    } catch (e2) {
      console.error(`x ${skill.name} — ${e2.message}`);
      stats.failed++;
    }
  }
}

// Clean up links left in projects by the per-project scope attempt. Only entries
// that carry a manifest name AND point into SKILLS/ are removed: a skill written by
// hand for one project must survive.
const projectsDir = path.join(rootDir, 'PROJECTS');
if (fs.existsSync(projectsDir)) {
  for (const proj of fs.readdirSync(projectsDir)) {
    const dir = path.join(projectsDir, proj, '.claude', 'skills');
    if (!fs.existsSync(dir)) continue;
    for (const entry of fs.readdirSync(dir)) {
      const p = path.join(dir, entry);
      const target = linkTarget(p);
      const fromSkills = target && path.resolve(target).startsWith(path.resolve(path.join(rootDir, 'SKILLS')));
      if (!skillNames.has(entry) || !fromSkills) continue;
      console.log(`- PROJECTS/${proj}/.claude/skills/${entry} — scope link removed`);
      if (!dryRun) fs.rmSync(p, { recursive: true, force: true });
      stats.removed++;
    }
    if (!dryRun) {
      try {
        if (fs.readdirSync(dir).length === 0) {
          fs.rmdirSync(dir);
          const parent = path.dirname(dir);
          if (fs.readdirSync(parent).length === 0) fs.rmdirSync(parent);
        }
      } catch { /* not empty or locked: leave it */ }
    }
  }
}

if (prune && fs.existsSync(rootTarget)) {
  for (const entry of fs.readdirSync(rootTarget)) {
    if (expectedAtRoot.has(entry)) continue;
    console.log(`- ${entry} — not in the manifest, removed`);
    if (!dryRun) removeEntry(path.join(rootTarget, entry));
    stats.removed++;
  }
}

const parts = [`native ${expectedAtRoot.size}`, `via RAG ${stats.rag}`,
  `created ${stats.created}`, `unchanged ${stats.kept}`];
if (stats.repaired) parts.push(`recreated ${stats.repaired}`);
if (stats.removed) parts.push(`removed ${stats.removed}`);
if (stats.copied) parts.push(`copied ${stats.copied}`);
if (stats.failed) parts.push(`failed ${stats.failed}`);
console.log(`\n${dryRun ? '[dry-run] ' : ''}${parts.join(' · ')}`);
process.exit(stats.failed ? 1 : 0);
