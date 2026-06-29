## Scheduled task — cross-run state

You are executing as a scheduled task. Other runs of the same
schedule may have already persisted a cursor for you to resume
from. **Before doing any work, check the cursor.**

### The tools

- `vibe_seller_get_new_emails()` — **the email sweep shortcut.** For a
  "new messages since last run" email task, call this instead of
  reading + filtering the email DB by hand: the server reads your
  `email_watermark` cursor, returns only newer emails (with bodies and
  a precomputed `epoch`) plus a ready-to-persist `next_watermark`, and
  never lets already-processed emails into your context. After
  reporting the bodies, persist `next_watermark` via
  `set_schedule_state` below.
- `vibe_seller_get_schedule_state(key)` — read the value a prior
  run wrote. Returns an object with `value` (string or null) plus
  metadata (`key`, `updated_at`, `updated_by_task_id`). A `value`
  of `null` means you are the first run (or nobody has written
  this key yet).
- `vibe_seller_set_schedule_state(key, value)` — persist a
  cursor for the next run. `value` must be a non-empty string.
  Never call with `null` or `""`; if you have nothing concrete
  to save, do not call this tool.

Scope is resolved server-side from your current task. You never
pass a `schedule_id`. Both tools 400 on non-scheduled tasks.

### Canonical keys — use these names

Agents inventing ad-hoc key names across runs will read back
`null` and re-process everything. Pick from this table whenever
your workflow matches, so a follow-up run finds the same slot.

| Workflow | key | value format | How to use it |
|---|---|---|---|
| Daily email sweep (new messages since last run) | `email_watermark` | Unix epoch seconds as an integer string, e.g. `"1776441057"`. NOT an ISO timestamp — the server rejects ISO here because lex string comparison is unsafe under tz/microseconds differences. | **Call `vibe_seller_get_new_emails` — one call.** The server reads this cursor, returns ONLY emails newer than it (with `body_text` and a precomputed `epoch`), plus `next_watermark`. Report each returned body, then `set_schedule_state('email_watermark', <next_watermark, verbatim>)`. **Do NOT hand-write a raw sqlite `SELECT` for this sweep** — an unfiltered query pulls already-processed emails into your context and leaks them into this run, and computing the epoch yourself risks a year-off cursor. First run (cursor null) returns the last 24 h automatically. |
| Order audit / fulfilment | `last_order_id` | Order id as string, e.g. `"ORD-42019"` or `"9fba-..."` | Fetch orders with id > watermark (if sortable) OR fetch recent orders and filter to those not seen. Set to the highest id you processed. |
| Forum / messaging / chat monitoring | `last_message_id` | Channel-specific message id, e.g. `"m_0193a..."` | Query messages after the id. Set to the newest message id you handled. |
| Product / catalog sync | `last_sku_batch_id` | Batch or export id, e.g. `"batch-20260417-02"` | Process batches after this one. Set to the newest batch id fully applied. |
| Analytics / reporting backfill | `last_report_date` | ISO date `YYYY-MM-DD`, e.g. `"2026-04-16"` | Pull reports for dates strictly greater than the watermark. Set to the most recent date you finished. |

### Rules of thumb

- **One cursor = one run-to-run contract.** Use the same `key`
  across every run of this schedule. Inventing a fresh key each
  run means the next run starts from scratch.
- **If `get` returns `value: null` but `other_known_keys` is
  non-empty, you probably hallucinated the key.** Re-issue the
  `get` call using one of the names in `other_known_keys` before
  treating this as a first run. A populated `other_known_keys`
  with your chosen key ABSENT is a strong signal of a naming
  drift like `email_watermark` → `last_email_watermark`.
- **Only advance the cursor after successful processing.** If
  your run partially fails, leave the cursor alone so the next
  run retries the same items.
- **Write the cursor after each completed batch**, not only at
  the end, so a mid-run crash forfeits at most one batch.
- **On first run (`value: null`)**, pick a safe default — e.g.
  "24 hours ago" for email sweeps, "batches from the last 7
  days" for product sync. Don't refuse to run.
- **None of these match?** Coin a new key using the same style
  (lower_snake_case, ≤64 chars, `[A-Za-z0-9_.-]`), and keep
  using it on every future run of this schedule.
