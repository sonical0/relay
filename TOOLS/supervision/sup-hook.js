#!/usr/bin/env node
// Claude Code hook of the supervision: an agent's state comes from its own events, not from reading its screen.
// One script for UserPromptSubmit, PreToolUse, PostToolUse, PermissionRequest, Notification, Stop, StopFailure,
// SubagentStart/Stop, SessionStart, SessionEnd (asynchronous).
//
// Without SUP_AGENT (set by start-manager.ps1) it exits at once, reading and loading nothing. Otherwise it posts
// {agent, event, tool, ts, sub, kind, run} to the board (POST /api/hook, 0.5 s at most), never the content of prompts
// or tools; board unreachable: the line goes to run/<project>/hook-spool.jsonl (256 KB at most), read back by the server
// when it starts. Always exit code 0, nothing on stdout.
'use strict';
// Wiring: NOT in the global settings (relay-setup-hook.js does not install it: it would add 2 node processes per tool
// call in every session), but at a manager's launch: `claude --settings "$(node sup-hook.js --emit-settings)"`.
// --emit-settings writes run/sup-hook.settings.json (11 events, asynchronous) and prints its path.
const SUP_HOOK_EVENTS = ['UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'PermissionRequest', 'Notification', 'Stop',
  'StopFailure', 'SubagentStart', 'SubagentStop', 'SessionStart', 'SessionEnd'];
if (process.argv.includes('--emit-settings')) {
  const fs = require('node:fs'), path = require('node:path');
  const self = __filename.replace(/\\/g, '/');
  const hooks = {};
  for (const e of SUP_HOOK_EVENTS) hooks[e] = [{ hooks: [{ type: 'command', command: `node "${self}"`, timeout: 5, async: true }] }];
  const dir = process.env.SUP_STATE_ROOT || path.join(__dirname, 'run');
  fs.mkdirSync(dir, { recursive: true });
  const out = path.join(dir, 'sup-hook.settings.json');
  fs.writeFileSync(out + '.tmp', JSON.stringify({ hooks }, null, 1), 'utf8');
  fs.renameSync(out + '.tmp', out);
  process.stdout.write(out);
  process.exit(0);
}
const agent = process.env.SUP_AGENT;
if (!agent || !/^[\w.-]+\/[A-Za-z0-9]+$/.test(agent)) process.exit(0);

const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');

const SPOOL_MAX = 256 * 1024;
const project = agent.split('/')[0];

function stateDir() {
  if (process.env.SUP_RUN_DIR) return process.env.SUP_RUN_DIR;
  return path.join(process.env.SUP_STATE_ROOT || path.join(__dirname, 'run'), project);
}

function spool(line) {
  try {
    const f = path.join(stateDir(), 'hook-spool.jsonl');
    let size = 0;
    try { size = fs.statSync(f).size; } catch { /* absent */ }
    if (size < SPOOL_MAX) fs.appendFileSync(f, line + '\n');
  } catch { /* no directory: supervision never launched here */ }
}

function base() {
  if (process.env.SUP_URL) return process.env.SUP_URL;
  try {
    const port = Number(fs.readFileSync(path.join(stateDir(), 'server.port'), 'utf8').trim());
    return Number.isInteger(port) && port > 0 ? `http://127.0.0.1:${port}` : null;
  } catch { return null; }
}

let input = {};
try { input = JSON.parse(fs.readFileSync(0, 'utf8') || '{}'); } catch { /* unreadable input */ }
const event = String(input.hook_event_name || process.argv[2] || '');
if (!event) process.exit(0);
const body = JSON.stringify({
  agent, event, ts: Date.now() / 1000,
  tool: String(input.tool_name || ''),
  sub: String(input.agent_id || ''),              // set when the event comes from a subagent
  kind: String(input.notification_type || ''),    // Notification : permission_prompt, idle_prompt…
  session: String(input.session_id || ''),
  run: process.env.SUP_RUN || '',
});

const url = base();
if (!url) { spool(body); process.exit(0); }
let done = false;
const finish = (ok) => { if (done) return; done = true; if (!ok) spool(body); process.exit(0); };
setTimeout(() => finish(false), 500).unref();
try {
  const u = new URL('/api/hook', url);
  const req = http.request({ host: u.hostname, port: u.port, path: u.pathname, method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) } }, (res) => {
    res.resume();
    res.on('end', () => finish(true));  // the board answered (even a refusal): nothing to spool
  });
  req.on('error', () => finish(false));
  req.end(body);
} catch { finish(false); }
