---
name: product-design
description: "Rework a web product's visual design away from generic defaults: memorable-thing brief, layered research, anti-slop checklist, and divergent variants compared before implementing. Use for a new design or a real overhaul — not a tweak."
disable-model-invocation: true
---

## When to use

When starting the visual design of a new product or site, or when an existing design looks generic ("it looks like everything else") and deserves a real overhaul rather than an adjustment. It sits upstream of design skills that execute a chosen direction well: here the goal is to **choose** a distinctive direction before executing it.

## Phase 1 — Product brief

A single question through the structured question tool, covering:
1. Confirm the product, for whom, in which sector (prefill from the README/notes when possible and just confirm).
2. Kind of project: web app, dashboard, showcase site, editorial, internal tool.
3. "Shall I look at what others do in this sector, or start straight from design knowledge?"

**Forcing question, the "memorable thing"**: "What is THE one thing you want someone to remember on first seeing this product?" One sentence. A feeling, a precise colour, a statement, a stance. Write it down — every later design decision must serve it. A design that tries to be memorable for everything is memorable for nothing.

## Phase 2 — Three-layer research (if the user said yes)

Web search: "[product category] design [year]", "best [sector] websites". If visual captures help, screenshot 2-3 sites confirmed by the user in a browser (a browser panel the user can see, else headless; *optional*, with a shared multiplexer panel: your own tab and `--surface <id>`, see `CLAUDE.md`). Never log into a competitor's account.

**Three-layer synthesis**:
- **Layer 1 (proven)**: which patterns do all products of this category share? These are the users' baseline expectations.
- **Layer 2 (new/trending)**: what do the search results say about current trends?
- **Layer 3 (first principles)**: given THIS product and ITS users, is there a reason the conventional approach is wrong? Where should we deliberately break with the category's codes?

If Layer 3 reveals a real insight, name it: "EUREKA: every [category] product does X assuming [hypothesis]. But this product's users [evidence] — so we should do Y." If the user wants no research, go straight to Phase 3 with existing design knowledge — that is enough.

## Phase 3 — Anti-generic checklist ("anti-slop")

Apply it systematically, when generating as when reviewing an existing design. **Avoid unless deliberately chosen and justified**:
- generic purple/blue gradient;
- 3-column feature grid with repeated icon-title-text;
- everything centred;
- decorative blobs/shapes and gratuitous separators;
- nested cards (cards inside cards);
- a "kicker" above the title (small uppercase label);
- an icon in a tile above every section title;
- "glow" effect in dark mode;
- a default font without a deliberate choice (Inter/Roboto/Arial "because it is the system font") — pick a font for a reason, even if the final choice is sober;
- recent generation tics: cream background + terracotta accent; black with a single acid accent; ruled "newspaper" style; SaaS card kit (same radius everywhere, diffuse grey shadow); one word of the title in colour; "→" added to every button; 01/02/03 numbering on what is not a sequence; fade-up on every section.

**UX principles in the background** (from gstack's design review): observe how users actually behave rather than assume; the first view is a poster, not a document — it gives the essentials at a glance; navigation behaves like a map (the user always knows where they are); mobile follows the same rules with higher stakes (less room for touch error).

## Phase 4 — Generate truly divergent variants

**Anti-convergence requirement (hard)**: each variant MUST use a different font, palette and layout approach. Concrete test: if the main headline could be swapped between two variants unnoticed, they are too close. The variants must look like the work of three different design teams, not one team at three caffeine levels.

1. Propose 3 distinct creative directions as a list (A/B/C), each with a name, a one-sentence description and its **token plan**: 4 to 6 hex colours named by role, fonts and their roles, an ASCII mock-up of the first view. Check each plan against the brief: what any neighbouring brief would also get is revised, and say what changed. Confirm with the user before generating (token cost if parallel).
2. Launch 3 general-purpose subagents **in parallel, in a single message** — one per direction. Each receives the full brief (Phases 1-3), its assigned direction, and the instruction to produce a self-contained, representative HTML/CSS file (not production code — a faithful mock-up) following its direction and the anti-slop checklist.
3. Write each variant to a local HTML file (`PLANS/design-<project>/<direction>.html`) and open them side by side in a local browser, not as a hosted artifact (`AGENTS.md`, previews stay local).
4. **Check before showing**: a deterministic design linter with no LLM and no key (e.g. `npx -y impeccable detect --no-advisory PLANS/design-<project>/`) on the files, then on the page served locally at four widths: `360x800`, `768x1024`, `1280x800`, `1920x1080`. Fix the findings or justify those you keep. Do not install a linter's plugin or hook (context cost, and some launch an agent without permissions).
5. Present the 3 files to the user with, for each, what sets it apart from the other two. Collect feedback, iterate on the chosen direction — not on all three in parallel again, unless explicitly asked.

**Interface copy** (in every variant): name things as the user does, not as the system does; a button says what it does and its confirmation repeats the verb ("Publish" → "Published"); an error says what to fix; an empty screen invites the next action.

## Phase 5 — Self-critique and second opinion

On the chosen variant, before implementing it:
- **One bold gesture** (the one serving the memorable thing), the rest sober. Then a last pass: remove one accessory.
- **Quality floor**: visible keyboard focus, `prefers-reduced-motion` respected, sufficient contrast (text and controls).
- **Second opinion** (optional): a fresh subagent that did not see the generation receives the brief and the file and says what looks generic; fix or justify.

## Taste memory (light, per project)

After a decision (variant chosen, or a direction explicitly rejected), note in one line in the project's notes: font, palette, approach chosen + one line on what was explicitly ruled out and why. Next time this skill runs on the same project, read that note before Phase 1 and take it into account in the 3 directions (without being locked in — say explicitly when departing from it and why).
