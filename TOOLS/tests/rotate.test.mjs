import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { sandbox, brainText, day, monthsBack, CLONE } from './helpers.mjs';

test('rotation keeps the current and previous month, archives the rest per host', (t) => {
  const cur = day(0);
  const prev = `${monthsBack(1)}-15`;
  const old = `${monthsBack(3)}-10`;
  const sb = sandbox({
    'relay-brain.yml': brainText(['HOST-A', 'HOST-B']),
    'LOG/HOST-A.md': `# Relay log — HOST-A\n\n## ${cur} 10:00 — Claude (demo: current)\n- a\n\n## ${prev} 10:00 — Claude (demo: previous)\n- b\n\n## ${old} 10:00 — Claude (demo: old)\n- c\n`,
    'LOG/HOST-B.md': `# Relay log — HOST-B\n\n## ${old} 09:00 — Gemini (demo: old on B)\n- d\n`,
  });
  t.after(sb.cleanup);
  assert.equal(sb.run('relay-log.mjs').status, 0);
  assert.match(sb.read('RELAY_LOG.md'), /old on B/);

  const r = sb.run('log-rotate.mjs');
  assert.equal(r.status, 0, r.out);
  const own = sb.read('LOG/HOST-A.md');
  assert.match(own, /\(demo: current\)/);
  assert.match(own, /\(demo: previous\)/);
  assert.doesNotMatch(own, /\(demo: old\)/);
  const archive = sb.read(`LOG/ARCHIVE/${monthsBack(3)}-HOST-A.md`);
  assert.match(archive, /\(demo: old\)/);
  // Only this machine's file is rotated.
  assert.match(sb.read('LOG/HOST-B.md'), /old on B/);
  // The merged log is regenerated without the archived entry.
  const log = sb.read('RELAY_LOG.md');
  assert.doesNotMatch(log, /\(demo: old\)/);
  assert.match(log, /\(demo: current\)/);

  // Nothing left to rotate: a second run writes nothing.
  const again = sb.run('log-rotate.mjs');
  assert.equal(again.status, 0, again.out);
  assert.match(again.stdout, /Nothing to archive/);
});

test('--brain moves cross_project_log entries older than 30 days to the history', (t) => {
  const log = [
    'cross_project_log:',
    `  - { date: "${day(-45)}", agent: claude, project: demo, note: "old note" }`,
    `  - date: "${day(-40)}"`,
    '    agent: claude',
    '    project: demo',
    '    note: "old block note"',
    `  - { date: "${day(-2)}", agent: claude, project: demo, note: "fresh note" }`,
    '',
  ].join('\n');
  const sb = sandbox({ 'relay-brain.yml': brainText(['HOST-A'], { log }) });
  t.after(sb.cleanup);
  const r = sb.run('log-rotate.mjs', ['--brain']);
  assert.equal(r.status, 0, r.out);
  const brain = sb.read('relay-brain.yml');
  assert.doesNotMatch(brain, /old note|old block note/);
  assert.match(brain, /fresh note/);
  assert.match(brain, /# ─── Machines ───/, 'comments survive');
  const hist = sb.read('relay-brain-history.yml');
  assert.match(hist, /old note/);
  assert.match(hist, /old block note/);

  // Idempotent: nothing appended twice.
  assert.equal(sb.run('log-rotate.mjs', ['--brain']).status, 0);
  assert.equal(sb.read('relay-brain-history.yml').match(/old note/g).length, 1);
});

test('--brain on the shipped template keeps the comments that follow the last entry', (t) => {
  const template = fs.readFileSync(path.join(CLONE, 'relay-brain.template.yml'), 'utf8').replace(/\r\n/g, '\n');
  const brain = template.replace(/^cross_project_log: \[\]$/m,
    `cross_project_log:\n  - { date: "${day(-3)}", agent: claude, project: demo, note: "recent" }\n  - { date: "${day(-40)}", agent: claude, project: demo, note: "stale" }`);
  assert.notEqual(brain, template, 'template shape changed: cross_project_log: [] not found');
  const sb = sandbox({ 'relay-brain.yml': brain });
  t.after(sb.cleanup);
  // Both the plain file and the split brain must keep the closing comments.
  const tail = template.slice(template.indexOf('cross_project_log: []') + 'cross_project_log: []'.length).trim();
  assert.ok(tail.length > 0);
  const r = sb.run('log-rotate.mjs', ['--brain']);
  assert.equal(r.status, 0, r.out);
  const out = sb.read('relay-brain.yml');
  assert.doesNotMatch(out, /stale/);
  assert.match(out, /recent/);
  assert.ok(out.includes(tail), 'closing comments survive the rotation');

  // Same with the split brain on: the chunk of the removed entry goes, the comments stay.
  sb.write('relay-brain.yml', brain);
  assert.equal(sb.run('relay-brain.mjs', ['--migrate']).status, 0);
  assert.ok(sb.exists('BRAIN/eof.yml'));
  assert.equal(sb.run('log-rotate.mjs', ['--brain']).status, 0);
  assert.equal(sb.run('relay-brain.mjs').status, 0);
  const split = sb.read('relay-brain.yml');
  assert.doesNotMatch(split, /stale/);
  assert.ok(split.includes(tail));
  assert.equal(fs.readdirSync(path.join(sb.root, 'BRAIN', 'cross_project_log')).filter((f) => f !== '_.yml').length, 1);
});
