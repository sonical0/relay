// Opens a browser (or other) tab IN THE CALLER'S PANE, without touching the focus.
//
//   node wmux-tab.mjs [--type browser] [--title "Browser B"] [--pane <paneId>] [--show]
//   -> prints {"surfaceId": "...", "paneId": "..."}; then drive it with `wmux browser ... --surface <id>`.
//
// Why: `wmux new-surface` does not pass a pane, so wmux creates the tab in the FIRST pane of the
// workspace (the supervisor's). The API does accept a `paneId`: it is called directly here, through the same
// pipe and with the same token as the wmux client. Default pane: the one of WMUX_SURFACE_ID (the shell running
// the command, hence the manager or its worker).
import net from "node:net";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const args = process.argv.slice(2);
const flag = (n) => { const i = args.indexOf(n); return i >= 0 ? args[i + 1] : undefined; };

const PIPE = process.env.WMUX_PIPE || "\\\\.\\pipe\\wmux";
function token() {
  if (process.env.WMUX_PIPE_TOKEN?.trim()) return process.env.WMUX_PIPE_TOKEN.trim();
  const sfx = process.env.WMUX_INSTANCE?.trim() ? `-${process.env.WMUX_INSTANCE.trim()}` : "";
  const base = process.env.APPDATA || path.join(os.homedir(), "AppData", "Roaming");
  try { return fs.readFileSync(path.join(base, `wmux${sfx}`, "pipe-token"), "utf-8").trim(); } catch { return ""; }
}

function call(method, params = {}) {
  if (process.env.WMUX_SURFACE_ID && params.caller === undefined) params = { ...params, caller: process.env.WMUX_SURFACE_ID };
  return new Promise((resolve, reject) => {
    const c = net.connect({ path: PIPE }, () => c.write(JSON.stringify({ method, params, id: 1, token: token() }) + "\n"));
    let data = "";
    const t = setTimeout(() => { c.destroy(); reject(new Error(`timed out: ${method}`)); }, 10000);
    c.on("data", (ch) => {
      data += ch;
      if (!data.includes("\n")) return;
      clearTimeout(t); c.destroy();
      const r = JSON.parse(data.trim());
      r.error ? reject(new Error(r.error.message)) : resolve(r.result);
    });
    c.on("error", (e) => { clearTimeout(t); reject(e); });
  });
}

const { panes } = await call("pane.list", {});
let pane = flag("--pane");
if (!pane) {
  const me = process.env.WMUX_SURFACE_ID;
  if (!me) { console.error("WMUX_SURFACE_ID not set: give --pane <paneId>"); process.exit(2); }
  pane = (panes || []).find((p) => p.surfaces?.some((s) => s.id === me))?.paneId;
  if (!pane) { console.error(`pane of ${me} not found`); process.exit(2); }
}
const info = (panes || []).find((p) => p.paneId === pane);
const wasActive = info?.surfaces?.[info.activeSurfaceIndex ?? 0]?.id;  // usually the manager's terminal
const res = await call("surface.create", { type: flag("--type") || "browser", paneId: pane });
if (!res?.surfaceId) { console.error("creation refused", res); process.exit(1); }
if (flag("--title")) await call("surface.rename", { id: res.surfaceId, title: flag("--title") });
// The new tab becomes the pane's active tab: put the terminal back in front, unless --show.
if (wasActive && !args.includes("--show")) await call("surface.focus", { id: wasActive });
console.log(JSON.stringify(res));
