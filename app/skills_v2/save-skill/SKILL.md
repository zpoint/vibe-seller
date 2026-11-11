---
name: save-skill
description: "Saves the workflow just carried out as a reusable skill, or updates an existing user skill, through the vibe_seller_save_skill MCP tool. Use when the user asks to remember, reuse or amend a workflow (\"把这个任务的流程保存为技能\", \"save this workflow as a skill\", \"remember how to do this\", \"next time do it the same way\", \"update that skill\", \"add this step to the skill\", \"更新这个技能\", \"把这一步加到技能里\"), or when a task produced a procedure other tasks will repeat. Searches existing skills first and extends the one that covers the job instead of creating a near-duplicate."
---

# Save a workflow as a skill

Turn the work you just did into a durable skill so a future task can
repeat it without being re-taught, and reuse the skills that already
exist instead of adding near-duplicates. Do it when the user asks, or
when the task produced a procedure other tasks will clearly repeat.

Skill files persist only through `vibe_seller_save_skill`; a skill
written with the built-in Write/Edit tools is discarded when the task
ends.

## The two tools

- `vibe_seller_list_skills` → `{slug, name, description, source, updatable}`
  per skill. Call it first.
- `vibe_seller_save_skill(slug, skill_md, files?)` → creates a new
  user-space skill, or overwrites an existing `updatable` one (that is
  how you extend). `files` is an optional `{path: content}` map for
  bundled references or scripts.

## Only user-space skills are writable

`source` tells you what a skill is:

- **`builtin`** — shipped with the app and read-only;
  `vibe_seller_save_skill` rejects its slug. If a built-in is the closest
  match, create a new user-space skill with a distinct slug that
  captures the user's workflow and points at the built-in for the
  mechanics.
- **`custom` / `imported`** (`updatable: true`) — user-space, and the
  only skills you may overwrite. A skill you saved earlier in this
  session is `custom`.

Take `source` from a fresh `vibe_seller_list_skills` call, never decide
from memory, and never skip an update because a skill looks built-in:
the usual way an "update that skill" request gets dropped is an agent
recalling a skill as built-in and doing nothing. If you are unsure,
attempt the save — the server overwrites an updatable skill and rejects
a built-in one with a message, after which you create a new skill.

## Extend or create

Read each skill's `description` — it, not the slug, says what the skill
is for — and decide:

- **Extend** when what you did is the same job as an existing updatable
  skill (same intent, same surface, a new step, destination or edge
  case). Load that skill with the Skill tool to see its current content,
  merge your addition in so it comes out stronger rather than longer,
  and save the full merged content under the same slug.
- **Create** when nothing covers the job, or the closest match is
  built-in. A skill that tries to do two unrelated jobs helps with
  neither; when unsure whether something is "the same job", prefer a
  focused new skill.

## What makes a good skill

Skills follow the [Claude Agent Skills](https://platform.claude.com/docs)
standard:

- **The `description` is the whole selection signal.** Third person,
  what the skill does plus the situations and phrasings that should
  trigger it ("Use when…"). If a future agent can't tell from the
  description that the skill applies, it won't load it.
- **Capture the method, not a keystroke log:** the goal, the decisions
  and how to make them, what went wrong and how you noticed. Describe
  browser actions by what they accomplish; a step-by-step transcript
  breaks the first time a page changes, while a reason survives.
- **Keep `SKILL.md` well under 500 lines.** Put growing detail in a
  bundled file (`references/…` via `files`) and point to it.
- **Match freedom to fragility.** State bright-line numbers exactly;
  write judgement calls as guidance that asks the agent to think.

Use generic placeholders in examples (`acme`, `widget-006`, round
numbers), never a real store, brand, SKU or captured metric.

## Frontmatter shape

```yaml
---
name: <human-readable name>
description: "<what it does> + <third-person 'Use when…' triggers>"
---
```

`slug` is passed separately to `vibe_seller_save_skill` (lowercase
letters, digits, hyphens). Optional fields such as `allowed-tools` or
`requires` are fine where they apply.

## After saving

Tell the user whether you created or extended a skill, its name and
slug, and the trigger that will surface it next time.
