"""Host load (Windows: stdlib + ctypes; Linux: /proc; other systems: nothing): CPU, memory, disk.

Same definitions as the Windows Task Manager:
  - CPU    : 1 - idle time / total time (GetSystemTimes);
  - memory : dwMemoryLoad (GlobalMemoryStatusEx);
  - disk   : active time = 100 - "% Idle Time" of PhysicalDisk(_Total) (PDH counter, English name
             so as not to depend on the Windows display language).
"""
import ctypes
import os
import shutil
import threading
import time
from ctypes import wintypes

INTERVAL = 2
HISTORY = 90  # 3 min at 2 s
_lock = threading.Lock()
_stats = {}


class _MEMSTAT(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class _PDH_VALUE(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]


def _ft(f):
    return (f.dwHighDateTime << 32) | f.dwLowDateTime


def _cpu_times():
    idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    return _ft(idle), _ft(kernel) + _ft(user)  # kernel time includes idle time


def _memory():
    m = _MEMSTAT()
    m.dwLength = ctypes.sizeof(_MEMSTAT)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.dwMemoryLoad, m.ullTotalPhys, m.ullAvailPhys


class _Disk:
    PDH_FMT_DOUBLE = 0x00000200

    def __init__(self):
        self.pdh = ctypes.windll.pdh
        self.query, self.counter = wintypes.HANDLE(), wintypes.HANDLE()
        if self.pdh.PdhOpenQueryW(None, None, ctypes.byref(self.query)) != 0:
            raise OSError("PdhOpenQuery")
        if self.pdh.PdhAddEnglishCounterW(self.query, r"\PhysicalDisk(_Total)\% Idle Time", None,
                                         ctypes.byref(self.counter)) != 0:
            raise OSError("PdhAddEnglishCounter")
        self.pdh.PdhCollectQueryData(self.query)

    def active(self):
        self.pdh.PdhCollectQueryData(self.query)
        v = _PDH_VALUE()
        if self.pdh.PdhGetFormattedCounterValue(self.counter, self.PDH_FMT_DOUBLE, None, ctypes.byref(v)) != 0:
            return None
        return max(0.0, min(100.0, 100.0 - v.doubleValue))


def parse_proc_stat(line):
    """First line of /proc/stat ("cpu  user nice system idle iowait …") -> (idle, total)."""
    vals = [int(x) for x in line.split()[1:9]]
    return vals[3] + vals[4], sum(vals)  # idle + I/O wait, total


def parse_meminfo(text):
    """Content of /proc/meminfo -> (load in %, total bytes, available bytes)."""
    info = {}
    for line in text.splitlines():
        k, _, v = line.partition(":")
        if v.split():
            info[k] = int(v.split()[0]) * 1024
    total, avail = info["MemTotal"], info.get("MemAvailable", info.get("MemFree", 0))
    return round(100 * (total - avail) / total), total, avail


def _cpu_times_posix():
    with open("/proc/stat", encoding="utf-8") as f:
        return parse_proc_stat(f.readline())


def _memory_posix():
    with open("/proc/meminfo", encoding="utf-8") as f:
        return parse_meminfo(f.read())


def _loop():
    if os.name == "nt":
        cpu_times, memory, root = _cpu_times, _memory, "C:\\"
        try:
            disk = _Disk()
        except OSError:
            disk = None
    elif os.path.exists("/proc/stat"):
        cpu_times, memory, root, disk = _cpu_times_posix, _memory_posix, "/", None  # disk activity: Windows only
    else:
        return  # neither Windows nor /proc: no host statistics, the board does without
    prev = cpu_times()
    hist = []
    while True:
        time.sleep(INTERVAL)
        try:
            cur = cpu_times()
            d_idle, d_total = cur[0] - prev[0], cur[1] - prev[1]
            prev = cur
            cpu = max(0.0, min(100.0, 100.0 * (1 - d_idle / d_total))) if d_total else 0.0
            load, total, avail = memory()
            act = disk.active() if disk else None
            du = shutil.disk_usage(root)
            hist.append([int(time.time()), round(cpu), load, None if act is None else round(act)])
            del hist[:-HISTORY]
            with _lock:
                _stats.update({
                    "cpu": round(cpu), "mem": load, "mem_used_gb": round((total - avail) / 2**30, 1),
                    "mem_total_gb": round(total / 2**30, 1), "disk": None if act is None else round(act),
                    "disk_free_gb": round(du.free / 2**30), "disk_used_pct": round(100 * du.used / du.total),
                    "t": int(time.time()), "history": list(hist),
                })
        except Exception as e:  # noqa: BLE001 — host stats must never bring the server down
            print("host stats:", e, flush=True)


def start():
    threading.Thread(target=_loop, daemon=True).start()


def snapshot():
    with _lock:
        return dict(_stats)
