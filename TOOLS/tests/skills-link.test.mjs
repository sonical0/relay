import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { sandbox } from './helpers.mjs';

test('skills-link links manifest skills and copies RULES/*.md, removing only its own stale copies', (t) => {
  const sb = sandbox({
    'SKILLS/skills.manifest.json': JSON.stringify({ skills: [{ name: 'inbox', src: 'inbox', why: 'test' }] }),
    'SKILLS/inbox/SKILL.md': '---\nname: inbox\ndescription: "Review proposals."\ndisable-model-invocation: true\n---\n',
    'RULES/journal.md': '# Journal rules\n',
    'RULES/brain.md': '# Brain rules\n',
    '.claude/rules/handwritten.md': '# Mine\n',
  });
  t.after(sb.cleanup);

  let r = sb.run('skills-link.mjs');
  assert.equal(r.status, 0, r.out);
  assert.ok(fs.existsSync(path.join(sb.root, '.claude', 'skills', 'inbox', 'SKILL.md')));
  assert.match(r.stdout, /on demand 1/);
  assert.equal(sb.read('.claude/rules/journal.md'), '# Journal rules\n');
  assert.equal(sb.read('.claude/rules/brain.md'), '# Brain rules\n');
  // A copy, not a link.
  assert.equal(fs.lstatSync(path.join(sb.root, '.claude', 'rules', 'journal.md')).isSymbolicLink(), false);

  // Idempotent.
  r = sb.run('skills-link.mjs');
  assert.doesNotMatch(r.stdout, /rules written/);

  // Source updated, then removed.
  sb.write('RULES/journal.md', '# Journal rules v2\n');
  fs.rmSync(path.join(sb.root, 'RULES', 'brain.md'));
  r = sb.run('skills-link.mjs');
  assert.equal(r.status, 0, r.out);
  assert.equal(sb.read('.claude/rules/journal.md'), '# Journal rules v2\n');
  assert.equal(sb.exists('.claude/rules/brain.md'), false);
  assert.equal(sb.read('.claude/rules/handwritten.md'), '# Mine\n', 'a rule written by hand survives');
});
