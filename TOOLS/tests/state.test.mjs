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
