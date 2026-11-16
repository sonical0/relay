@./AGENTS.md

## Gemini CLI — capabilities

- Headless Playwright: liveness checks, scraping SPAs
- Node scripts, Python, shell
- Subagents: generalist, codebase_investigator
- Web search, image generation, scientific databases

## Gemini CLI — limits

- Sandboxed to `RELAY_ROOT` and below — no access to paths outside it
- Not an orchestrator: it can be spawned as a worker, it does not spawn others

## Sandbox

If `$RELAY_ROOT` is set → sandbox is `$RELAY_ROOT`.
Otherwise → the directory containing this `GEMINI.md`.

## Delegation

Delegate to Gemini the capabilities it alone has rather than improvising a degraded
equivalent. It does not read `.claude/skills/`: point it at `SKILLS/<name>/SKILL.md`
by path.
