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
  - `paths: ["glob", …]` → native, but loaded only when a matching file is touched.
- **Native is for what you would not think to look for** (a debugging or spec method). Everything invoked deliberately is `disable-model-invocation: true`.
- **A native description is paid every session**: ~200 bytes, **in quotes** (they contain `:`), just enough to trigger selection. Method detail goes in the body. `TOOLS/relay-state.mjs` alerts past 4,000 bytes in total.
- **The body has a ceiling too**: after compaction Claude Code re-injects at most 5,000 tokens per invoked skill. Past ~12 KB, move maintainer documentation to a sibling file (`MAINTENANCE.md`).
- **Rules `RULES/*.md`**: a `paths:` frontmatter is mandatory (without it the rule loads every session, like `AGENTS.md`). A `paths:` rule is not re-injected after compaction until a matching file is touched again: what must always apply stays in `AGENTS.md`.
- **Third-party skill**: clone it into a scratch dir, scan it (e.g. `skillspector scan <dir> --no-llm`) and read it fully past a moderate score, then rewrite it condensed with `disable-model-invocation: true`. Never `npx skills add -g` or `/plugin install`: they install outside `SKILLS/`, hence outside sync and review.
- **Do not build a routing skill** (tried: 0/3 triggers), nor skill links under `PROJECTS/<x>/.claude/skills/` (git follows junctions and would commit a diverging copy). `DECISIONS.md`, 2026-09-15 and 2026-10-14.
