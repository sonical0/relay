import test from 'node:test';
import assert from 'node:assert/strict';
import { sandbox, brainText, day } from './helpers.mjs';

test('after-turn: second run with unchanged inputs does nothing', (t) => {
  const sb = sandbox({
    'relay-brain.yml': brainText(),
    'LOG/HOST-A.md': `# Relay log — HOST-A\n\n## ${day(0)} 10:00 — Claude (demo: start)\n- [demo] started\n`,
    'AGENTS.md': '# Agents\n\nShared rules for every agent working in this root, long enough to be indexed.\n',
  });
  t.after(sb.cleanup);

  let r = sb.run('relay-after-turn.mjs', ['--scan']);
  assert.equal(r.status, 0, r.out);
  assert.match(r.stdout, /state/);
  assert.ok(sb.exists('RELAY_LOG.md'));
  assert.ok(sb.exists('.relay-state.md'));
  assert.ok(sb.exists('.relay-rag.json'));
  const ragMtime = sb.mtime('.relay-rag.json');
  const stateMtime = sb.mtime('.relay-state.md');
  const logMtime = sb.mtime('RELAY_LOG.md');

  r = sb.run('relay-after-turn.mjs', ['--scan']);
  assert.equal(r.status, 0, r.out);
  assert.match(r.stdout, /skipped \(inputs unchanged\)/);
  assert.equal(sb.mtime('.relay-rag.json'), ragMtime);
  assert.equal(sb.mtime('.relay-state.md'), stateMtime);
  assert.equal(sb.mtime('RELAY_LOG.md'), logMtime);

  // A new log entry changes the inputs: state and index run again and see it.
  sb.write('LOG/HOST-A.md', sb.read('LOG/HOST-A.md').replace('\n\n## ', `\n\n## ${day(0)} 11:00 — Claude (demo: next)\n- [demo] continued\n\n## `));
  r = sb.run('relay-after-turn.mjs', ['--scan']);
  assert.equal(r.status, 0, r.out);
  assert.doesNotMatch(r.stdout, /skipped/);
  assert.match(sb.read('.relay-state.md'), /demo: next/);

  // --force ignores the threshold.
  r = sb.run('relay-after-turn.mjs', ['--scan', '--force']);
  assert.doesNotMatch(r.stdout, /skipped/);
});

test('after-turn on a satellite never touches the split brain', (t) => {
  const sb = sandbox({ 'relay-brain.yml': brainText(['HOST-A']).replace('    os: linux', '    os: linux\n    role: satellite') });
  t.after(sb.cleanup);
  assert.equal(sb.run('relay-brain.mjs', ['--migrate']).status, 0);
  const edited = sb.read('relay-brain.yml').replace('/tmp/relay', '/edited');
  sb.write('relay-brain.yml', edited);
  const r = sb.run('relay-after-turn.mjs', ['--scan']);
  assert.equal(r.status, 0, r.out);
  assert.doesNotMatch(r.stdout, /brain/);
  assert.doesNotMatch(sb.read('BRAIN/machines/HOST-A.yml'), /edited/);
});
