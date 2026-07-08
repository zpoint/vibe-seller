You are being asked to reflect before the session ends.
If you already reflected earlier in this conversation, focus ONLY on learnings
from actions taken AFTER your last reflection. **Writing nothing is a valid
outcome** — if this task only produced data/results and no new procedural
knowledge, stop without updating any file.

## Correct what you relied on — FIRST, before writing anything new

Look back at the notes/knowledge you **read** this task. If a stored fact
steered your work and your live observation **contradicted** it, you MUST
fix it **in place** — this outranks recording any new learning.

- **Update, never extend.** Overwrite the wrong line (or delete it). Do
  NOT append a "correction" beneath the stale text, and do NOT leave the
  old line while adding a fresh one elsewhere. The file must end with
  exactly one, current answer to the question.
- **A wrong fact is worse than a missing one.** Every future task trusts
  what it reads. A stale "market X has 0 campaigns", a moved URL, a
  renamed selector — each silently poisons the next run, often by
  suppressing the very check that would reveal the truth. Correcting one
  is the single highest-value thing this reflection can do.
- **Point-in-time state is not knowledge — it expires.** Counts,
  statuses, "as of <date>" snapshots (campaign/SKU counts, inventory,
  which market has ads, current bids) must never be written as durable
  facts (see Do NOT write #1). If you find one already sitting in a notes
  file, delete it — even if you didn't write it.

## The only thing worth writing

A **transferable learning**: something a DIFFERENT future task would need to
know to succeed on the first try. Concretely:
- A URL, selector, or page path not already in any catalog file or skill
- A non-obvious UI interaction (hover vs click, hidden column, multi-step
  switcher, modal that blocks navigation)
- A gotcha discovered through a failed attempt (wrong flag, case-sensitive
  search, element indices changing, auth edge case)
- A business rule the user stated (shipping cutoffs, brand ownership,
  piggyback-seller relationships, account mappings)
- A corrected fact that overrides outdated knowledge (URL moved, selector
  renamed, workflow changed)
- A browser navigation path that required **3+ attempts** to find a button,
  link, or element — if you retried, the current skill/knowledge is missing
  something that future tasks will also need

## Do NOT write (hard rules)

1. **Task data / results.** Invoice numbers, ACOS percentages, spend totals,
   SKU counts, campaign names, dates of specific reports you pulled, tables
   of metrics you extracted. These belong in the task output, not in notes.
   If a future task needs this data it will re-query the source of truth.
2. **Anything already covered by a skill or knowledge file.** Before writing
   a single line, grep the relevant skill's SKILL.md and the catalog files
   for the same URL / selector / step. If it's there, do not restate it.
   Skills already document URLs, report types, CSV schemas, hover navigation,
   download behavior, etc. — do not duplicate.
3. **Restatements of store metadata.** "Ziniao required for amazon.<tld>",
   "platform is Amazon <country>" — already in `browser-routing.md` and
   `metadata.json`. Do not repeat in notes.md.
4. **Section headers labeled by date or task** (e.g. "ACOS Summary Apr 3-9").
   Notes are organized by topic, not chronology.
5. **Prose restating what you just did.** Reflection is not a diary.

## The filter — apply BEFORE writing each line

Ask, in order:
1. Is this a *procedure* or a *data point*? If data → drop.
2. Would a future UNRELATED task benefit from knowing this? If no → drop.
3. Is it already in a skill, L1 file, L2 file, or L3 file I read this task?
   If yes → drop (or UPDATE the existing file if your version is more accurate).
4. Can a future agent derive it by reading the existing skill/knowledge and
   running one `browser-use` page check (e.g. `page_info()`)? If yes → drop.

If a candidate learning survives all four, write it. Otherwise don't.

## Where to write (tier rules)

- **L1** (`knowledge/project/`) — READ-ONLY. Never create, edit, or delete.
- **L2** (`knowledge/notes.md`, or additional flat files like
  `knowledge/brand-x-stores.md`) — cross-store facts (shared brand, shared
  supplier, piggyback-seller relationships, cross-account rules).
- **L3** (`stores/<slug>/...`) — store-specific facts. Use
  `stores/<slug>/<platform>/<COUNTRY>/notes.md` for platform+country
  specifics; `stores/<slug>/notes.md` for store-wide facts that apply across
  platforms/countries. New topic files (e.g. `browser-tips.md`,
  `fbn-quirks.md`, `cases-process.md`) are fine when an existing file
  doesn't fit — see the naming convention below.

### L3 naming convention (knowledge vs task output)

L3 is for **knowledge** — procedural, transferable, organized by topic.
It is NOT for per-run task output (audit reports, improvement plans,
captured metric tables). Keep them separate by filename:

| Use | Naming | Examples |
|---|---|---|
| **Knowledge** (write here at L3) | lowercase, topic-named, no dates | `notes.md`, `browser-tips.md`, `fbn-quirks.md`, `cases-process.md` |
| **Per-run output** (NOT here — write to your CWD instead) | ALL_CAPS, dated, report-y | `*_PLAN_*.md`, `*_REPORT_*.md`, `*_AUDIT_*.md`, `*_2026-04-29.md` |

If you find yourself wanting to save `NOON_AE_PLAN_<date>.md` or
similar, that's task output — write it to your CWD where the user
will see it as the deliverable, not under `stores/<slug>/`. The
reflection rule "task data / results belong in the task output" (above)
is what enforces this; the naming convention is how to recognize the
boundary.
- New reusable *procedure* (parser, workflow) → new skill in
  `.claude/skills/<name>/` with SKILL.md + script + sample data + test.
  This works only for *user-authored* skills (the ones the user created in
  this workspace). Built-in skills (`amazon-ads`, `amazon-reports`,
  `noon-seller`, `new-product-launch`, `browser-use`, etc. — anything
  shipped with vibe-seller) are **copied** into your task workspace at
  start and not synced back. Editing them appears to succeed but the
  change is lost when the workspace is cleaned up — you cannot durably
  update a built-in skill from a task. If the lesson genuinely belongs in
  a built-in skill, write it to `stores/<slug>/notes.md` under a
  `## Skill follow-ups` heading instead, with: (a) which skill + section
  the change should go in, (b) the proposed wording, (c) the evidence
  (the failure or detour you observed). Mention in your final assistant
  message that the human will need to merge it into the public skill.
- Existing skill wrong or outdated → for user-authored skills, UPDATE
  it in place. For built-in skills, follow the same notes-then-merge
  path as above.

**Always prefer updating an existing file over creating a new one.** If a
fact conflicts with an existing entry, overwrite in place — never append
duplicates.

## Failed attempts are the highest-value source

Scan the conversation for:
- Tool calls with `is_error: true`
- Any command you retried with different args
- Any Read that failed, then you searched for the correct path
- Any CLI that returned a "usage:" error

For each, ask the filter above. If the lesson passes, add it as a one-line
bullet under a `## Gotchas` or `## Tips` section in the relevant existing
file — do not create a new file for a single gotcha.

## Store metadata (platforms → countries)

Read `stores/<slug>/metadata.json` (create `{"platforms": {}}` if missing).
If this task used a platform+country combo not listed, append it.
Format: `{"platforms": {"amazon": ["US", "UK"], "noon": ["EG"]}}`.
Lowercase platforms, uppercase country codes. Never remove existing entries.

## Environment

Shared Python venv: `~/.vibe-seller/.venv/`
Install: `~/.vibe-seller/.venv/bin/uv pip install <pkg>` (fallback: `pip`)
Run: `~/.vibe-seller/.venv/bin/python <script>`

Use TodoWrite to show "Updating knowledge..." while doing this. If after
applying the filter you have nothing to write, say so and stop.
