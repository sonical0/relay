"""SQLite database of the supervision: the ONLY access layer to a project's persistent state.

    import store
    st = store.Store.open(run_dir)        # <state root>/<project>/supervision.db; migrates state.json if needed
    s = st.load()                         # dictionary in the historical state.json shape (a copy)
    st.save(s, actor="Manager B")         # writes what changed, one `events` row per change
    st.backup(dest)                       # consistent copy (sqlite3 .backup API)

Principles ("why SQLite"):
  - a single writer, the server; one connection, guarded by a lock; short transactions; WAL mode;
  - `events` is APPEND-ONLY (triggers refuse UPDATE and DELETE): log (`kind = 'log'`, what the board shows) and
    state changes (`kind = 'change'`: who, what, before -> after, timestamped);
  - `/api/state` keeps its shape: load() returns the same dictionary as the old state.json, with `events` = the
    last 300 log rows (since the last reset); the database itself keeps everything;
  - queryable columns + a `data` column (JSON of the whole object): the server logic does not change.
The dictionary returned by load() is a copy: changing it only reaches the database on the next save().
Private keys set by the server and removed by save(): `_pending` (log rows to append, in chronological order)
and `_reset` (reset: the log view restarts from there, the database keeps what came before).
All file writes go through config.guard_write (test mode: test roots only).
"""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

import config

DB_NAME = "supervision.db"
SCHEMA_VERSION = 1
EVENTS_VIEW = 300  # log rows returned in state["events"] (the database keeps everything)
TABLE_KEYS = ("managers", "tasks", "requests", "events", "sessions", "merge_queue")  # the rest goes to `meta`
SILENT = {"updated", "t", "history"}  # fields that move without being a state change (no events row)
SILENT_META = {"updated", "quota", "mailbox", "mail_seq", "pointers", "receipts"}  # quota: samples in its own
# table; mailbox, mail_seq, pointers: ephemeral messages and their pointers; receipts: receipts of idempotent commands

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS managers (id TEXT PRIMARY KEY, status TEXT, role TEXT, branch TEXT, task TEXT,
    progress INTEGER, updated INTEGER, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workers (id TEXT PRIMARY KEY, manager TEXT NOT NULL, status TEXT, model TEXT, task TEXT,
    verified INTEGER, updated INTEGER, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, manager TEXT, status TEXT, title TEXT, updated INTEGER,
    data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS merge_queue (pos INTEGER PRIMARY KEY, branch TEXT NOT NULL, sha TEXT, manager TEXT,
    status TEXT, t INTEGER, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, sender TEXT, status TEXT, what TEXT, t INTEGER,
    data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files (manager TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('declared', 'real')),
    path TEXT NOT NULL, pos INTEGER NOT NULL, since INTEGER, PRIMARY KEY (manager, kind, path));
CREATE TABLE IF NOT EXISTS sessions (sid TEXT PRIMARY KEY, who TEXT, model TEXT, ctx REAL, cost REAL,
    first_t INTEGER, t INTEGER, active INTEGER NOT NULL DEFAULT 1, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quota (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, used_percentage REAL,
    resets_at INTEGER, t INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, t INTEGER NOT NULL, who TEXT,
    kind TEXT NOT NULL, subject TEXT, msg TEXT, level TEXT, before TEXT, after TEXT);
CREATE INDEX IF NOT EXISTS events_t ON events (t);
CREATE INDEX IF NOT EXISTS events_kind ON events (kind, subject);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
    BEGIN SELECT RAISE(ABORT, 'events: append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
    BEGIN SELECT RAISE(ABORT, 'events: append-only'); END;
CREATE TABLE IF NOT EXISTS waves (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, plan TEXT,
    start_t INTEGER NOT NULL, end_t INTEGER);
CREATE TABLE IF NOT EXISTS days (day TEXT PRIMARY KEY, first_t INTEGER NOT NULL, last_t INTEGER NOT NULL);
"""


def now():
    return int(time.time())


def _j(v):
    return json.dumps(v, ensure_ascii=False, sort_keys=False)


def _same(a, b):
    return json.dumps(a, ensure_ascii=False, sort_keys=True) == json.dumps(b, ensure_ascii=False, sort_keys=True)


def _clone(v):
    return json.loads(json.dumps(v, ensure_ascii=False))


def empty_state():
    return {"project": "", "updated": now(), "managers": {}, "tasks": {}, "requests": {},
            "events": [], "sessions": {}, "quota": {}, "merge_queue": [], "braked": False}


def connect(path, readonly=False):
    """Connection tuned for the supervision (WAL, NORMAL, 5 s lock wait). readonly: read-only."""
    if readonly:
        c = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True, timeout=5,
                            check_same_thread=False, isolation_level=None)
    else:
        c = sqlite3.connect(str(config.guard_write(path)), timeout=5, check_same_thread=False, isolation_level=None)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA busy_timeout=5000")
    return c


def _mtime(p):
    return max((Path(str(p) + s).stat().st_mtime for s in ("", "-wal") if Path(str(p) + s).exists()), default=0)


class Store:
    """One database, one connection, one lock. All methods are thread-safe."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._conn = connect(self.path)
        self._conn.executescript(SCHEMA)  # CREATE … IF NOT EXISTS: idempotent
        if self._meta_get("sys.schema") is None:
            self._conn.execute("INSERT OR REPLACE INTO meta VALUES ('sys.schema', ?)", (_j(SCHEMA_VERSION),))
        self._state = self._read_state()

    # --- open and migration -----------------------------------------------------------------------------

    @classmethod
    def open(cls, run_dir, log=print):
        """Open `<run_dir>/supervision.db`; on first start, import `state.json` then rename it to .bak.

        Idempotent: database present -> no import. Database AND state.json present:
          - state.json newer than the database (rollback in progress?): refused, with a clear message;
          - otherwise (stopped between import and rename): state.json renamed to .bak, the database wins.
        """
        run_dir = Path(run_dir)
        db, src = run_dir / DB_NAME, run_dir / "state.json"
        config.guard_write(run_dir).mkdir(parents=True, exist_ok=True)
        if db.exists() and src.exists():
            if src.stat().st_mtime > _mtime(db) + 1:
                raise RuntimeError(f"{src} is newer than {db}: rollback in progress? To re-import "
                                   f"state.json, first rename {DB_NAME} (and its -wal/-shm files).")
            log(f"Migration: {src.name} already imported, renamed to {_bak(src).name}")
            _rename_bak(src)
        elif not db.exists() and src.exists():
            n = migrate(src, db)
            log(f"Migration: {src.name} -> {DB_NAME} ({n}); old file kept as {src.name}.bak")
        return cls(db)

    def close(self):
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # --- reading ------------------------------------------------------------------------------------------

    def load(self):
        """Copy of the state, in the old state.json shape."""
        with self._lock:
            return _clone(self._state)

    def _meta_get(self, key, default=None):
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _read_state(self):
        c = self._conn
        s = empty_state()
        for k, v in c.execute("SELECT key, value FROM meta WHERE key LIKE 'state.%' ORDER BY rowid"):
            s[k[6:]] = json.loads(v)
        files = {}
        for mid, kind, path in c.execute("SELECT manager, kind, path FROM files ORDER BY manager, kind, pos"):
            files.setdefault((mid, kind), []).append(path)
        s["managers"] = {}
        for mid, data in c.execute("SELECT id, data FROM managers ORDER BY rowid"):
            m = json.loads(data)
            m["files"] = files.get((mid, "declared"), [])
            m["real_files"] = files.get((mid, "real"), [])
            m["workers"] = {}
            s["managers"][mid] = m
        for wid, mid, data in c.execute("SELECT id, manager, data FROM workers ORDER BY rowid"):
            if mid in s["managers"]:
                s["managers"][mid]["workers"][wid] = json.loads(data)
        s["tasks"] = {i: json.loads(d) for i, d in c.execute("SELECT id, data FROM tasks ORDER BY rowid")}
        s["requests"] = {i: json.loads(d) for i, d in c.execute("SELECT id, data FROM requests ORDER BY rowid")}
        s["merge_queue"] = [json.loads(d) for (d,) in c.execute("SELECT data FROM merge_queue ORDER BY pos")]
        s["sessions"] = {i: json.loads(d) for i, d in c.execute("SELECT sid, data FROM sessions WHERE active = 1 ORDER BY rowid")}
        s["events"] = self.recent_events()
        return s

    def recent_events(self, limit=EVENTS_VIEW):
        """Displayed log (`log` rows), newest first, since the last reset."""
        since = self._meta_get("sys.events_since", 0)
        rows = self._conn.execute("SELECT t, who, msg, level FROM events WHERE kind = 'log' AND id > ? "
                                  "ORDER BY id DESC LIMIT ?", (since, limit))
        return [{"t": t, "who": who, "msg": msg, "level": level} for t, who, msg, level in rows]

    def events(self, kind=None, since_t=0, limit=None):
        """All events rows (metrics, report), oldest first."""
        q = "SELECT id, t, who, kind, subject, msg, level, before, after FROM events WHERE t >= ?"
        args = [since_t]
        if kind:
            q += " AND kind = ?"
            args.append(kind)
        q += " ORDER BY id" + (" LIMIT ?" if limit else "")
        args += [limit] if limit else []
        cols = ("id", "t", "who", "kind", "subject", "msg", "level", "before", "after")
        with self._lock:
            return [dict(zip(cols, r)) for r in self._conn.execute(q, args)]

    def query(self, sql, args=()):
        """Free read (tests, metrics)."""
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    # --- writing -----------------------------------------------------------------------------------------

    class _Tx:
        def __init__(self, conn):
            self.c = conn

        def __enter__(self):
            self.c.execute("BEGIN IMMEDIATE")
            return self.c

        def __exit__(self, et, ev, tb):
            self.c.execute("ROLLBACK" if et else "COMMIT")
            return False

    def _tx(self):
        return Store._Tx(self._conn)

    def append_event(self, who, msg, level="info", kind="log", subject=None, before=None, after=None, t=None):
        """Append a row to `events` outside a save() (e.g. migration, backup)."""
        with self._lock, self._tx() as c:
            _insert_event(c, t or now(), who, kind, subject, msg, level, before, after)

    def save(self, state, actor="", changes=True):
        """Write what differs from the last version, in ONE transaction; `changes`: `change` rows in events.

        `actor`: who caused the write (`who` column of the change rows).
        """
        pending = state.pop("_pending", [])
        reset = state.pop("_reset", False)
        new = _clone(state)
        t = now()
        with self._lock:
            old = self._state
            with self._tx() as c:
                ev = _Changes(c, t, actor or "serveur", changes)
                self._write_meta(c, old, new, ev)
                self._write_managers(c, old["managers"], new["managers"], ev, t)
                for table, key in (("tasks", "tasks"), ("requests", "requests")):
                    self._write_dict(c, table, old[key], new[key], ev)
                self._write_merge(c, old["merge_queue"], new["merge_queue"], ev)
                self._write_sessions(c, old["sessions"], new["sessions"], t)
                if reset:
                    last = c.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
                    c.execute("INSERT OR REPLACE INTO meta VALUES ('sys.events_since', ?)", (_j(last),))
                for e in pending:
                    _insert_event(c, e.get("t") or t, e.get("who"), "log", None, e.get("msg"), e.get("level", "info"))
                day = time.strftime("%Y-%m-%d", time.localtime(t))
                c.execute("INSERT INTO days VALUES (?, ?, ?) ON CONFLICT(day) DO UPDATE SET last_t = excluded.last_t",
                          (day, t, t))
            new["events"] = self.recent_events() if (pending or reset) else old["events"]
            self._state = new
            state["events"] = _clone(new["events"])

    def _write_meta(self, c, old, new, ev):
        okeys = {k for k in old if k not in TABLE_KEYS}
        nkeys = {k for k in new if k not in TABLE_KEYS}
        for k in sorted(okeys - nkeys):
            c.execute("DELETE FROM meta WHERE key = ?", ("state." + k,))
            if k not in SILENT_META:
                ev.add(k, old[k], None)
        for k in [k for k in new if k in nkeys]:
            if k in okeys and _same(old[k], new[k]):
                continue
            c.execute("INSERT INTO meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                      ("state." + k, _j(new[k])))
            if k == "quota":
                _quota_samples(c, old.get("quota") or {}, new["quota"] or {})
            if k == "plan" and new[k]:  # new wave: the previous one ends
                c.execute("UPDATE waves SET end_t = ? WHERE end_t IS NULL", (ev.t,))
                c.execute("INSERT INTO waves (name, plan, start_t) VALUES (?, ?, ?)",
                          (Path(str(new[k])).stem, new[k], ev.t))
            if k not in SILENT_META:
                ev.add(k, old.get(k), new[k])

    def _write_managers(self, c, old, new, ev, t):
        for mid in [m for m in old if m not in new]:
            c.execute("DELETE FROM managers WHERE id = ?", (mid,))
            c.execute("DELETE FROM workers WHERE manager = ?", (mid,))
            c.execute("DELETE FROM files WHERE manager = ?", (mid,))
            ev.add(f"manager:{mid}", _card(old[mid]), None)
        for mid, m in new.items():
            o = old.get(mid)
            card = _card(m)
            if o is None or not _same(_card(o), card):
                c.execute("INSERT INTO managers VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                          "status = excluded.status, role = excluded.role, branch = excluded.branch, task = excluded.task, "
                          "progress = excluded.progress, updated = excluded.updated, data = excluded.data",
                          (mid, m.get("status"), m.get("role"), m.get("branch"), m.get("task"), m.get("progress"),
                           m.get("updated"), _j(card)))
                if o is None:
                    ev.add(f"manager:{mid}", None, _brief(card))
                else:
                    ev.fields(f"manager:{mid}", _card(o), card)
            ow = (o or {}).get("workers", {})
            for wid in [w for w in ow if w not in m.get("workers", {})]:
                c.execute("DELETE FROM workers WHERE id = ?", (wid,))
                ev.add(f"worker:{wid}", ow[wid], None)
            for wid, w in m.get("workers", {}).items():
                if wid in ow and _same(ow[wid], w):
                    continue
                c.execute("INSERT INTO workers VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                          "manager = excluded.manager, status = excluded.status, model = excluded.model, "
                          "task = excluded.task, verified = excluded.verified, updated = excluded.updated, data = excluded.data",
                          (wid, mid, w.get("status"), w.get("model"), w.get("task"), int(bool(w.get("verified"))),
                           w.get("updated"), _j(w)))
                if wid in ow:
                    ev.fields(f"worker:{wid}", ow[wid], w)
                else:
                    ev.add(f"worker:{wid}", None, w)
            for kind, key in (("declared", "files"), ("real", "real_files")):
                before, after = list((o or {}).get(key, [])), list(m.get(key, []))
                if before == after:
                    continue
                since = dict(c.execute("SELECT path, since FROM files WHERE manager = ? AND kind = ?", (mid, kind)))
                c.execute("DELETE FROM files WHERE manager = ? AND kind = ?", (mid, kind))
                c.executemany("INSERT OR IGNORE INTO files VALUES (?, ?, ?, ?, ?)",
                              [(mid, kind, p, i, since.get(p, t)) for i, p in enumerate(after)])
                gone, added = sorted(set(before) - set(after)), sorted(set(after) - set(before))
                if gone or added:
                    ev.add(f"files:{mid}.{kind}", gone or None, added or None, f"+{len(added)} -{len(gone)}")

    def _write_dict(self, c, table, old, new, ev):
        single = table[:-1]
        # queryable columns <- object keys (`from` is an SQL reserved word: column `sender`)
        cols = {"tasks": {"manager": "manager", "status": "status", "title": "title", "updated": "updated"},
                "requests": {"sender": "from", "status": "status", "what": "what", "t": "t"}}[table]
        for k in [k for k in old if k not in new]:
            c.execute(f"DELETE FROM {table} WHERE id = ?", (k,))
            ev.add(f"{single}:{k}", old[k], None)
        for k, v in new.items():
            if k in old and _same(old[k], v):
                continue
            c.execute(f"INSERT INTO {table} (id, {', '.join(cols)}, data) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                      + ", ".join(f"{n} = excluded.{n}" for n in (*cols, "data")),
                      (k, *(v.get(x) for x in cols.values()), _j(v)))
            if k in old:
                ev.fields(f"{single}:{k}", old[k], v)
            else:
                ev.add(f"{single}:{k}", None, v)

    def _write_merge(self, c, old, new, ev):
        if _same(old, new):
            return
        c.execute("DELETE FROM merge_queue")
        c.executemany("INSERT INTO merge_queue VALUES (?, ?, ?, ?, ?, ?, ?)",
                      [(i, x.get("branch", ""), x.get("sha"), x.get("manager"), x.get("status"), x.get("t"), _j(x))
                       for i, x in enumerate(new, 1)])
        ob = {x.get("branch"): (i, x) for i, x in enumerate(old, 1)}
        nb = {x.get("branch"): (i, x) for i, x in enumerate(new, 1)}
        for b in [b for b in ob if b not in nb]:
            ev.add(f"merge:{b}", ob[b][1], None)
        for b, (i, x) in nb.items():
            if b not in ob:
                ev.add(f"merge:{b}", None, {**x, "pos": i})
                continue
            j, y = ob[b]
            if i != j:
                ev.add(f"merge:{b}.pos", j, i)
            ev.fields(f"merge:{b}", y, x)

    def _write_sessions(self, c, old, new, t):
        for sid in [s for s in old if s not in new]:  # left the view (6 h without news): the database keeps it
            c.execute("UPDATE sessions SET active = 0 WHERE sid = ?", (sid,))
        for sid, v in new.items():
            if sid in old and _same(old[sid], v):
                continue
            c.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?) ON CONFLICT(sid) DO UPDATE SET "
                      "who = excluded.who, model = excluded.model, ctx = excluded.ctx, cost = excluded.cost, "
                      "t = excluded.t, active = 1, data = excluded.data",
                      (sid, v.get("who"), v.get("model"), v.get("ctx"), v.get("cost"), v.get("t") or t,
                       v.get("t") or t, _j(v)))

    # --- backup ------------------------------------------------------------------------------------------

    def backup(self, dest):
        """Consistent copy of the database to `dest` (.backup API: safe during writes, unlike a file copy)."""
        with self._lock:
            return backup_conn(self._conn, dest)


def backup_conn(conn, dest):
    dest = config.guard_write(Path(dest))
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.guard_write(dest.with_name(dest.name + ".tmp"))
    tmp.unlink(missing_ok=True)
    out = sqlite3.connect(str(tmp))
    try:
        conn.backup(out)
        ok = out.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        out.close()
    if ok != "ok":
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"invalid backup ({ok})")
    os.replace(tmp, dest)
    return dest


def backup_file(db, dest):
    """Backup from the database file, READ-ONLY (sup.py backup: server running or not)."""
    if not Path(db).exists():
        raise FileNotFoundError(f"database missing: {db} (has the server not created it yet?)")
    conn = connect(db, readonly=True)
    try:
        return backup_conn(conn, dest)
    finally:
        conn.close()


def backup_path(project, day=None):
    """`<SUP_BACKUP_DIR>/<YYYY-MM-DD>/supervision-<project>.db`."""
    return config.backup_dir() / (day or time.strftime("%Y-%m-%d")) / f"supervision-{project}.db"


# --- helpers -------------------------------------------------------------------------------------------------

def _card(m):
    """A manager's card as stored in managers.data (workers and files have their own tables)."""
    return {k: v for k, v in m.items() if k not in ("workers", "files", "real_files")}


def _brief(card):
    return {k: v for k, v in card.items() if k not in SILENT}


def _insert_event(c, t, who, kind, subject, msg, level, before=None, after=None):
    c.execute("INSERT INTO events (t, who, kind, subject, msg, level, before, after) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
              (int(t), who, kind, subject, msg, level,
               None if before is None else _j(before), None if after is None else _j(after)))


class _Changes:
    """`change` rows of events: one per modified field (before -> after)."""

    def __init__(self, c, t, who, on):
        self.c, self.t, self.who, self.on = c, t, who, on

    def add(self, subject, before, after, msg=None):
        if self.on:
            _insert_event(self.c, self.t, self.who, "change", subject, msg, "info", before, after)

    def fields(self, subject, old, new):
        for k in [*old, *(k for k in new if k not in old)]:
            if k in SILENT or _same(old.get(k), new.get(k)):
                continue
            self.add(f"{subject}.{k}", old.get(k), new.get(k))


def _quota_samples(c, old, new):
    for k, v in new.items():
        o = old.get(k) or {}
        if (o.get("used_percentage"), o.get("resets_at")) != (v.get("used_percentage"), v.get("resets_at")):
            c.execute("INSERT INTO quota (kind, used_percentage, resets_at, t) VALUES (?, ?, ?, ?)",
                      (k, v.get("used_percentage"), v.get("resets_at"), v.get("t") or now()))


def _bak(src):
    bak = src.with_name(src.name + ".bak")
    return bak if not bak.exists() else src.with_name(f"{src.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")


def _rename_bak(src):
    dst = config.guard_write(_bak(src))
    config.guard_write(src)
    os.replace(src, dst)  # never deleted: the old state stays readable
    return dst


def migrate(src, db):
    """Import state.json into a new database (temporary file then swap), then rename state.json to .bak.

    Returns a summary. Log rows are imported as is (same view as before); no "change" is invented: a single
    `migration` row says where the state came from.
    """
    src, db = Path(src), Path(db)
    state = json.loads(src.read_text(encoding="utf-8"))
    for k, v in empty_state().items():
        state.setdefault(k, v)
    tmp = config.guard_write(db.with_name(db.name + ".import"))
    for s in ("", "-wal", "-shm"):
        config.guard_write(Path(str(tmp) + s)).unlink(missing_ok=True)  # leftover of an interrupted import
    summary = f"{len(state['managers'])} managers, {len(state['tasks'])} tasks, {len(state['events'])} events"
    st = Store(tmp)
    try:
        state["_pending"] = list(reversed(state.get("events") or []))  # chronological order
        st.save(state, actor="migration", changes=False)  # `plan` present: opens the current wave
        st.append_event("migration", f"Import of {src.name}: {summary}", kind="migration", subject=str(src))
    finally:
        st.close()  # closes and folds the WAL into the main file
    config.guard_write(db)
    os.replace(tmp, db)
    _rename_bak(src)
    return summary
