import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { sandbox, snapshot, CLONE } from './helpers.mjs';

// Stand-in for relay-setup-hook.js: the real one is never run by the tests. It installs one
// hook into $CLAUDE_CONFIG_DIR/settings.json when missing, like the real one does.
const STUB_SETUP_HOOK = `
const fs = require('fs'), path = require('path');
const file = path.join(process.env.CLAUDE_CONFIG_DIR, 'settings.json');
let s = {};
try { s = JSON.parse(fs.readFileSync(file, 'utf8')); } catch {}
if (!s.hooks) {
  s.hooks = { Stop: [{ hooks: [{ type: 'command', command: 'node relay-stop-hook.js' }] }] };
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(s, null, 2));
  process.stderr.write('RELAY — HOOKS WERE MISSING, REINSTALLED\\n');
}
`;

test('relay-init sets up a machine, and a re-run changes nothing', (t) => {
  const sb = sandbox({
    'relay-brain.template.yml': fs.readFileSync(path.join(CLONE, 'relay-brain.template.yml'), 'utf8'),
    'relay-setup-hook.js': STUB_SETUP_HOOK,
    // Dependencies "already installed": the init must not run npm in a test.
    'node_modules/js-yaml/package.json': '{"name":"js-yaml","version":"0.0.0-test"}',
    'SKILLS/skills.manifest.json': JSON.stringify({ skills: [{ name: 'inbox', src: 'inbox' }] }),
    'SKILLS/inbox/SKILL.md': '---\nname: inbox\ndescription: "Review proposals."\ndisable-model-invocation: true\n---\n',
    'RULES/journal.md': '# Journal rules\n',
    'AGENTS.md': '# Agents\n\nRules shared by every agent, long enough to be indexed by the RAG.\n',
  });
  t.after(sb.cleanup);

  const r1 = sb.run('relay-init.mjs', ['--yes']);
  assert.equal(r1.status, 0, r1.out);
  const brain = sb.read('relay-brain.yml').replace(/\r\n/g, '\n');
  assert.match(brain, /^ {2}HOST-A:\n {4}relay_root: "[^"]+"/m);
  assert.ok(brain.includes(`relay_root: "${sb.root.replace(/\\/g, '/')}"`));
  assert.doesNotMatch(brain, /MY-MACHINE:/);
  assert.match(brain, /# ─── Machines/, 'template comments kept');
  assert.ok(fs.existsSync(path.join(sb.config, 'settings.json')));
  for (const f of ['.relay-state.md', '.relay-rag.json', 'relay-projects.derived.yml', '.claude/rules/journal.md', '.claude/skills/inbox']) {
    assert.ok(sb.exists(f), `${f} missing\n${r1.out}`);
  }
  assert.match(r1.stdout, /additionalDirectories/);
  assert.doesNotMatch(sb.read('.relay-state.md'), /missing from relay-brain/);

  const before = { root: snapshot(sb.root), config: snapshot(sb.config) };
  const r2 = sb.run('relay-init.mjs', ['--yes']);
  assert.equal(r2.status, 0, r2.out);
  assert.match(r2.stdout, /HOST-A already in relay-brain\.yml/);
  assert.match(r2.stdout, /hooks already in place/);
  assert.deepEqual({ root: snapshot(sb.root), config: snapshot(sb.config) }, before);
});

test('relay-init --dry-run writes nothing', (t) => {
  const sb = sandbox({
    'relay-brain.template.yml': fs.readFileSync(path.join(CLONE, 'relay-brain.template.yml'), 'utf8'),
    'relay-setup-hook.js': STUB_SETUP_HOOK,
    'node_modules/js-yaml/package.json': '{"name":"js-yaml"}',
  });
  t.after(sb.cleanup);
  const before = snapshot(sb.root);
  const r = sb.run('relay-init.mjs', ['--dry-run']);
  assert.equal(r.status, 0, r.out);
  assert.deepEqual(snapshot(sb.root), before);
  assert.deepEqual(snapshot(sb.config), {});
});
