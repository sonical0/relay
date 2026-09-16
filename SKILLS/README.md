# SKILLS — shared agent skills

A skill is a packaged method: what to do, in which order, with which files. It lets
an agent arriving cold understand immediately how to work on a given task or project.

```
SKILLS/
├── skills.manifest.json   ← source of truth: which skills exist, how each one loads
├── _template/SKILL.md     ← copy this for a new skill
└── <skill-name>/SKILL.md  ← the skill itself
```

## The description is the expensive part

A skill's `description:` enters the system prompt **on every session** — about 0.37
token per byte, measured. The body is only loaded when the skill is invoked.

So: put all the method detail in the body, and keep the description near 200 bytes,
just enough for the agent to decide whether this skill is the right one. Write it
**in quotes** — descriptions contain `:`, and without quotes the frontmatter stops
being valid YAML for our own tooling.

`TOOLS/relay-state.mjs` raises an alert once the linked descriptions exceed 4,000
bytes in total, so this cannot drift unnoticed.

## Two loading modes

Set `load` per skill in `skills.manifest.json`, then run `node TOOLS/skills-link.mjs`.

| `load` | What happens | Use it for |
|---|---|---|
| `native` | Linked into `.claude/skills/`, present in the system prompt, triggers on its own | Methods you would not think to go looking for — a debugging discipline fires when you are already inside the bug |
| `rag` | No link, absent from the system prompt. Found with `node TOOLS/rag-query.mjs --query "…" --type skill`, read before use | Methods you invoke deliberately, and rare ones |

`rag` skills are listed by name in the injected startup state (~25 tokens). Without
that line they would be unreachable: you do not search for what you do not know exists.

A manifest entry can also carry `suggest_if`, a regex tested against a project's file
and dependency names. On a match, the skill is named in that project's state block —
the only mechanism that actually surfaces an on-demand skill at the right moment. A
routing skill does not work: tried and measured at 0 auto-triggers out of 3 projects.

**Rollback**: `node TOOLS/skills-all-native.mjs && node TOOLS/skills-link.mjs` puts
everything back to native and prints what it costs per session.

## Creating a skill

1. `cp -r SKILLS/_template SKILLS/<name>` and fill in `SKILL.md`
2. Add an entry to `skills.manifest.json` with `load` and a one-line `why`
3. `node TOOLS/skills-link.mjs` (then `node TOOLS/rag-index.mjs` for a `rag` skill)

Non-Claude agents do not read `.claude/skills/`. Point them at
`SKILLS/<name>/SKILL.md` by path.
