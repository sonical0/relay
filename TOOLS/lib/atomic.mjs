/**
 * atomic.mjs — atomic writes that survive Windows file locks.
 *
 * The tmp + rename pattern protects against an interrupted write, but on Windows
 * the rename fails when the destination is momentarily held open by another
 * process: cloud sync clients scan the directory constantly, and so do the search
 * indexer and the antivirus. Measured on the internal system, 2026-09-15: roughly
 * 1 run in 6 exited with EPERM, which made `npm run state` fail at random.
 *
 * Protections:
 *   - the temp file is suffixed with the PID, so two concurrent processes cannot
 *     steal each other's intermediate file (that failure showed up as ENOENT);
 *   - a short retry on EPERM/EBUSY/EACCES absorbs the transient lock;
 *   - `validate` re-reads the temp file before the swap (format re-validation);
 *   - `writeIfChanged` skips identical content; `withLock` serialises writers.
 */

import fs from 'fs';
import os from 'os';
import path from 'path';
import crypto from 'crypto';

const LOCK_CODES = new Set(['EPERM', 'EBUSY', 'EACCES']);
const DELAYS_MS = [40, 80, 160, 320, 640];

function sleepSync(ms) {
  // No await: these scripts are synchronous end to end, and a blocking sleep of a
  // few dozen milliseconds is harmless here.
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

/**
 * Removes temp files abandoned by a dead process (crash, killed hook, failure
 * before cleanup). Without this they pile up silently in a synced directory.
 * A file under an hour old may belong to a live process: leave it alone.
 */
function sweepStale(target) {
  const dir = path.dirname(target);
  const base = path.basename(target);
  const re = new RegExp(`^${base.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\.\\d+\\.(tmp|old)$`);
  let entries;
  try { entries = fs.readdirSync(dir); } catch { return; }
  for (const name of entries) {
    if (!re.test(name)) continue;
    const p = path.join(dir, name);
    try {
      if (Date.now() - fs.lstatSync(p).mtimeMs < 3600_000) continue;
      fs.rmSync(p, { recursive: true, force: true });
    } catch { /* already gone, or locked: retry on the next pass */ }
  }
}

/** Renames, retrying while the destination stays locked. */
export function renameWithRetry(from, to) {
  for (let attempt = 0; ; attempt++) {
    try {
      fs.renameSync(from, to);
      return;
    } catch (e) {
      if (!LOCK_CODES.has(e.code) || attempt >= DELAYS_MS.length) {
        try { fs.rmSync(from, { recursive: true, force: true }); } catch {}
        throw e;
      }
      sleepSync(DELAYS_MS[attempt]);
    }
  }
}

/**
 * Writes `content` to `target` atomically.
 * `validate(text)` — optional — re-reads the temp file and throws if the produced
 * content is invalid; the original is then never replaced.
 */
export function writeAtomic(target, content, validate) {
  sweepStale(target);
  const tmp = `${target}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, content, 'utf8');
  if (validate) {
    try {
      validate(fs.readFileSync(tmp, 'utf8'));
    } catch (e) {
      try { fs.unlinkSync(tmp); } catch {}
      throw new Error(`Write aborted for ${target} — ${e.message}`);
    }
  }
  renameWithRetry(tmp, target);
}

/** A date line generated files carry: it changes every day while the content does not. */
export const UPDATED_LINE = /^updated: .*$/gm;

/**
 * Writes `target` only when its content changes. Returns true when written.
 *
 * The end-of-turn hook regenerates files after every answer; rewriting them
 * identically means needless sync transfers and an mtime that no longer means
 * anything to the tools that rely on it. `ignore` strips what varies without
 * mattering (by default an `updated:` line): a file that differs only there keeps
 * its old version. Pass `ignore: null` for a strict comparison.
 */
export function writeIfChanged(target, content, { ignore = UPDATED_LINE, validate } = {}) {
  let current = null;
  try { current = fs.readFileSync(target, 'utf8'); } catch { /* missing: write it */ }
  if (current !== null) {
    const strip = (s) => (ignore ? s.replace(ignore, '') : s);
    if (current === content || strip(current) === strip(content)) return false;
  }
  fs.mkdirSync(path.dirname(target), { recursive: true });
  writeAtomic(target, content, validate);
  return true;
}

/** Windows ignores path case: without this, a run through `…/relay` and another through
    `…/Relay` would take two different locks for the same file. */
const lockKey = (target) => {
  const r = path.resolve(target);
  return process.platform === 'win32' ? r.toLowerCase() : r;
};
const lockOwner = (lock) => {
  try { return JSON.parse(fs.readFileSync(path.join(lock, 'owner.json'), 'utf8')).token; } catch { return null; }
};

/**
 * Inter-process lock on a given target, then `fn()`.
 *
 * `mkdir` is atomic on every useful file system: it fails with EEXIST when the
 * directory exists, which makes it a test-and-set in one operation.
 *
 * The lock lives in the machine's temp directory, not next to the target: the
 * target may sit in a synced folder, and a lock copied from another machine would
 * block a legitimate run here. The race handled is between processes of one machine.
 *
 * A killed process leaves its lock behind; past `staleMs` it is considered abandoned.
 */
export function withLock(target, fn, { timeoutMs = 10_000, staleMs = 120_000 } = {}) {
  const lock = path.join(
    os.tmpdir(),
    `relay-lock-${crypto.createHash('sha1').update(lockKey(target)).digest('hex').slice(0, 16)}`
  );
  const deadline = Date.now() + timeoutMs;

  for (;;) {
    try {
      fs.mkdirSync(lock);
      break;
    } catch (e) {
      if (e.code !== 'EEXIST') throw e;
      let age = null;
      try { age = Date.now() - fs.statSync(lock).mtimeMs; } catch { /* vanished meanwhile */ }
      if (age !== null && age > staleMs) {
        try { fs.rmSync(lock, { recursive: true, force: true }); } catch {}
        continue;
      }
      if (Date.now() > deadline) {
        let who = '';
        try { who = ` (held by ${JSON.parse(fs.readFileSync(path.join(lock, 'owner.json'), 'utf8')).pid})`; } catch {}
        throw new Error(`Lock not obtained on ${path.basename(target)} after ${timeoutMs} ms${who} — another run is probably in progress.`);
      }
      sleepSync(100);
    }
  }

  // A token for this acquisition: if the lock was judged stale and taken over by another
  // process during an overly long pass, we must not remove that process's lock.
  const token = `${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  try {
    try {
      fs.writeFileSync(path.join(lock, 'owner.json'), JSON.stringify({ pid: process.pid, at: Date.now(), token }), 'utf8');
    } catch { /* the lock holds without this trace */ }
    return fn();
  } finally {
    const owner = lockOwner(lock);
    if (owner === token || owner === null) {
      try { fs.rmSync(lock, { recursive: true, force: true }); } catch {}
    }
  }
}

/**
 * Replaces a whole directory by swap: the old one is only removed once the new one
 * is complete, so a concurrent reader never sees an empty directory.
 * `build(dir)` receives the temporary directory to fill.
 *
 * Serialised by a lock. Measured on the internal system: two sessions regenerating
 * the state at once failed with EPERM about 1 run in 36 — on Windows, renaming a
 * directory onto one the other process has just recreated fails, and no retry delay
 * can fix an occupied destination. 0 failures in 36 with the lock.
 */
export function swapDir(target, build) {
  return withLock(target, () => {
    sweepStale(target);
    const tmp = `${target}.${process.pid}.tmp`;
    const old = `${target}.${process.pid}.old`;
    fs.rmSync(tmp, { recursive: true, force: true });
    fs.mkdirSync(tmp, { recursive: true });
    build(tmp);
    fs.rmSync(old, { recursive: true, force: true });
    if (fs.existsSync(target)) {
      try {
        renameWithRetry(target, old);
      } catch (e) {
        if (e.code !== 'ENOENT') throw e;
      }
    }
    try {
      renameWithRetry(tmp, target);
    } catch (e) {
      // Put the old one back rather than leave the directory missing.
      if (fs.existsSync(old) && !fs.existsSync(target)) {
        try { fs.renameSync(old, target); } catch {}
      }
      throw e;
    }
    fs.rmSync(old, { recursive: true, force: true });
  });
}
