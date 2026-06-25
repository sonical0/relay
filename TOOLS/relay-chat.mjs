import fs   from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname  = path.dirname(fileURLToPath(import.meta.url));
const relayRoot  = path.join(__dirname, '..');
const SERVER     = process.env.RELAY_SERVER ?? 'http://127.0.0.1:7432';
const PID_FILE   = '/tmp/relay-server.pid';

// Chat file : RELAY_CHAT_FILE env var ou ../RELAY_CHAT.md (relatif depuis TOOLS/)
const chatFile = process.env.RELAY_CHAT_FILE
  ? path.resolve(process.env.RELAY_CHAT_FILE)
  : path.join(relayRoot, 'RELAY_CHAT.md');

// ─── Server detection ────────────────────────────────────────────────────────

async function isServerRunning() {
  try {
    if (!fs.existsSync(PID_FILE)) return false;
    const res = await fetch(`${SERVER}/status`, { signal: AbortSignal.timeout(500) });
    return res.ok;
  } catch { return false; }
}

// ─── File fallback ───────────────────────────────────────────────────────────

function ensureChatFile() {
  fs.mkdirSync(path.dirname(chatFile), { recursive: true });
  if (!fs.existsSync(chatFile)) {
    fs.writeFileSync(chatFile,
      "# 💬 Relay Live Chat\n\n*Canal de communication temps réel entre les agents.*\n\n---\n\n",
      'utf8');
  }
}

function fileAppend(sender, to, message) {
  ensureChatFile();
  const ts    = new Date().toISOString().replace('T', ' ').substring(11, 19);
  const label = to && to !== 'broadcast' ? `${sender} → ${to}` : sender;
  fs.appendFileSync(chatFile, `* **[${ts}] ${label}** : ${message}\n`, 'utf8');
}

function fileRead(limit) {
  ensureChatFile();
  const lines = fs.readFileSync(chatFile, 'utf8').split('\n').filter(l => l.startsWith('* **'));
  return lines.slice(-limit).join('\n');
}

// ─── Arg parsing ─────────────────────────────────────────────────────────────

const args    = process.argv.slice(2);
const command = args[0];

if (!command || command === 'help') {
  console.log(`
Relay Live Chat CLI
Usage:
  node TOOLS/relay-chat.mjs send <sender> [--to <recipient>] "<message>"
  node TOOLS/relay-chat.mjs poll <agent>  [--since <ISO>] [--limit N]
  node TOOLS/relay-chat.mjs read [limit]
  node TOOLS/relay-chat.mjs status
  node TOOLS/relay-chat.mjs clear

Environment:
  RELAY_SERVER      HTTP server URL (default: http://127.0.0.1:7432)
  RELAY_CHAT_FILE   Path to chat Markdown file (default: ../RELAY_CHAT.md)
  `);
  process.exit(0);
}

// ─── Commands ────────────────────────────────────────────────────────────────

if (command === 'send') {
  const sender = args[1] || 'Unknown';
  let to = 'broadcast';
  let rest = args.slice(2);
  const toIdx = rest.indexOf('--to');
  if (toIdx !== -1 && rest[toIdx + 1]) { to = rest[toIdx + 1]; rest = rest.filter((_, i) => i !== toIdx && i !== toIdx + 1); }
  const message = rest.join(' ');
  if (!message) { console.error('Erreur : message vide.'); process.exit(1); }

  if (await isServerRunning()) {
    const res  = await fetch(`${SERVER}/send`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ from: sender, to, type: 'chat', content: message }) });
    const data = await res.json();
    console.log(`[server] Message envoyé par ${sender} → ${to} (id: ${data.id})`);
  } else {
    fileAppend(sender, to, message);
    console.log(`[file] Message envoyé par ${sender}`);
  }

} else if (command === 'poll') {
  const agent = args[1];
  if (!agent) { console.error('Usage: relay-chat.mjs poll <agent>'); process.exit(1); }
  let since = null, limit = 10;
  for (let i = 2; i < args.length; i++) {
    if (args[i] === '--since' && args[i + 1]) since = args[++i];
    if (args[i] === '--limit' && args[i + 1]) limit = parseInt(args[++i], 10);
  }
  if (!await isServerRunning()) { console.log(JSON.stringify([])); process.exit(0); }
  let url = `${SERVER}/poll?for=${encodeURIComponent(agent)}&limit=${limit}`;
  if (since) url += `&since=${encodeURIComponent(since)}`;
  const data = await (await fetch(url, { signal: AbortSignal.timeout(3000) })).json();
  console.log(JSON.stringify(data.messages, null, 2));

} else if (command === 'read') {
  const limit = parseInt(args[1], 10) || 10;
  if (await isServerRunning()) {
    const data  = await (await fetch(`${SERVER}/history?limit=${limit}`, { signal: AbortSignal.timeout(2000) })).json();
    const lines = data.messages.map(m => {
      const ts    = m.timestamp.substring(11, 19);
      const label = m.to && m.to !== 'broadcast' ? `${m.from} → ${m.to}` : m.from;
      return `* **[${ts}] ${label}** : ${m.content}`;
    });
    console.log(lines.join('\n'));
  } else {
    console.log(fileRead(limit));
  }

} else if (command === 'status') {
  if (!await isServerRunning()) { console.log('[relay-server] Inactif. Démarrer : bash TOOLS/relay-server-start.sh'); process.exit(0); }
  const data = await (await fetch(`${SERVER}/status`, { signal: AbortSignal.timeout(1000) })).json();
  const queues = Object.entries(data.queue_sizes || {}).map(([k, v]) => `  ${k}: ${v} message(s)`).join('\n') || '  (aucune file active)';
  console.log(`[relay-server] Actif — uptime ${data.uptime_s}s\n${queues}`);

} else if (command === 'clear') {
  ensureChatFile();
  fs.writeFileSync(chatFile, "# 💬 Relay Live Chat\n\n*Canal de communication temps réel entre les agents.*\n\n---\n\n", 'utf8');
  if (await isServerRunning()) await fetch(`${SERVER}/clear?older_than_hours=0`, { method: 'DELETE' }).catch(() => {});
  console.log('Chat réinitialisé.');

} else {
  console.error(`Commande inconnue : ${command}`);
  process.exit(1);
}
