/**
 * atomic.mjs — atomic writes that survive Windows file locks.
 *
 * The tmp + rename pattern protects against an interrupted write, but on Windows
 * the rename fails when the destination is momentarily held open by another
 * process: cloud sync clients scan the directory constantly, and so do the search
 * indexer and the antivirus. Measured on the internal system, 2026-09-15: roughly
 * 1 run in 6 exited with EPERM, which made `npm run state` fail at random.
 *
 * Two protections:
 *   - the temp file is suffixed with the PID, so two concurrent processes cannot
 *     steal each other's intermediate file (that failure showed up as ENOENT);
 *   - a short retry on EPERM/EBUSY/EACCES absorbs the transient lock.
 */

import fs from 'fs';
import path from 'path';

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

/**
 * Replaces a whole directory by swap: the old one is only removed once the new one
 * is complete, so a concurrent reader never sees an empty directory.
 * `build(dir)` receives the temporary directory to fill.
 */
export function swapDir(target, build) {
  sweepStale(target);
  const tmp = `${target}.${process.pid}.tmp`;
  const old = `${target}.${process.pid}.old`;
  fs.rmSync(tmp, { recursive: true, force: true });
  fs.mkdirSync(tmp, { recursive: true });
  build(tmp);
  fs.rmSync(old, { recursive: true, force: true });
  if (fs.existsSync(target)) renameWithRetry(target, old);
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
}
