#!/usr/bin/env bash
# relay-agent-loop.sh — Listener antigravity/agy pour le framework Relay.
# À lancer dans un pane tmux dédié (fait automatiquement par relay-session-hook.js sur Linux).
# Poll le serveur toutes les 2s, dispatch les tasks à agy, renvoie les résultats.
set -euo pipefail

RELAY_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVER="http://127.0.0.1:7432"
POLL_INTERVAL=2
TASK_TIMEOUT=300   # 5 min max par task
AGY_BIN="${AGY_BIN:-agy}"  # Surcharger avec GEMINI_BIN=gemini si nécessaire

log() { echo "[$(date +%H:%M:%S)] [relay-loop] $*"; }

# ─── JSON helpers ─────────────────────────────────────────────────────────────

extract_message() {
  local resp_file
  resp_file=$(mktemp)
  printf '%s' "$1" > "$resp_file"
  python3 -c "
import json, sys
data = json.loads(open(sys.argv[1]).read())
msgs = data.get('messages', [])
if not msgs: sys.exit(1)
m = msgs[0]
task_file = '/tmp/relay-task-' + m['id'] + '.md'
open(task_file, 'w').write(m.get('content', ''))
print('MSG_ID='      + m['id'])
print('MSG_TYPE='    + m.get('type', 'chat'))
print('MSG_FROM='    + m.get('from', 'unknown'))
print('MSG_SESSION=' + m.get('session_id', ''))
print('TASK_FILE='   + task_file)
" "$resp_file"
  local ret=$?; rm -f "$resp_file"; return $ret
}

send_result() {
  local from_agent="$1" to="$2" session_id="$3" content_file="$4"
  RELAY_FROM="$from_agent" RELAY_TO="$to" RELAY_SESSION="$session_id" RELAY_FILE="$content_file" \
  python3 -c "
import urllib.request, json, os
payload = json.dumps({
    'from': os.environ['RELAY_FROM'], 'to': os.environ['RELAY_TO'],
    'type': 'result', 'session_id': os.environ['RELAY_SESSION'],
    'content': open(os.environ['RELAY_FILE']).read()[:8000],
}).encode()
req = urllib.request.Request('http://127.0.0.1:7432/send', data=payload,
      headers={'Content-Type':'application/json'}, method='POST')
urllib.request.urlopen(req, timeout=5); print('ok')
"
}

send_ack() {
  RELAY_TO="$1" RELAY_SESSION="$2" python3 -c "
import urllib.request, json, os
payload = json.dumps({'from':'agy','to':os.environ['RELAY_TO'],'type':'ack',
                      'session_id':os.environ['RELAY_SESSION'],'content':'pong'}).encode()
req = urllib.request.Request('http://127.0.0.1:7432/send', data=payload,
      headers={'Content-Type':'application/json'}, method='POST')
urllib.request.urlopen(req, timeout=3)
" 2>/dev/null || true
}

# ─── Main loop ───────────────────────────────────────────────────────────────

log "Relay agy loop démarré (poll toutes les ${POLL_INTERVAL}s)"
log "Serveur : $SERVER | agy : $(which "$AGY_BIN" 2>/dev/null || echo 'NOT FOUND')"

while true; do
  if ! curl -sf --max-time 1 "$SERVER/status" > /dev/null 2>&1; then
    log "Serveur non disponible, retry dans 10s..."
    sleep 10; continue
  fi

  RESPONSE=$(curl -sf --max-time 3 "$SERVER/poll?for=agy&limit=1" 2>/dev/null \
             || echo '{"messages":[],"count":0}')
  COUNT=$(echo "$RESPONSE" | python3 -c \
          "import json,sys; print(json.loads(sys.stdin.read()).get('count',0))" 2>/dev/null || echo 0)

  if [[ "$COUNT" -gt 0 ]]; then
    VARS=$(extract_message "$RESPONSE") || { log "Erreur parsing JSON"; sleep "$POLL_INTERVAL"; continue; }
    eval "$VARS"
    log "Message reçu [$MSG_TYPE] de $MSG_FROM"

    if [[ "$MSG_TYPE" == "task" ]]; then
      RESULT_FILE="/tmp/relay-result-${MSG_ID}.txt"
      log "Dispatching à $AGY_BIN → $TASK_FILE"
      if timeout "$TASK_TIMEOUT" "$AGY_BIN" -p "@${TASK_FILE}" > "$RESULT_FILE" 2>&1; then
        log "Terminé. Envoi résultat à $MSG_FROM..."
      else
        log "Erreur (exit $?). Envoi erreur à $MSG_FROM..."
        echo "[ERREUR agy exit $?]" >> "$RESULT_FILE"
      fi
      send_result "agy" "$MSG_FROM" "$MSG_SESSION" "$RESULT_FILE" \
        && log "Résultat envoyé." || log "Échec envoi résultat."
      rm -f "$TASK_FILE" "$RESULT_FILE"

    elif [[ "$MSG_TYPE" == "ping" ]]; then
      send_ack "$MSG_FROM" "$MSG_SESSION"
      log "Pong envoyé à $MSG_FROM"
      rm -f "/tmp/relay-task-${MSG_ID}.md" 2>/dev/null || true

    else
      log "Type '$MSG_TYPE' ignoré."
      rm -f "/tmp/relay-task-${MSG_ID}.md" 2>/dev/null || true
    fi
  fi

  sleep "$POLL_INTERVAL"
done
