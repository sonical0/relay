# SKILLS — shared agent skills

A skill is a packaged method: what to do, in which order, with which files. It lets an agent
arriving cold understand immediately how to work on a given task or project.

```
SKILLS/
├── skills.manifest.json   ← which skills exist (name → source folder)
├── _template/SKILL.md     ← copy this for a new skill
├── inbox/                 ← shipped: proposal box and /inbox review
├── supervision/           ← shipped: multi-agent supervision rules, one file per role
└── <skill-name>/SKILL.md  ← your own skills (personal: synced, not committed)
```

## The description is the expensive part

A native skill's `description:` enters the system prompt **on every session** — about 0.37
token per byte, measured. The body only loads when the skill is invoked. Keep the description
near 200 bytes, **in quotes** (it contains `:`). `TOOLS/relay-state.mjs` alerts once the
native descriptions exceed 4,000 bytes in total.

## Loading mode: in the frontmatter

| Frontmatter | What happens | Use it for |
|---|---|---|
| *(nothing)* | Native: in the system prompt, triggers on its own | Methods you would not think to look for — a debugging discipline fires when you are already inside the bug |
| `disable-model-invocation: true` | Out of the system prompt (zero tokens); `/name` for the user, `SKILLS/<name>/SKILL.md` for the agent; listed by name in the injected state | Everything you invoke deliberately |
| `paths: ["glob"]` | Native, loaded only when a matching file is touched | Format-specific methods |

Rollback for an on-demand skill: remove the line. A routing skill that suggests the others
does not work: measured at 0 auto-triggers out of 3 (DECISIONS, 2026-09-15).

## Creating a skill

1. `cp -r SKILLS/_template SKILLS/<name>` and fill in `SKILL.md`.
2. Add `{ "name": "<name>", "src": "<name>", "why": "…" }` to `skills.manifest.json`.
3. `node TOOLS/skills-link.mjs` (the setup hook also runs it at every start).

Past ~12 KB, move maintainer documentation to a sibling `MAINTENANCE.md`: after compaction
only 5,000 tokens per invoked skill are re-injected. Third-party skills: `RULES/skills.md`.

Non-Claude agents do not read `.claude/skills/`: point them at `SKILLS/<name>/SKILL.md`.
