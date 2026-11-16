"""Host load: size the supervision, brake, leave no orphans.

Typical failure on a 12-thread laptop: 100 % CPU for hours (several `npm ci` at once, multi-minute production builds,
orphan `npm ci` left after a cancelled task). Four tools:

  - `profile()`   : limits derived from cores and RAM (total workers, concurrent `npm ci`, lite mode suggested);
  - `Brake`       : hysteresis brake (CPU ≥ 90 % for 5 min, memory ≥ 90 % for 1 min);
  - `Slots`       : directory semaphore (one `npm ci` at a time under 8 threads, two above), queue visible on the board;
  - `orphans()`   : node/npm processes whose worktree no longer exists (offered to the end-of-day cleanup).

Everything is pure or injectable: no test touches real processes.
"""
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

SMALL_THREADS = 8        # below: a single npm ci at a time, lite mode suggested
SMALL_RAM_GB = 16
CPU_BRAKE, CPU_RELEASE, CPU_HOLD = 90, 75, 300      # %; seconds above the threshold before tightening
MEM_BRAKE, MEM_RELEASE, MEM_HOLD = 90, 80, 60
RELEASE_HOLD = 60        # seconds under the low threshold before releasing
SLOT_STALE_SECONDS = 3 * 3600   # an npm ci does not need three hours


def ram_gb():
    """Total physical memory in GB, None if the system does not report it."""
    try:
        import sysstats
        if os.name == "nt":
            return sysstats._memory()[1] / 2**30
        if os.path.exists("/proc/meminfo"):
            return sysstats._memory_posix()[1] / 2**30
    except Exception:  # noqa: BLE001 — a missing measure never blocks a launch
        pass
    return None


def profile(threads=None, ram=None, cfg=None):
    """Host load limits: {threads, ram_gb, tier, workers_total, npm_ci, lite_suggested, why}.

    The brain's config wins: `workers_total`, `npm_ci_max`; `host_limits: false` sets no limit
    (workers_total/npm_ci None). Without a RAM measure, only the thread rule applies.
    """
    cfg = cfg or {}
    threads = int(threads or os.cpu_count() or 4)
    ram = ram if ram is not None else ram_gb()
    small = threads < SMALL_THREADS or (ram is not None and ram < SMALL_RAM_GB)
    out = {"threads": threads, "ram_gb": None if ram is None else round(ram, 1), "tier": "small" if small else "normal",
           "workers_total": max(2, threads // 2), "npm_ci": 1 if threads < SMALL_THREADS else 2,
           "lite_suggested": small}
    if cfg.get("workers_total"):
        out["workers_total"] = int(cfg["workers_total"])
    if cfg.get("npm_ci_max"):
        out["npm_ci"] = int(cfg["npm_ci_max"])
    if cfg.get("host_limits") is False:
        out["workers_total"] = out["npm_ci"] = None
    out["why"] = (f"{threads} threads" + (f", {out['ram_gb']} GB" if out["ram_gb"] else "")
                  + (": modest host, lite mode suggested (--lite)" if small else ""))
    return out


class Brake:
    """Hysteresis brake: tightens when the value stays ≥ `high` for `hold` s, releases when it stays ≤ `low`
    for `release_hold` s. A missing measure (None) changes nothing. `update()` returns True if the state changes."""

    def __init__(self, high, low, hold, release_hold=RELEASE_HOLD):
        self.high, self.low, self.hold, self.release_hold = high, low, hold, release_hold
        self.on, self.value = False, None
        self._above = self._below = None

    def update(self, value, t=None):
        t = time.time() if t is None else t
        if value is None:
            return False
        self.value = value
        self._above = (self._above if self._above is not None else t) if value >= self.high else None
        self._below = (self._below if self._below is not None else t) if value <= self.low else None
        if not self.on and self._above is not None and t - self._above >= self.hold:
            self.on = True
            return True
        if self.on and self._below is not None and t - self._below >= self.release_hold:
            self.on = False
            return True
        return False


def brakes(cfg):
    """The two brakes (CPU, memory) from the config: cpu_brake, cpu_release, cpu_window, mem_brake…"""
    cfg = cfg or {}
    return {"cpu": Brake(cfg.get("cpu_brake", CPU_BRAKE), cfg.get("cpu_release", CPU_RELEASE), cfg.get("cpu_window", CPU_HOLD)),
            "mem": Brake(cfg.get("mem_brake", MEM_BRAKE), cfg.get("mem_release", MEM_RELEASE), cfg.get("mem_window", MEM_HOLD))}


# --- Directory semaphore ---------------------------------------------------------------------------------

def pid_alive(pid):
    """Does the process exist? (Windows: never os.kill, which TERMINATES the process there.)"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Slots:
    """`n` named slots under `root`: `<name>-<i>/` (atomic mkdir) with an `owner` file; waiters are
    `wait-<name>-<who>-<pid>` files. A slot whose holder is dead, or too old, is taken back."""

    def __init__(self, root, name, n, guard=lambda p: p):
        self.root, self.name, self.n, self.guard = Path(root), name, max(1, int(n)), guard

    def _owner(self, d):
        try:
            return json.loads((d / "owner").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _stale(self, d, now):
        o = self._owner(d)
        if o.get("pid") and not pid_alive(o["pid"]):
            return True
        try:
            return now - (d / "owner" if (d / "owner").is_file() else d).stat().st_mtime > SLOT_STALE_SECONDS
        except OSError:
            return True

    def try_acquire(self, who, pid=None, now=None):
        """A free (or taken back) slot → its directory, else None."""
        now = now or time.time()
        self.guard(self.root).mkdir(parents=True, exist_ok=True)
        for i in range(self.n):
            d = self.root / f"{self.name}-{i}"
            try:
                d.mkdir()
            except FileExistsError:
                if not self._stale(d, now):
                    continue
                shutil.rmtree(d, ignore_errors=True)
                try:
                    d.mkdir()
                except FileExistsError:
                    continue
            (d / "owner").write_text(json.dumps({"who": who, "pid": pid or os.getpid(), "t": int(now)}), encoding="utf-8")
            return d
        return None

    def wait_marker(self, who, pid=None):
        return self.root / f"wait-{self.name}-{who}-{pid or os.getpid()}"

    def acquire(self, who, pid=None, poll=5, say=None, sleep=time.sleep):
        """Blocks until a slot is obtained; the queue stays visible (wait-* file)."""
        marker, waited = self.wait_marker(who, pid), False
        try:
            while True:
                d = self.try_acquire(who, pid)
                if d:
                    return d
                if not waited:
                    self.guard(marker).write_text(json.dumps({"who": who, "pid": pid or os.getpid(), "t": int(time.time())}),
                                                  encoding="utf-8")
                    if say:
                        say(f"{self.n} {self.name} already running on this host: waiting for my turn…")
                    waited = True
                sleep(poll)
        finally:
            marker.unlink(missing_ok=True)

    @staticmethod
    def release(d):
        shutil.rmtree(d, ignore_errors=True)


def slot_state(root, now=None):
    """For the board: {"held": [{name, who, since}], "waiting": [{name, who, since}]} (dead slots ignored)."""
    root, now = Path(root), now or time.time()
    held, waiting = [], []
    if not root.is_dir():
        return {"held": held, "waiting": waiting}
    for p in sorted(root.iterdir()):
        try:
            if p.is_dir() and re.match(r"^[\w-]+-\d+$", p.name):
                o = json.loads((p / "owner").read_text(encoding="utf-8"))
                if pid_alive(o.get("pid")):
                    held.append({"name": p.name.rsplit("-", 1)[0], "who": o.get("who", "?"), "since": o.get("t", 0)})
            elif p.is_file() and p.name.startswith("wait-"):
                o = json.loads(p.read_text(encoding="utf-8"))
                if pid_alive(o.get("pid")):
                    waiting.append({"name": p.name[5:].rsplit("-", 2)[0], "who": o.get("who", "?"), "since": o.get("t", 0)})
        except (OSError, ValueError):
            continue
    return {"held": held, "waiting": waiting}


# --- Orphan processes ------------------------------------------------------------------------------------

def list_procs():
    """node/npm/npx processes: [{pid, name, cmd}]. Windows: CIM (PowerShell); else `ps`. Empty on failure."""
    try:
        if os.name == "nt":
            script = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(node|npm|npx)' } | "
                      "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress")
            r = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                               encoding="utf-8", timeout=30, creationflags=0x08000000)
            data = json.loads(r.stdout) if r.stdout.strip() else []
            data = [data] if isinstance(data, dict) else data
            return [{"pid": d["ProcessId"], "name": d["Name"], "cmd": d.get("CommandLine") or ""} for d in data]
        r = subprocess.run(["ps", "-eo", "pid,comm,args"], capture_output=True, text=True, timeout=30)
        out = []
        for line in r.stdout.splitlines()[1:]:
            parts = line.split(None, 2)
            if len(parts) >= 2 and re.match(r"^(node|npm|npx)", parts[1]):
                out.append({"pid": int(parts[0]), "name": parts[1], "cmd": parts[2] if len(parts) > 2 else ""})
        return out
    except Exception:  # noqa: BLE001
        return []


def _norm(p):
    return str(p).replace("\\", "/").rstrip("/").lower()


def orphans(procs, dead_paths, own_pid=None):
    """Processes whose command line names a vanished worktree → [{pid, name, path}]. Known limit: an `npm ci`
    started without a path in its command line is not recognised (a process's current directory cannot be read
    without a third-party tool); its child node processes do name the path."""
    dead = [_norm(p) for p in dead_paths if p]
    out = []
    for p in procs:
        if own_pid and p["pid"] == own_pid:
            continue
        cmd = _norm(p.get("cmd", ""))
        for d in dead:
            if d and re.search(re.escape(d) + r"(/|$|\s|\"|')", cmd):
                out.append({"pid": p["pid"], "name": p.get("name", ""), "path": d})
                break
    return out
