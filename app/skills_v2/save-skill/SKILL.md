---
name: save-skill
description: "Save the workflow you just carried out as a reusable skill — OR update an existing skill — whenever the user asks to remember, reuse, or amend it. Triggers include \"把这个任务的流程保存为技能\", \"save this workflow as a skill\", \"save to skill\", \"remember how to do this\", \"turn this into a skill\", \"next time do it the same way\", AND update/extend phrasings like \"update that skill\", \"extend the skill\", \"add this step to the skill\", \"make the skill also remember to…\", \"更新这个技能\", \"把这一步加到技能里\", \"让技能也记住…\". Search the existing skills first, then EXTEND the one that already covers this workflow or CREATE a new one — never duplicate. Skills are saved/extended ONLY through the vibe_seller_save_skill MCP tool (the built-in Write/Edit tools do not persist skill files); they then auto-load for future tasks."
---

# Save this workflow as a skill

The user watched you do something and wants it captured so a future task
can repeat it without being re-taught. Your job is to turn the *actual
work you just did* into a durable, reusable skill — and to reuse the
skills that already exist instead of piling up near-duplicates.

Do this only when asked (this is the user-triggered counterpart to
post-task reflection). Write through the MCP tools below — a skill
written with the built-in Write tool is discarded when the task ends.

## The two tools

- `vibe_seller_list_skills` → `{slug, name, description, source, updatable}`
  per skill. Call it **first**, every time.
- `vibe_seller_save_skill(slug, skill_md, files?)` → creates a new
  user-space skill, or **overwrites** an existing `updatable` one (that
  is how you extend). `files` is an optional `{path: content}` map for
  bundled references or scripts.

## Hard rule: you only ever write user-space skills

`source` tells you what a skill is:

- **`builtin`** — shipped and maintained by the repo. **Read-only.** You
  cannot edit it and `vibe_seller_save_skill` will reject its slug. If a
  built-in is the closest match, do **not** try to extend it — create a
  **new** user-space skill (pick a distinct slug) that captures the
  user's specific workflow and, where useful, points at the built-in for
  the mechanics.
- **`custom` / `imported`** (`updatable: true`) — user-space. These are
  the only skills you may overwrite.

## Search, then judge: extend or create

List the skills and read each `description` — that field, not the slug,
is what tells you what a skill is *for*. Then decide with judgement, not
a checklist:

- **Extend** an updatable skill when the workflow you just did is the
  *same job* as one that already exists — same intent, same surface,
  just a new wrinkle (an extra step, a new destination, an edge case you
  hit). Fold the new knowledge into that skill so it comes out stronger,
  not longer. Read its current `SKILL.md` first, merge your addition in,
  and save the **full merged content** back under the same slug.
- **Create** a new skill when no existing skill really covers this — or
  when the closest match is a built-in. A skill that tries to be two
  unrelated jobs at once helps with neither; when in doubt about whether
  something is "the same job," lean toward a focused new skill.

If you extend, say so to the user and name the skill. If you create,
name the new skill and its trigger.

## What makes a good skill (follow the standard)

Skills follow the [Claude Agent Skills](https://platform.claude.com/docs)
standard. Keep them concise and let them earn their place:

- **`description` is the whole selection signal.** Write it in the third
  person and pack it with *what the skill does* plus the concrete
  situations and phrasings that should trigger it ("Use when…"). If the
  future agent can't tell from the description alone that this skill
  applies, it won't load — nothing else matters as much.
- **Capture intent and judgement, not a keystroke log.** Write the
  reusable *method*: the goal, the decisions and how to make them, the
  things that go wrong and how you knew. Describe browser/tool actions by
  what they accomplish. A rigid step-1/step-2 transcript rots the moment
  a page or menu changes; a heuristic that explains *why* survives.
- **Concise body, progressive disclosure.** Keep `SKILL.md` well under
  500 lines. If detail grows, move it into a bundled file (pass it in
  `files`, e.g. `references/…`) and have `SKILL.md` point to it.
- **Match freedom to fragility.** Bright-line numeric thresholds can be
  stated exactly; genuinely judgement-based choices should read as
  guidance that asks the agent to think.

Generic placeholders only in examples — never a real store, brand, SKU,
or captured metric.

## Frontmatter shape

```yaml
---
name: <human-readable name>
description: "<what it does> + <third-person 'Use when…' triggers>"
---
```

`slug` is passed separately to `vibe_seller_save_skill` (lowercase
letters, digits, hyphens). Optional fields like `allowed-tools` or
`requires` are fine when they apply — look at a built-in skill for a
real example.

## After saving

Tell the user plainly what happened: created vs extended, the skill's
name/slug, and the one-line trigger that will surface it next time.
