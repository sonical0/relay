// Safety net for `wmux browser`: sends the command only if the target tab EXISTS.
//
//   node wbrowser.mjs <surfaceId> <verb> [args...]
//   node wbrowser.mjs surf-1234... goto http://localhost:3000/admin     (reliable navigation, through eval)
//   node wbrowser.mjs surf-1234... eval "document.title"
//   node wbrowser.mjs surf-1234... screenshot | snapshot | get-text | click e3 | ...
//
// Why: a `wmux browser ... --surface <id>` command aimed at a tab that no longer exists fails
// ("Could not open browser panel") AND creates 3 empty browser panes; `open` aimed at a background tab
// can create one too. Here the tab is checked first, `open` is refused (use `goto`, which navigates
// through location.href), and the panes are counted again afterwards: if there are more, it says so.
import { spawnSync } from "node:child_process";
import path from "node:path";

const [surface, verb, ...rest] = process.argv.slice(2);
if (!surface || !verb) {
  console.error("usage: node wbrowser.mjs <surfaceId> <verb> [args...]   (verbs: goto <url>, eval, screenshot, snapshot...)");
  process.exit(2);
}
const node = process.env.WMUX_NODE || "node";
const cli = process.env.WMUX_CLI || path.join(path.dirname(process.env.WMUX_BIN || ""), "..", "cli", "wmux.js");
const env = { ...process.env, ...(process.env.WMUX_NODE_ELECTRON ? { ELECTRON_RUN_AS_NODE: "1" } : {}) };
const wmux = (...a) => spawnSync(node, [cli, ...a], { encoding: "utf8", env });

function surfaces() {
  const r = wmux("list-surfaces");
  try { return JSON.parse(r.stdout).surfaces || []; } catch { return null; }
}

const before = surfaces();
if (!before) { console.error("wmux unreachable"); process.exit(2); }
const target = before.find((s) => s.id === surface || s.id.startsWith(surface));
if (!target) {
  console.error(`refused: tab ${surface} does not exist (any more). A command aimed at it would create empty panes.`);
  process.exit(3);
}
if (target.type !== "browser") { console.error(`refused: ${target.id} is not a browser (${target.type})`); process.exit(3); }
if (verb === "open") { console.error("refused: `open` can create a pane; use `goto <url>`"); process.exit(3); }

// A tab hidden behind the terminal cannot be driven (and every attempt creates 3 empty panes):
// `show` brings it to the front in ITS pane, `hide` puts the pane's terminal back in front.
const siblings = before.filter((s) => s.paneId === target.paneId);
if (verb === "show" || verb === "hide") {
  const to = verb === "show" ? target : siblings.find((s) => s.type === "terminal");
  if (!to) { console.error("no terminal in this pane"); process.exit(3); }
  const r = wmux("focus-surface", to.id);
  if (verb === "show") spawnSync(process.platform === "win32" ? "timeout" : "sleep", process.platform === "win32" ? ["/t", "2", "/nobreak"] : ["2"], { stdio: "ignore", shell: true });
  console.log(r.status === 0 ? `${verb === "show" ? "tab shown" : "terminal back in front"} (${to.id})` : (r.stderr || "failed"));
  process.exit(r.status ?? 1);
}
if (!target.isActive) {
  console.error(`refused: tab ${target.id} is not shown. First: node wbrowser.mjs ${target.id} show; at the end: ... hide`);
  process.exit(3);
}

const args = verb === "goto"
  ? ["browser", "eval", `location.href=${JSON.stringify(rest[0] || "about:blank")}; "ok"`, "--surface", target.id]
  : ["browser", verb, ...rest, "--surface", target.id];
const r = wmux(...args);
process.stdout.write(r.stdout || "");
process.stderr.write(r.stderr || "");

const after = surfaces();
const created = after ? after.filter((s) => !before.some((b) => b.id === s.id)) : [];
if (created.length) console.error(`WARNING: ${created.length} surface(s) created by this command: ${created.map((s) => s.id).join(", ")}: close them.`);
process.exit(r.status ?? 1);
