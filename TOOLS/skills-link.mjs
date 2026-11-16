#!/usr/bin/env node
/**
 * skills-link.mjs — exposes the skills in .claude/skills/ and the rules in .claude/rules/.
 *
 * Claude Code only discovers skills in `.claude/skills/` and area-scoped rules in
 * `.claude/rules/`. The sources live in `SKILLS/` and `RULES/` (synced between machines);
 * the `.claude/` copies are recreated locally because neither NTFS junctions nor symlinks
 * survive file sync. Re-run on every machine. Idempotent: writes nothing when everything
 * is in place.
 *
 * Every skill of the manifest is linked. Its LOADING MODE lives in its own frontmatter,
 * not in the manifest:
 *   disable-model-invocation: true   on demand — out of the system prompt, invoked as /name
 *   paths: [...]                     native, loaded when matching files are touched
 *   (nothing)                        native — its description is paid on every session
 *
 * RULES/*.md are COPIED (a file, not a directory, and a copy survives anything). Only the
 * copies this script wrote (listed in `.claude/rules/.rules-copied-from`) are ever removed,
 * so a rule written by hand on one machine survives.
 *
 * Per-project scope (links under `PROJECTS/<x>/.claude/skills/`) was tried and abandoned:
 * `git status -uall` follows junctions, so a `git add .` would have committed a copy of the
 * skill into the project repo. This script cleans up links left by that attempt.
 *
 * Usage:
 *   node TOOLS/skills-link.mjs [--dry-run] [--prune] [--force] [--user]
 *
 * --user  links into the user's config dir ($CLAUDE_CONFIG_DIR, else ~/.claude) instead of
 *         <root>/.claude, so sessions started outside the Relay root see the skills and the
 *         rules. With --prune, only removes there what this script put there.
 */

import fs from 'fs';
import os from 'os';
import path from 'path';
import { ROOT_DIR as rootDir, samePath, canon } from './lib/brain.mjs';
import { writeAtomic } from './lib/atomic.mjs';

const manifestPath = path.join(rootDir, 'SKILLS', 'skills.manifest.json');

const args = process.argv.slice(2);
const dryRun = args.includes('--dry-run');
const prune = args.includes('--prune');
const force = args.includes('--force');
const user = args.includes('--user');
const userDir = process.env.CLAUDE_CONFIG_DIR || path.join(os.homedir(), '.claude');

let manifest = { skills: [] };
if (fs.existsSync(manifestPath)) {
  try {
    manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  } catch (e) {
    console.error(`Manifest unreadable: ${e.message}`);
    process.exit(1);
  }
} else {
  console.log(`No manifest (${path.relative(rootDir, manifestPath)}) — no skill to link.`);
}

const rootTarget = user ? path.join(userDir, 'skills') : path.join(rootDir, manifest.target || '.claude/skills');
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

/** Loading mode read from the frontmatter, for the summary line. */
function loadMode(src) {
  try {
    const fm = fs.readFileSync(path.join(src, 'SKILL.md'), 'utf8').match(/^---\r?\n([\s\S]*?)\r?\n---/);
    const head = fm ? fm[1] : '';
    if (/^disable-model-invocation:\s*true/m.test(head)) return 'onDemand';
    if (/^paths:/m.test(head)) return 'paths';
  } catch {}
  return 'auto';
}

const stats = { created: 0, kept: 0, repaired: 0, failed: 0, copied: 0, removed: 0, rules: 0 };
const modes = { auto: 0, paths: 0, onDemand: 0 };
const expectedAtRoot = new Set();
const skillNames = new Set(skills.map((s) => s.name));
const skillsRoot = path.join(rootDir, 'SKILLS');

for (const skill of skills) {
  const src = path.join(skillsRoot, skill.src);
  if (!fs.existsSync(src)) {
    console.error(`x ${skill.name} — source missing: SKILLS/${skill.src}`);
    stats.failed++;
    continue;
  }
  modes[loadMode(src)]++;

  const dest = path.join(rootTarget, skill.name);
  expectedAtRoot.add(skill.name);

  const exists = entryExists(dest);
  if (exists && !force) {
    const current = linkTarget(dest);
    // Case-insensitive on Windows: started from …/relay instead of …/Relay, a strict test
    // declared the links "pointing elsewhere" and recreated them on every pass.
    if (current && samePath(current, src)) { stats.kept++; continue; }
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

// ── Area rules: RULES/*.md → .claude/rules/ ──────────────────────────────────
const rulesSrc = path.join(rootDir, 'RULES');
const rulesDst = user ? path.join(userDir, 'rules') : path.join(rootDir, '.claude', 'rules');
if (fs.existsSync(rulesSrc)) {
  const marker = path.join(rulesDst, '.rules-copied-from');
  let previous = [];
  try { previous = fs.readFileSync(marker, 'utf8').split(/\r?\n/).filter(Boolean); } catch {}
  const wanted = fs.readdirSync(rulesSrc).filter((f) => f.endsWith('.md')).sort();
  for (const f of wanted) {
    const s = path.join(rulesSrc, f), d = path.join(rulesDst, f);
    const content = fs.readFileSync(s, 'utf8');
    let current = null;
    try { current = fs.readFileSync(d, 'utf8'); } catch {}
    if (current === content) continue;
    console.log(`${current === null ? '+' : '~'} rules/${f}`);
    stats.rules++;
    if (dryRun) continue;
    fs.mkdirSync(rulesDst, { recursive: true });
    writeAtomic(d, content);
  }
  for (const f of previous) {
    if (wanted.includes(f) || !entryExists(path.join(rulesDst, f))) continue;
    console.log(`- rules/${f} — no longer in RULES/, removed`);
    stats.rules++;
    if (!dryRun) fs.unlinkSync(path.join(rulesDst, f));
  }
  if (!dryRun && (stats.rules || previous.join('\n') !== wanted.join('\n'))) {
    fs.mkdirSync(rulesDst, { recursive: true });
    writeAtomic(marker, wanted.join('\n') + '\n');
  }
}

// Clean up links left in projects by the per-project scope attempt. Only entries that
// carry a manifest name AND point into SKILLS/ are removed: a skill written by hand for
// one project must survive.
const projectsDir = path.join(rootDir, 'PROJECTS');
if (fs.existsSync(projectsDir)) {
  for (const proj of fs.readdirSync(projectsDir)) {
    const dir = path.join(projectsDir, proj, '.claude', 'skills');
    if (!fs.existsSync(dir)) continue;
    for (const entry of fs.readdirSync(dir)) {
      const p = path.join(dir, entry);
      const target = linkTarget(p);
      const fromSkills = target && canon(target).startsWith(canon(skillsRoot));
      if (!skillNames.has(entry) || !fromSkills) continue;
      console.log(`- PROJECTS/${proj}/.claude/skills/${entry} — scope link removed`);
      if (!dryRun) removeEntry(p);
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
    // At user level other skills may live there: only remove ours.
    if (user) {
      const t = linkTarget(path.join(rootTarget, entry));
      const ours = (t && canon(t).startsWith(canon(skillsRoot)))
        || fs.existsSync(path.join(rootTarget, entry, '.skill-copied-from'));
      if (!ours) continue;
    }
    console.log(`- ${entry} — not in the manifest, removed`);
    if (!dryRun) removeEntry(path.join(rootTarget, entry));
    stats.removed++;
  }
}

const parts = [`skills ${expectedAtRoot.size} (auto ${modes.auto}, paths ${modes.paths}, on demand ${modes.onDemand})`,
  `created ${stats.created}`, `unchanged ${stats.kept}`];
if (stats.repaired) parts.push(`recreated ${stats.repaired}`);
if (stats.removed) parts.push(`removed ${stats.removed}`);
if (stats.copied) parts.push(`copied ${stats.copied}`);
if (stats.rules) parts.push(`rules written ${stats.rules}`);
if (stats.failed) parts.push(`failed ${stats.failed}`);
console.log(`\n${dryRun ? '[dry-run] ' : ''}${parts.join(' · ')}`);
process.exit(stats.failed ? 1 : 0);
