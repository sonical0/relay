---
name: skill-name
description: "What this skill does and when to reach for it. Keep it near 200 bytes: it is paid for on every session. Quotes are required — this text contains colons."
# On demand (zero tokens until /skill-name): keep the next line. Native (triggers on its own): remove it.
disable-model-invocation: true
---

# {SKILL_NAME}

> The body is only loaded when the skill is invoked. Put all the method detail here,
> not in the description above.

## When this applies

<!-- One or two lines. What situation, what signal in the request. -->
<!-- Also say when it does NOT apply — that is what keeps a skill from misfiring. -->

## Method

<!-- Numbered steps, in order, specific enough to follow without improvising. -->

1.
2.
3.

## Files to read

| Step | Read |
|---|---|
| | |

## Delegation

- More than 3 files → subagent
- Speculative search across files you do not know → search subagent
- Same task × N → parallel subagents

## Done when

<!-- The check that says the work is finished. Not "it looks right" — a command to -->
<!-- run, a file to diff, an output to compare. -->
