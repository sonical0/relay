---
paths:
  - "SKILLS/**"
  - ".claude/skills/**"
  - "RULES/**"
---

# Skills and rules

- **Single source**: `SKILLS/<skill>/SKILL.md`, listed in `SKILLS/skills.manifest.json`. `node TOOLS/skills-link.mjs` links each skill into `.claude/skills/` and copies `RULES/*.md` into `.claude/rules/`. Neither is synced: re-run the script on every machine (the setup hook does it at each start). Idempotent.
- **The loading mode lives in the skill's frontmatter**, not in the manifest:
  - nothing → native: the description enters the system prompt every session (~0.37 token per byte) and the skill triggers on its own;
  - `disable-model-invocation: true` → out of the system prompt, zero cost; the user invokes it with `/name`, or the agent reads `SKILLS/<name>/SKILL.md`. Its name is listed in the injected state (you do not look for what you do not know exists). Not preloaded in subagents;
  - `paths: ["glob", …]` → native, but loaded only when a matching file is touched (relative to the Relay root).
- **Native is for what you would not think to look for**, and only once your own usage proves it. Relay ships no native skill: `investigate`, `spec`, `qa` and `product-design` are on-demand examples; remove the line `disable-model-invocation: true` from one only if `token-scan --skills` shows you reach for it. Everything invoked deliberately stays on demand, and `rag-query.mjs --type skill` finds it (DECISIONS, 2026-11-21).
- **Prerequisite on each machine**: from a project directory, reading `SKILLS/` and running `TOOLS/` leaves the working directory. In the user `settings.json`: `"permissions": { "additionalDirectories": ["<relay_root>"], "allow": ["Bash(node \"<relay_root>/TOOLS/*)", "Bash(npm run --prefix \"<relay_root>\"*)"] }`.
- **A native description is paid every session**: ~200 bytes, **in quotes** (they contain `:`), just enough to trigger selection. Method detail goes in the body. `TOOLS/relay-state.mjs` alerts past 4,000 bytes in total; `node TOOLS/token-scan.mjs --skills` gives real usage and the skills never invoked.
- **The body has a ceiling too**: after compaction Claude Code re-injects at most 5,000 tokens per invoked skill. Past ~12 KB, move maintainer documentation to a sibling file (`MAINTENANCE.md`). A sibling file over 300 lines starts with a `**Contents:**` line (its `##` headings) so only the useful section is read.
- **Measure before keeping**: a skill rework is judged on 2 or 3 realistic prompts, run in subagents with and without the skill in the same turn, then compared — the measured friction `RULES/tools.md` requires. Before changing a native skill's description, test it on 8 to 10 queries that must trigger it and 8 to 10 neighbours that must not: an agent only consults a skill for what it cannot do alone (hence the routing skill's failure). A script rewritten at every run in the transcripts belongs in the skill (`scripts/`).
- **Rules `RULES/*.md`**: a `paths:` frontmatter is mandatory (without it the rule loads every session, like `AGENTS.md`). A `paths:` rule is not re-injected after compaction until a matching file is touched again: what must always apply stays in `AGENTS.md`.
- **Third-party skill or tool**: (1) clone it into a scratch dir; (2) scan it with a static scanner (e.g. `skillspector scan <dir> --no-llm`), score and findings in the INBOX proposal's `security:` line; (3) **read it in full, whatever the score** (a scanner without an LLM only sees known patterns): SKILL.md, scripts, references, dependencies; look for hidden instructions, network calls, writes outside its folder, destructive commands; note "read: clean" or the points found. Large folder: a read-only subagent citing `file:line`, then a second pass; (4) **adapt, never copy as is**: rewrite it condensed (gstack precedent: ~100 lines instead of 600+), `disable-model-invocation: true` by default. Never `npx skills add -g` or `/plugin install`: they install outside `SKILLS/`, hence outside sync and review. Same spirit for an MCP server, a script or a practice from elsewhere (DECISIONS, 2026-11-14 and 2026-11-19).
- **Do not build a routing skill** (tried: 0/3 triggers), nor skill links under `PROJECTS/<x>/.claude/skills/` (git follows junctions and would commit a diverging copy). `DECISIONS.md`, 2026-09-15 and 2026-10-14.
