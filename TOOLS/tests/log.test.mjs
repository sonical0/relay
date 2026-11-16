import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { sandbox, brainText, day, monthsBack } from './helpers.mjs';

const TRAILER = /^<!-- relay-log generated: ([0-9a-f ]*) -->\s*$/m;
const sha8 = (s) => crypto.createHash('sha1').update(s.trim()).digest('hex').slice(0, 8);
const heads = (text) => [...text.matchAll(/^## .*$/gm)].map((m) => m[0]);

test('no LOG/ and no entry: nothing touched, exit 0', (t) => {
  const stub = '# RELAY_LOG\n\n> stub — newest entry on top.\n';
  const sb = sandbox({ 'RELAY_LOG.md': stub });
  t.after(sb.cleanup);
  const r = sb.run('relay-log.mjs');
  assert.equal(r.status, 0, r.out);
  assert.equal(sb.read('RELAY_LOG.md'), stub);
  assert.equal(sb.exists('LOG'), false);
});

test('merge of two machines, hand-written entry moved, idempotent', (t) => {
  const today = day(0);
  const yesterday = day(-1);
  const sb = sandbox({
    'relay-brain.yml': brainText(['HOST-A', 'HOST-B']),
    'LOG/HOST-A.md': `# Relay log — HOST-A\n\n## ${today} 10:00 — Claude (demo: a2)\n- [demo] second on A\n\n## ${yesterday} 09:00 — Claude (demo: a1)\n- [demo] first on A\n`,
    'LOG/HOST-B.md': `# Relay log — HOST-B\n\n## ${today} 11:00 — Gemini (demo: b2)\n- [demo] on B\n\n## ${yesterday} 12:00 — Gemini (demo: b1)\n- [demo] older on B\n`,
  });
  t.after(sb.cleanup);

  let r = sb.run('relay-log.mjs');
  assert.equal(r.status, 0, r.out);
  let log = sb.read('RELAY_LOG.md');
  assert.deepEqual(heads(log), [
    `## ${today} 11:00 — Gemini (demo: b2)`,
    `## ${today} 10:00 — Claude (demo: a2)`,
    `## ${yesterday} 12:00 — Gemini (demo: b1)`,
    `## ${yesterday} 09:00 — Claude (demo: a1)`,
  ]);
  // Exactly one trailer, listing the fingerprint of every header.
  assert.equal(log.match(/<!-- relay-log generated:/g).length, 1);
  assert.deepEqual(log.match(TRAILER)[1].split(' '), heads(log).map(sha8));
  assert.ok(log.trimEnd().endsWith('-->'));

  // An entry written by hand in the generated file is moved into this machine's file.
  const hand = `## ${today} 12:30 — Claude (demo: by hand)\n- [demo] written in RELAY_LOG.md\n\n`;
  sb.write('RELAY_LOG.md', log.replace(/^## /m, hand + '## '));
  r = sb.run('relay-log.mjs');
  assert.equal(r.status, 0, r.out);
  const own = sb.read('LOG/HOST-A.md');
  assert.match(own, /by hand/);
  assert.match(own, /<!-- adopted from RELAY_LOG\.md/);
  assert.ok(own.indexOf('by hand') < own.indexOf('(demo: a2)'), 'inserted at its chronological place');
  log = sb.read('RELAY_LOG.md');
  assert.equal(heads(log)[0], `## ${today} 12:30 — Claude (demo: by hand)`);
  assert.doesNotMatch(log, /adopted from/);
  assert.equal(sb.read('LOG/HOST-B.md').includes('by hand'), false);

  // Idempotent: a second run writes nothing.
  const before = sb.read('RELAY_LOG.md');
  const mtime = sb.mtime('RELAY_LOG.md');
  r = sb.run('relay-log.mjs');
  assert.equal(r.status, 0, r.out);
  assert.match(r.stdout, /unchanged/);
  assert.equal(sb.read('RELAY_LOG.md'), before);
  assert.equal(sb.mtime('RELAY_LOG.md'), mtime);
});

test('a log written before per-machine files is migrated, whatever the entry dates', (t) => {
  const old = `${monthsBack(1)}-03`;
  const sb = sandbox({
    'RELAY_LOG.md': `# RELAY_LOG\n\n> preamble\n\n## ${day(0)} — Claude (demo: new)\n- [demo] x\n\n## ${old} — Claude (demo: old)\n- [demo] y\n`,
  });
  t.after(sb.cleanup);
  const r = sb.run('relay-log.mjs');
  assert.equal(r.status, 0, r.out);
  assert.match(sb.read('LOG/HOST-A.md'), /\(demo: old\)/);
  assert.equal(heads(sb.read('RELAY_LOG.md')).length, 2);
  assert.match(sb.read('RELAY_LOG.md'), TRAILER);
});
