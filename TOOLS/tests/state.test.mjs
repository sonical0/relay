import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { sandbox, brainText, day } from './helpers.mjs';

test('state lists on-demand skills from SKILL.md frontmatter, and the alerts', (t) => {
  const sb = sandbox({
    'relay-brain.yml': brainText(['OTHER-HOST'], { projects: '  demo:\n    path: "PROJECTS/demo"\n' }),
    'SKILLS/skills.manifest.json': JSON.stringify({ skills: [
      { name: 'ondemand', src: 'ondemand' }, { name: 'native', src: 'native' }, { name: 'crlf', src: 'nested/crlf' },
    ] }),
    'SKILLS/ondemand/SKILL.md': '---\nname: ondemand\ndescription: "Invoked by hand."\ndisable-model-invocation: true\n---\n\n# On demand\n',
    'SKILLS/native/SKILL.md': '---\nname: native\ndescription: "Always there."\n---\n\n# Native\n',
    'SKILLS/nested/crlf/SKILL.md': '---\r\nname: crlf\r\ndescription: "CRLF checkout."\r\ndisable-model-invocation: true\r\n---\r\n\r\n# CRLF\r\n',
    'SKILLS/_template/SKILL.md': '---\nname: skill-name\ndisable-model-invocation: true\n---\n',
    'INBOX/HOST-A/2026-01-01-idea.md': '# idea\n',
    'INBOX/HOST-B/2026-01-02-other.md': '# other\n',
    'INBOX/_processed/2025-12/old.md': '# done\n',
    'PROJECTS/demo/AGENTS.md': '# demo\n',
    'LOG/HOST-A.md': `# Relay log\n\n## ${day(0)} 09:00 — Claude (demo: kickoff)\n- [demo] first bullet\n`,
  });
  t.after(sb.cleanup);
  assert.equal(sb.run('relay-log.mjs').status, 0);
  assert.equal(sb.run('project-scan.mjs').status, 0);
  const r = sb.run('relay-state.mjs');
  assert.equal(r.status, 0, r.out);
  const state = sb.read('.relay-state.md');

  const line = state.split('\n').find((l) => l.startsWith('**On-demand skills**'));
  assert.ok(line, state);
  assert.match(line, /ondemand/);
  assert.match(line, /crlf/);
  assert.doesNotMatch(line, /native|skill-name/);

  assert.match(state, /machine HOST-A missing from relay-brain\.yml/);
  assert.match(state, /2 proposal\(s\) pending in INBOX/);
  assert.match(state, /demo: kickoff/);

  // Per-project state and its index keep the format the start hook reads.
  const index = JSON.parse(sb.read('.relay-state/index.json'));
  assert.ok(Array.isArray(index));
  const demo = index.find((e) => e.name === 'demo');
  assert.ok(demo && demo.file === 'demo.md' && path.isAbsolute(demo.dir), JSON.stringify(index));
  assert.match(sb.read('.relay-state/demo.md'), /first bullet/);

  // Unchanged inputs: nothing rewritten.
  const m = fs.statSync(path.join(sb.root, '.relay-state', 'index.json')).mtimeMs;
  const again = sb.run('relay-state.mjs');
  assert.match(again.stdout, /unchanged/);
  assert.equal(fs.statSync(path.join(sb.root, '.relay-state', 'index.json')).mtimeMs, m);
});

test('skill description budget counts only skills in the system prompt', (t) => {
  const long = 'x'.repeat(4200);
  const sb = sandbox({
    'relay-brain.yml': brainText(),
    [`.claude/skills/big/SKILL.md`]: `---\nname: big\ndescription: "${long}"\n---\n`,
    [`.claude/skills/hidden/SKILL.md`]: `---\nname: hidden\ndescription: "${long}"\ndisable-model-invocation: true\n---\n`,
  });
  t.after(sb.cleanup);
  sb.run('relay-state.mjs');
  const state = sb.read('.relay-state.md');
  const n = Number(state.match(/skill descriptions at (\d+) B/)?.[1]);
  assert.ok(n > 4000 && n < 4300, state);
});

test('an unchanged scan still refreshes the date relay-state reads (no stale-status alert)', (t) => {
  const sb = sandbox({ 'relay-brain.yml': brainText() });
  t.after(sb.cleanup);
  assert.equal(sb.run('project-scan.mjs').status, 0);
  // Same content, generated 10 days ago.
  const old = new Date(Date.now() - 10 * 86400000).toISOString();
  sb.write('relay-projects.derived.yml', sb.read('relay-projects.derived.yml').replace(/^generated: .*$/m, `generated: '${old}'`));
  sb.run('relay-state.mjs');
  assert.match(sb.read('.relay-state.md'), /project status is 10 d old/);
  assert.equal(sb.run('project-scan.mjs').status, 0);
  sb.run('relay-state.mjs');
  assert.doesNotMatch(sb.read('.relay-state.md'), /project status is \d+ d old|not computed/);
});

const alertsLine = (sb) => sb.read('.relay-state.md').split('\n').find((l) => l.startsWith('**Alerts**')) ?? '';

test('skill links that differ from the manifest are flagged, silent when they match', (t) => {
  const sb = sandbox({
    'relay-brain.yml': brainText(),
    'SKILLS/skills.manifest.json': JSON.stringify({ skills: [
      { name: 'kept', src: 'kept' }, { name: 'added', src: 'added' }, { name: 'gone', src: 'no-source' },
    ] }),
    'SKILLS/kept/SKILL.md': '---\nname: kept\ndisable-model-invocation: true\n---\n',
    'SKILLS/added/SKILL.md': '---\nname: added\ndisable-model-invocation: true\n---\n',
    '.claude/skills/kept/SKILL.md': '---\nname: kept\ndisable-model-invocation: true\n---\n',
    '.claude/skills/archived/SKILL.md': '---\nname: archived\ndisable-model-invocation: true\n---\n',
  });
  t.after(sb.cleanup);
  sb.run('relay-state.mjs');
  const line = alertsLine(sb);
  assert.match(line, /skill links ≠ manifest \(missing: added; extra: archived\) → node TOOLS\/skills-link\.mjs --prune/);
  // A manifest entry without source is skills-link's error, not a missing link.
  assert.doesNotMatch(line, /gone/);

  sb.write('.claude/skills/added/SKILL.md', '---\nname: added\ndisable-model-invocation: true\n---\n');
  fs.rmSync(path.join(sb.root, '.claude/skills/archived'), { recursive: true });
  sb.run('relay-state.mjs');
  assert.doesNotMatch(alertsLine(sb), /skill links/);
});

test('sync conflict files are counted with their folders, skipping BACKUPS/ and ARCHIVE/', (t) => {
  const sb = sandbox({ 'relay-brain.yml': brainText() });
  t.after(sb.cleanup);
  sb.run('relay-state.mjs');
  assert.doesNotMatch(alertsLine(sb), /sync conflict/);

  sb.write('PROJECTS/demo/notes.sync-conflict-20260101-120000-ABCDEFG.md', 'x');
  sb.write('LOG/HOST-A (conflicted copy 2026-01-01).md', 'x');
  sb.write('BACKUPS/old.sync-conflict-20250101-120000-ABCDEFG.md', 'x');
  sb.write('ARCHIVE/x/old (conflicted copy).md', 'x');
  sb.write('node_modules/pkg/a.sync-conflict-1.js', 'x');
  sb.run('relay-state.mjs');
  const line = alertsLine(sb);
  assert.match(line, /2 sync conflict file\(s\) \(/);
  assert.match(line, /PROJECTS\/demo/);
  assert.match(line, /\bLOG\b/);
  assert.doesNotMatch(line, /BACKUPS\/old|ARCHIVE\/x|node_modules/);
});

test('AGENTS.md over its 9 KB cap calls for a removal pass', (t) => {
  const sb = sandbox({ 'relay-brain.yml': brainText(), 'AGENTS.md': 'x'.repeat(9 * 1024) });
  t.after(sb.cleanup);
  sb.run('relay-state.mjs');
  assert.doesNotMatch(alertsLine(sb), /AGENTS\.md/);
  sb.write('AGENTS.md', 'x'.repeat(9 * 1024 + 1));
  sb.run('relay-state.mjs');
  assert.match(alertsLine(sb), /AGENTS\.md at 9217 B \(cap 9216\) → removal pass: \/inbox --prune/);
});

test('tooling budget: alert past 20 % of the last 7 days of journal, pro rata, silent below', (t) => {
  const entry = (d, project, n) => `## ${day(-d)} 09:${String(n).padStart(2, '0')} — Claude (${project}: subject ${n})\n- [x] bullet\n\n`;
  const write = (sb, tooling) => {
    // 10 recent entries over two machines: `tooling` on relay, one shared (relay + demo, counts 0.5),
    // the rest on demo. Plus old relay entries that must not count.
    let a = '# Log\n\n', b = '# Log\n\n';
    for (let i = 0; i < 10; i++) {
      const p = i < tooling ? 'relay' : i === tooling ? 'relay + demo' : 'demo';
      if (i % 2) a += entry(i % 6, p, i); else b += entry(i % 6, p, i);
    }
    for (let i = 0; i < 20; i++) a += entry(10 + i, 'relay', i);
    sb.write('LOG/HOST-A.md', a);
    sb.write('LOG/HOST-B.md', b);
  };
  const sb = sandbox({ 'relay-brain.yml': brainText() });
  t.after(sb.cleanup);

  write(sb, 1); // 1.5 / 10 = 15 %
  sb.run('relay-state.mjs');
  assert.doesNotMatch(alertsLine(sb), /tooling/);

  write(sb, 2); // 2.5 / 10 = 25 %
  sb.run('relay-state.mjs');
  assert.match(alertsLine(sb), /tooling at 25 % of the journal over 7 days \(target < 20 %\) → no new tool without removing one/);

  // Fewer than 10 recent entries: too few to mean anything.
  sb.write('LOG/HOST-A.md', `# Log\n\n${entry(0, 'relay', 1)}${entry(1, 'relay', 2)}`);
  sb.write('LOG/HOST-B.md', '# Log\n');
  sb.run('relay-state.mjs');
  assert.doesNotMatch(alertsLine(sb), /tooling/);
});
