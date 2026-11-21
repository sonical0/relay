// The three Claude Code hooks. Each hook takes its root from its own location (__dirname),
// so it is copied into the sandbox root and run from there: the clone and the real
// ~/.claude are never touched (CLAUDE_CONFIG_DIR points to the sandbox).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { sandbox, brainText, CLONE } from './helpers.mjs';

function runHook(sb, hook, payload = {}, extra = {}) {
  const dest = path.join(sb.root, hook);
  if (!fs.existsSync(dest)) fs.copyFileSync(path.join(CLONE, hook), dest);
  const r = spawnSync(process.execPath, [dest], {
    cwd: sb.root, encoding: 'utf8', env: sb.env(extra), input: JSON.stringify(payload), timeout: 30_000,
  });
  return { status: r.status, stdout: r.stdout ?? '', stderr: r.stderr ?? '', out: `${r.stdout}\n${r.stderr}` };
}
const context = (r) => JSON.parse(r.stdout).hookSpecificOutput.additionalContext;
const tmpKeyFiles = (root) => {
  const key = crypto.createHash('sha1').update(root.toLowerCase()).digest('hex').slice(0, 12);
  for (const f of fs.readdirSync(os.tmpdir())) {
    if (f.includes(key) && /^relay-(scan|rotate|start-logs)-/.test(f)) fs.rmSync(path.join(os.tmpdir(), f), { force: true });
  }
};

test('start hook: fresh resume card injected, card older than 48 h ignored', (t) => {
  const sb = sandbox({
    '.relay-state.md': '# Relay state\n\nGeneral view.\n',
    '.relay-reprise/demo/A.md': 'Goal: ship the parser.\nTo do: error messages.\n',
  });
  t.after(sb.cleanup);
  const env = { RELAY_PROJECT: 'demo', RELAY_AGENT: 'A', WMUX_SURFACE_ID: '' };

  let r = runHook(sb, 'relay-start-hook.js', { cwd: sb.root, source: 'startup' }, env);
  assert.equal(r.status, 0, r.out);
  let ctx = context(r);
  assert.match(ctx, /General view/);
  assert.match(ctx, /\[RELAY · resume\] Resume card/);
  assert.match(ctx, /ship the parser/);
  assert.match(ctx, /\[RELAY · session\] demo_A/);

  const old = (Date.now() - 49 * 3600 * 1000) / 1000;
  fs.utimesSync(path.join(sb.root, '.relay-reprise/demo/A.md'), old, old);
  ctx = context(runHook(sb, 'relay-start-hook.js', { cwd: sb.root }, env));
  assert.doesNotMatch(ctx, /ship the parser/);
  assert.match(ctx, /older than 48 h ignored/);
});

test('start hook: no card → where to write it; unnamed session → warning', (t) => {
  const sb = sandbox({ '.relay-state.md': '# Relay state\n' });
  t.after(sb.cleanup);
  const ctx = context(runHook(sb, 'relay-start-hook.js', { cwd: sb.root }, { WMUX_SURFACE_ID: '' }));
  assert.ok(ctx.includes(path.join(sb.root, '.relay-reprise', 'relay', 'main.md')), ctx);
  assert.match(ctx, /Before \/clear or at the end of a task/);
  assert.match(ctx, /No session name detected/);
});

test('start hook: a card from another wmux pane is refused', (t) => {
  const sb = sandbox({
    '.relay-state.md': '# Relay state\n',
    '.relay-reprise/relay/pane-11112222.md': 'surface: surf-99998888\nGoal: secret plan.\n',
  });
  t.after(sb.cleanup);
  const ctx = context(runHook(sb, 'relay-start-hook.js', { cwd: sb.root }, { WMUX_SURFACE_ID: 'surf-11112222-aaaa' }));
  assert.doesNotMatch(ctx, /secret plan/);
  assert.match(ctx, /another pane/);
});

test('start hook: log change refreshes the state in the background, not inline', (t) => {
  const sb = sandbox({ '.relay-state.md': '# Old state\n', 'LOG/HOST-A.md': '# log\n' });
  t.after(() => { tmpKeyFiles(sb.root); sb.cleanup(); });
  const marker = path.join(sb.root, 'refreshed.txt');
  sb.write('TOOLS/relay-log.mjs', 'await new Promise((r) => setTimeout(r, 3000));\n');
  sb.write('TOOLS/relay-state.mjs', `import fs from 'node:fs'; fs.writeFileSync(${JSON.stringify(marker)}, 'ok');\n`);
  const t0 = Date.now();
  const r = runHook(sb, 'relay-start-hook.js', { cwd: sb.root });
  assert.equal(r.status, 0, r.out);
  assert.match(context(r), /Old state/);
  assert.ok(Date.now() - t0 < 2500, 'the hook must not wait for the refresh');
  assert.ok(!fs.existsSync(marker));
  const until = Date.now() + 15_000;
  while (!fs.existsSync(marker) && Date.now() < until) spawnSync(process.execPath, ['-e', 'setTimeout(()=>{},200)']);
  assert.ok(fs.existsSync(marker), 'detached refresh ran');
});

test('stop hook: block message asks for the cause and an INBOX proposal', (t) => {
  const sb = sandbox({ 'relay-brain.yml': brainText() });
  const sessionId = `test-${crypto.randomUUID()}`;
  const flag = path.join(os.tmpdir(), 'relay-stop', `${crypto.createHash('sha1').update(sessionId).digest('hex')}.flag`);
  t.after(() => { fs.rmSync(flag, { force: true }); tmpKeyFiles(sb.root); sb.cleanup(); });

  let r = runHook(sb, 'relay-stop-hook.js', { session_id: sessionId });
  assert.equal(r.status, 0, r.out);
  const out = JSON.parse(r.stdout);
  assert.equal(out.decision, 'block');
  assert.match(out.reason, /No journal entry/);
  assert.match(out.reason, /identify why/);
  assert.match(out.reason, /forgot.*session without work.*written elsewhere/s);
  assert.match(out.reason, /INBOX\/HOST-A\//);

  // Second pass: warns, never blocks again.
  r = runHook(sb, 'relay-stop-hook.js', { session_id: sessionId });
  assert.equal(r.stdout.trim(), '{}');
});

test('setup hook: deny entries and statusLine refreshInterval, idempotent, user values kept', (t) => {
  const sb = sandbox();
  t.after(sb.cleanup);
  const settingsPath = path.join(sb.config, 'settings.json');
  fs.writeFileSync(settingsPath, JSON.stringify({
    permissions: { deny: ['Bash(rm -rf *)', 'Bash(git add .)'] },
    statusLine: { type: 'command', command: 'python "/x/TOOLS/supervision/statusline.py"' },
  }));

  let r = runHook(sb, 'relay-setup-hook.js');
  assert.equal(r.status, 0, r.out);
  let s = JSON.parse(fs.readFileSync(settingsPath, 'utf8'));
  for (const rule of ['Bash(git add -A)', 'Bash(git add --all)', 'Bash(rtk git add .)', 'Bash(rtk git add -A)', 'Bash(rtk git add --all)']) {
    assert.ok(s.permissions.deny.includes(rule), rule);
  }
  assert.ok(s.permissions.deny.includes('Bash(rm -rf *)'));
  assert.equal(s.permissions.deny.filter((x) => x === 'Bash(git add .)').length, 1);
  assert.equal(s.statusLine.refreshInterval, 2);
  assert.ok(s.hooks.SessionStart && s.hooks.Stop);

  const before = fs.readFileSync(settingsPath, 'utf8');
  r = runHook(sb, 'relay-setup-hook.js');
  assert.equal(r.status, 0, r.out);
  assert.equal(fs.readFileSync(settingsPath, 'utf8'), before, 'second run changes nothing');

  // A user refreshInterval and a foreign status line are never touched.
  s.statusLine.refreshInterval = 10;
  fs.writeFileSync(settingsPath, JSON.stringify(s));
  runHook(sb, 'relay-setup-hook.js');
  assert.equal(JSON.parse(fs.readFileSync(settingsPath, 'utf8')).statusLine.refreshInterval, 10);
  s.statusLine = { type: 'command', command: 'other-statusline' };
  fs.writeFileSync(settingsPath, JSON.stringify(s));
  runHook(sb, 'relay-setup-hook.js');
  assert.equal(JSON.parse(fs.readFileSync(settingsPath, 'utf8')).statusLine.refreshInterval, undefined);
});
