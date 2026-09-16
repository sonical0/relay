@./AGENTS.md

## Claude Code — capabilities

- Full filesystem access (no sandbox) → **orchestrator role**
- Subagents: Explore, Plan, general-purpose
- Skills: discovered in `.claude/skills/` only — see the Skills section in `AGENTS.md`
- Node scripts, file writing, web

## Claude Code — limits

- No headless browser of its own. If a sandboxed worker agent has Playwright,
  delegate liveness checks and SPA scraping to it rather than improvising.

## Hooks

Relay installs two hooks in `~/.claude/settings.json` (or `$CLAUDE_CONFIG_DIR`):

| Hook | Script | Role |
|---|---|---|
| `UserPromptSubmit` | `relay-session-hook.js` | injects the pre-digested state, once per session |
| `SessionStart` | `relay-setup-hook.js` | re-checks the hooks are present, re-adds them if not |

Install once per machine: `node relay-setup-hook.js`. Hook configuration is
**local to the machine** and never committed.

## Browser panel (optional)

If a terminal multiplexer with a visible browser panel (e.g. wmux) is installed,
prefer it over headless browsing so the user sees what you are doing. Probe it
once per session rather than assuming; record the result in
`relay-brain.yml → machines[<hostname>].browser_panel`.
