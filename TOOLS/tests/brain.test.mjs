import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { sandbox, brainText, CLONE } from './helpers.mjs';

test('split brain is opt-in: without BRAIN/ nothing is touched', (t) => {
  const sb = sandbox({ 'relay-brain.yml': brainText() });
  t.after(sb.cleanup);
  const before = sb.read('relay-brain.yml');
  const r = sb.run('relay-brain.mjs');
  assert.equal(r.status, 0, r.out);
  assert.match(r.stdout, /off/);
  assert.equal(sb.exists('BRAIN'), false);
  assert.equal(sb.read('relay-brain.yml'), before);
});

test('the shipped template migrates and regenerates to the same content', (t) => {
  const sb = sandbox();
  t.after(sb.cleanup);
  const template = fs.readFileSync(path.join(CLONE, 'relay-brain.template.yml'), 'utf8').replace(/\r\n/g, '\n');
  sb.write('relay-brain.yml', template);
  const r = sb.run('relay-brain.mjs', ['--migrate']);
  assert.equal(r.status, 0, r.out);
  assert.ok(sb.exists('BRAIN/machines/MY-MACHINE.yml'));
  // Text identical apart from the fingerprint line appended at the end.
  const out = sb.read('relay-brain.yml');
  assert.ok(out.startsWith(template.replace(/\n+$/, '\n')), 'regenerated brain must keep the template text and order');
  assert.match(out, /^# relay-brain generated: /m);
  // A second pass changes nothing.
  const again = sb.run('relay-brain.mjs');
  assert.equal(again.status, 0, again.out);
  assert.equal(sb.read('relay-brain.yml'), out);
});

test('split round-trip: an edit is reported to its chunk; a concurrent edit is a conflict', (t) => {
  const sb = sandbox({ 'relay-brain.yml': brainText(['HOST-A', 'HOST-B'], { projects: '  demo:\n    path: "PROJECTS/demo"\n' }) });
  t.after(sb.cleanup);
  assert.equal(sb.run('relay-brain.mjs', ['--migrate']).status, 0);
  for (const f of ['head.yml', 'machines/_.yml', 'machines/HOST-A.yml', 'machines/HOST-B.yml', 'projects/demo.yml']) {
    assert.ok(sb.exists(`BRAIN/${f}`), f);
  }

  // Edit here, chunk untouched elsewhere → reported.
  sb.write('relay-brain.yml', sb.read('relay-brain.yml').replace('  HOST-A:\n    relay_root: "/tmp/relay"', '  HOST-A:\n    relay_root: "/srv/relay"'));
  let r = sb.run('relay-brain.mjs');
  assert.equal(r.status, 0, r.out);
  assert.match(sb.read('BRAIN/machines/HOST-A.yml'), /\/srv\/relay/);

  // A new project added by hand → new chunk.
  sb.write('relay-brain.yml', sb.read('relay-brain.yml').replace('projects:\n', 'projects:\n  other:\n    path: "PROJECTS/other"\n'));
  r = sb.run('relay-brain.mjs');
  assert.equal(r.status, 0, r.out);
  assert.ok(sb.exists('BRAIN/projects/other.yml'));

  // Another machine changed HOST-B's chunk while it was edited here → conflict, chunk wins.
  sb.write('BRAIN/machines/HOST-B.yml', '  HOST-B:\n    relay_root: "/from/elsewhere"\n    os: linux\n');
  sb.write('relay-brain.yml', sb.read('relay-brain.yml').replace(/ {2}HOST-B:\n {4}relay_root: "[^"]*"/, '  HOST-B:\n    relay_root: "/edited/here"'));
  r = sb.run('relay-brain.mjs');
  assert.equal(r.status, 0, r.out);
  assert.match(r.stdout, /conflict/);
  assert.match(sb.read('BRAIN/machines/HOST-B.yml'), /from\/elsewhere/);
  const conflicts = fs.readdirSync(path.join(sb.root, 'BRAIN', '_conflicts'));
  assert.equal(conflicts.length, 1);
  assert.match(fs.readFileSync(path.join(sb.root, 'BRAIN', '_conflicts', conflicts[0]), 'utf8'), /edited\/here/);
  assert.match(sb.read('relay-brain.yml'), /from\/elsewhere/);

  // The injected state reports it.
  sb.run('relay-state.mjs');
  assert.match(sb.read('.relay-state.md'), /BRAIN\/_conflicts/);
});

test('an edit that breaks the YAML is neither reported nor overwritten', (t) => {
  const sb = sandbox({ 'relay-brain.yml': brainText() });
  t.after(sb.cleanup);
  assert.equal(sb.run('relay-brain.mjs', ['--migrate']).status, 0);
  const broken = sb.read('relay-brain.yml').replace('machines:', 'machines: [unclosed');
  sb.write('relay-brain.yml', broken);
  const r = sb.run('relay-brain.mjs');
  assert.equal(r.status, 1);
  assert.equal(sb.read('relay-brain.yml'), broken);
});
