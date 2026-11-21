import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { TOOLS, sandbox } from './helpers.mjs';

// Every run gets its own TMPDIR: the bar and durations.json live under os.tmpdir()/relay-progress.
function tmpEnv(t) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'relay-test-progress-'));
  t.after(() => fs.rmSync(tmp, { recursive: true, force: true }));
  const dir = path.join(tmp, 'relay-progress');
  const run = (script, args, extra = {}) => spawnSync(process.execPath, [path.join(TOOLS, script), ...args], {
    encoding: 'utf8', timeout: 60_000,
    env: { ...process.env, TMPDIR: tmp, TEMP: tmp, TMP: tmp, CLAUDE_CODE_SESSION_ID: 'sess-1', ...extra },
  });
  const bar = () => { try { return JSON.parse(fs.readFileSync(path.join(dir, 'sess-1.json'), 'utf8')); } catch { return null; } };
  return { tmp, dir, run, bar };
}

test('progress writes the bar, keeps the start while the task goes on, resets it otherwise', async (t) => {
  const { run, bar } = tmpEnv(t);
  assert.equal(run('progress.mjs', ['1', '5', 'item', 'one']).status, 0);
  const first = bar();
  assert.equal(first.k, 1);
  assert.equal(first.n, 5);
  assert.equal(first.label, 'item one');
  await new Promise((r) => setTimeout(r, 20));

  run('progress.mjs', ['3', '5', '--remaining', '42']);
  assert.equal(bar().start, first.start, 'same task: start kept');
  assert.equal(bar().remaining_s, 42);

  run('progress.mjs', ['2', '5']);
  assert.ok(bar().start > first.start, 'k went down: start reset');
  const second = bar().start;
  await new Promise((r) => setTimeout(r, 20));
  run('progress.mjs', ['2', '7']);
  assert.ok(bar().start > second, 'N changed: start reset');

  assert.equal(run('progress.mjs', ['done']).status, 0);
  assert.equal(bar(), null);
});

test('progress: bad arguments exit 2, no session id does nothing', (t) => {
  const { run, bar } = tmpEnv(t);
  assert.equal(run('progress.mjs', ['x', '5']).status, 2);
  assert.equal(run('progress.mjs', ['1', '5'], { CLAUDE_CODE_SESSION_ID: '' }).status, 0);
  assert.equal(bar(), null);
});

test('progress-run keeps the exit code, records only successful durations, clears the bar', (t) => {
  const { dir, run, bar } = tmpEnv(t);
  const ok = run('progress-run.mjs', ['ok', '--key', 'k1', '--', process.execPath, '-e', 'setTimeout(()=>{},300)']);
  assert.equal(ok.status, 0, ok.stderr);
  assert.equal(bar(), null, 'bar cleared');
  const durations = JSON.parse(fs.readFileSync(path.join(dir, 'durations.json'), 'utf8'));
  assert.equal(durations['run:k1'].length, 1);
  assert.ok(durations['run:k1'][0] >= 250);

  const ko = run('progress-run.mjs', ['ko', '--key', 'k2', '--', process.execPath, '-e', 'process.exit(3)']);
  assert.equal(ko.status, 3);
  assert.equal(bar(), null);
  assert.equal(JSON.parse(fs.readFileSync(path.join(dir, 'durations.json'), 'utf8'))['run:k2'], undefined);

  assert.equal(run('progress-run.mjs', ['no command']).status, 2);
  assert.equal(run('progress-run.mjs', ['missing', '--', 'relay-no-such-command-xyz']).status, 127);
});

test('durations: median of the last 5, mm:ss formatting', (t) => {
  const { tmp } = tmpEnv(t);
  const code = `
    const d = await import(${JSON.stringify(new URL('../lib/durations.mjs', import.meta.url).href)});
    for (const ms of [9000, 1000, 2000, 3000, 4000, 100000]) d.recordDuration('k', ms);
    console.log(JSON.stringify([d.expectedDuration('k'), d.expectedDuration('none'), d.mmss(75), d.mmss(3900)]));
    d.recordDuration('even', 1000); d.recordDuration('even', 2000);
    console.log(d.expectedDuration('even'));`;
  const r = spawnSync(process.execPath, ['--input-type=module', '-e', code], { encoding: 'utf8', env: { ...process.env, TMPDIR: tmp, TEMP: tmp, TMP: tmp } });
  assert.equal(r.status, 0, r.stderr);
  const [line1, line2] = r.stdout.trim().split('\n');
  assert.deepEqual(JSON.parse(line1), [3000, null, '1:15', '1 h 05'], 'the oldest of 6 is dropped');
  assert.equal(line2, '1500');
});

test('token-scan and hook-stats read the transcripts; the session file wins over the cwd mapping', (t) => {
  const sb = sandbox();
  t.after(sb.cleanup);
  const tmp = path.join(sb.base, 'tmp');
  fs.mkdirSync(tmp);
  const app = path.join(sb.base, 'clones', 'app');
  fs.mkdirSync(app, { recursive: true });
  sb.write('relay-brain.yml', `machines:\n  HOST-A:\n    relay_root: ${JSON.stringify(sb.root)}\nprojects:\n  app:\n    path: ${JSON.stringify(app)}\n`);
  sb.write('.relay-reprise/_sessions.tsv', 's-forced\tother-proj\tB\t2026-01-01T00:00:00Z\n');
  const ts = new Date().toISOString();
  const msg = (id, sessionId, cwd, cacheRead) => JSON.stringify({
    type: 'assistant', timestamp: ts, sessionId, cwd,
    message: { id, model: 'm', usage: { input_tokens: 10, output_tokens: 20, cache_read_input_tokens: cacheRead, cache_creation_input_tokens: 0 } },
  });
  const lines = [
    msg('a1', 's-app', path.join(app, 'src'), 200_000),
    msg('a2', 's-app', app, 200_000),
    msg('a2', 's-app', app, 200_000), // same message twice: counted once
    msg('r1', 's-root', sb.root, 1000),
    msg('f1', 's-forced', app, 5000),
    JSON.stringify({ type: 'assistant', uuid: 'u1', timestamp: ts, message: { content: [{ type: 'tool_use', name: 'Skill', input: { skill: 'inbox' } }] } }),
    JSON.stringify({ type: 'attachment', timestamp: ts, attachment: { hookEvent: 'Stop', command: 'node /x/y/relay-stop-hook.js', durationMs: 40 } }),
  ];
  const tdir = path.join(sb.config, 'projects', 'p');
  fs.mkdirSync(tdir, { recursive: true });
  fs.writeFileSync(path.join(tdir, 's.jsonl'), lines.join('\n') + '\n');
  const env = { TMPDIR: tmp, TEMP: tmp, TMP: tmp };

  const r = sb.run('token-scan.mjs', ['--days', '7', '--json'], env);
  assert.equal(r.status, 0, r.out);
  const rows = Object.fromEntries(JSON.parse(r.stdout).rows.map((g) => [g.key, g]));
  assert.equal(rows.app.msgs, 2);
  assert.equal(rows.app.reread_per_call, 200_000);
  assert.equal(rows.relay.msgs, 1);
  assert.equal(rows['other-proj'].msgs, 1);
  assert.equal(rows.app.weighted, Math.round(2 * (10 + 20 * 5 + 200_000 * 0.1)));

  const text = sb.run('token-scan.mjs', ['--sort', 'reread'], env);
  assert.match(text.stdout.split('\n')[2], /^app\t.*200 k ⚠/);

  const sk = sb.run('token-scan.mjs', ['--skills', '--json'], env);
  assert.deepEqual(JSON.parse(sk.stdout).used.map((u) => `${u.type} ${u.name} ${u.n}`), ['skill inbox 1']);

  const hs = sb.run('hook-stats.mjs', ['--days', '1'], env);
  assert.equal(hs.status, 0, hs.out);
  assert.match(hs.stdout, /^1\t40\t40\t40\t0\tStop \| node relay-stop-hook\.js$/m);
});
