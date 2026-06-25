#!/usr/bin/env python3
"""Relay Message Broker — stdlib pur, Python 3.8+, port 7432.

Usage:
  python3 TOOLS/relay-server.py [--port N] [--chat PATH] [--db PATH]

Options:
  --port N      Port d'écoute (défaut : 7432)
  --chat PATH   Fichier Live-Chat Markdown (défaut : ../RELAY_CHAT.md)
  --db PATH     Base SQLite (défaut : ./relay-messages.db dans TOOLS/)
"""

import http.server
import json
import os
import queue
import signal
import sqlite3
import sys
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs

PORT     = 7432
PID_FILE = '/tmp/relay-server.pid'

DB_SCHEMA = '''
CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    from_agent  TEXT NOT NULL,
    to_agent    TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT 'chat',
    content     TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    session_id  TEXT DEFAULT '',
    read        INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_poll ON messages (to_agent, read, timestamp);
'''


class RelayBroker:
    def __init__(self, db_path, chat_path):
        self.db_path   = db_path
        self.chat_path = chat_path
        self.db_lock   = threading.Lock()
        self.queues    = defaultdict(queue.Queue)
        self.start_time = time.time()
        self._init_db()
        self._reload_unread()
        self._start_purge_thread()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self.db_lock:
            conn = self._connect()
            conn.executescript(DB_SCHEMA)
            conn.commit(); conn.close()

    def _reload_unread(self):
        with self.db_lock:
            conn = self._connect()
            rows = conn.execute('SELECT * FROM messages WHERE read=0 ORDER BY timestamp').fetchall()
            conn.close()
        for row in rows:
            self.queues[row['to_agent']].put(self._row_to_dict(row))

    def _row_to_dict(self, row):
        return {
            'id':         row['id'],
            'from':       row['from_agent'],
            'to':         row['to_agent'],
            'type':       row['type'],
            'content':    row['content'],
            'timestamp':  row['timestamp'],
            'session_id': row['session_id'] or '',
            'read':       bool(row['read']),
        }

    def post_message(self, from_agent, to_agent, type_, content, session_id=''):
        msg_id = str(uuid.uuid4())
        ts = datetime.now(timezone.utc).isoformat()
        with self.db_lock:
            conn = self._connect()
            conn.execute(
                'INSERT INTO messages (id,from_agent,to_agent,type,content,timestamp,session_id,read) VALUES (?,?,?,?,?,?,?,0)',
                (msg_id, from_agent, to_agent, type_, content, ts, session_id)
            )
            conn.commit(); conn.close()
        msg = {'id': msg_id, 'from': from_agent, 'to': to_agent, 'type': type_,
               'content': content, 'timestamp': ts, 'session_id': session_id, 'read': False}
        if to_agent == 'broadcast':
            for agent in list(self.queues.keys()) or ['claude', 'agy']:
                self.queues[agent].put(dict(msg))
        else:
            self.queues[to_agent].put(msg)
        self._append_chat(msg)
        return msg_id

    def poll(self, for_agent, since=None, limit=10):
        results = []
        while len(results) < limit:
            try:
                msg = self.queues[for_agent].get_nowait()
                if since and msg['timestamp'] <= since:
                    continue
                results.append(msg)
            except queue.Empty:
                break
        if results:
            ids = [m['id'] for m in results]
            with self.db_lock:
                conn = self._connect()
                conn.execute(f'UPDATE messages SET read=1 WHERE id IN ({",".join("?"*len(ids))})', ids)
                conn.commit(); conn.close()
        return results

    def history(self, limit=50):
        with self.db_lock:
            conn = self._connect()
            rows = conn.execute('SELECT * FROM messages ORDER BY timestamp DESC LIMIT ?', (limit,)).fetchall()
            conn.close()
        return [self._row_to_dict(r) for r in reversed(rows)]

    def status(self):
        return {
            'ok': True, 'version': '1.0.0',
            'uptime_s': int(time.time() - self.start_time),
            'queue_sizes': {k: v.qsize() for k, v in self.queues.items()},
        }

    def purge_old(self, hours=24):
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self.db_lock:
            conn = self._connect()
            conn.execute('DELETE FROM messages WHERE timestamp < ? AND read=1', (cutoff,))
            n = conn.total_changes; conn.commit(); conn.close()
        return n

    def _append_chat(self, msg):
        try:
            ts = datetime.fromisoformat(msg['timestamp']).strftime('%H:%M:%S')
        except Exception:
            ts = msg['timestamp'][11:19]
        to_ = msg['to']
        line = (f"* **[{ts}] {msg['from']} → {to_}** : {msg['content']}\n"
                if to_ not in ('broadcast', '') else
                f"* **[{ts}] {msg['from']}** : {msg['content']}\n")
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.chat_path)), exist_ok=True)
            with open(self.chat_path, 'a', encoding='utf-8') as f:
                f.write(line)
        except Exception as e:
            print(f'[relay-server] Warning: could not write to chat file: {e}', file=sys.stderr)

    def _start_purge_thread(self):
        def loop():
            while True:
                time.sleep(600)
                n = self.purge_old(24)
                if n: print(f'[relay-server] Purged {n} old messages', flush=True)
        threading.Thread(target=loop, daemon=True).start()


class RelayHandler(http.server.BaseHTTPRequestHandler):
    broker = None

    def log_message(self, *args): pass

    def _send_json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers(); self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        if not length: return {}
        try: return json.loads(self.rfile.read(length))
        except json.JSONDecodeError: return {}

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path == '/status':
            self._send_json(200, self.broker.status())
        elif parsed.path == '/poll':
            agent = qs.get('for', [''])[0]
            if not agent: self._send_json(400, {'error': 'Missing ?for=<agent>'}); return
            msgs = self.broker.poll(agent, qs.get('since', [None])[0], int(qs.get('limit', ['10'])[0]))
            self._send_json(200, {'messages': msgs, 'count': len(msgs)})
        elif parsed.path == '/history':
            self._send_json(200, {'messages': self.broker.history(int(qs.get('limit', ['50'])[0]))})
        else:
            self._send_json(404, {'error': 'Not found'})

    def do_POST(self):
        parsed = urlparse(self.path)
        body = self._read_body()
        if parsed.path == '/send':
            content = body.get('content', '')
            if not content: self._send_json(400, {'error': 'content is required'}); return
            msg_id = self.broker.post_message(
                body.get('from', 'unknown'), body.get('to', 'broadcast'),
                body.get('type', 'chat'), content, body.get('session_id', ''))
            self._send_json(200, {'ok': True, 'id': msg_id})
        else:
            self._send_json(404, {'error': 'Not found'})

    def do_DELETE(self):
        parsed = urlparse(self.path)
        if parsed.path == '/clear':
            hours = int(parse_qs(parsed.query).get('older_than_hours', ['24'])[0])
            self._send_json(200, {'ok': True, 'deleted': self.broker.purge_old(hours)})
        else:
            self._send_json(404, {'error': 'Not found'})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.end_headers()


def main():
    import argparse
    here = os.path.dirname(os.path.abspath(__file__))
    relay_root = os.path.join(here, '..')

    parser = argparse.ArgumentParser(description='Relay Message Broker')
    parser.add_argument('--db',   default=os.path.join(here, 'relay-messages.db'))
    parser.add_argument('--chat', default=os.path.join(relay_root, 'RELAY_CHAT.md'))
    parser.add_argument('--port', type=int, default=PORT)
    args = parser.parse_args()

    broker = RelayBroker(os.path.abspath(args.db), os.path.abspath(args.chat))
    RelayHandler.broker = broker

    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

    def shutdown(sig=None, frame=None):
        try: os.unlink(PID_FILE)
        except FileNotFoundError: pass
        print('\n[relay-server] Stopped.', flush=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT,  shutdown)

    server = http.server.HTTPServer(('127.0.0.1', args.port), RelayHandler)
    print(f'[relay-server] Listening on 127.0.0.1:{args.port} | DB: {args.db}', flush=True)
    try: server.serve_forever()
    finally: shutdown()


if __name__ == '__main__':
    main()
