#!/usr/bin/env bash
# relay-server-start.sh — Démarre le message broker Relay en arrière-plan.
# Idempotent : sans effet si le serveur est déjà en cours d'exécution.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="/tmp/relay-server.pid"
LOG_FILE="/tmp/relay-server.log"

# Vérifier si déjà en cours
if [[ -f "$PID_FILE" ]]; then
  PID=$(cat "$PID_FILE")
  if kill -0 "$PID" 2>/dev/null; then
    echo "[relay-server] Déjà en cours (pid $PID)" >&2
    exit 0
  else
    rm -f "$PID_FILE"
  fi
fi

# Démarrer en arrière-plan
nohup python3 "${SCRIPT_DIR}/relay-server.py" >> "$LOG_FILE" 2>&1 &
BGPID=$!

# Attendre que le serveur soit prêt (max 3s)
for i in 1 2 3 4 5 6; do
  sleep 0.5
  if curl -sf --max-time 0.3 "http://127.0.0.1:7432/status" > /dev/null 2>&1; then
    echo "[relay-server] Démarré (pid $BGPID)"
    exit 0
  fi
done

echo "[relay-server] Démarré (pid $BGPID) — délai de démarrage possible"
