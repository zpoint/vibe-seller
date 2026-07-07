## Hand off to the next scheduled run

You were launched by a schedule, so a follow-up run will happen.
If you consumed a cursor-like resource (emails, orders, messages,
anything ordered by time or id), persist a watermark now so the
next run can resume cleanly.

### Pick the same `key` you read at the start

Use the canonical key that matches your workflow (the same table
you used with `vibe_seller_get_schedule_state`):

| Workflow | key | value to write |
|---|---|---|
| Daily email sweep | `email_watermark` | The **`next_watermark`** returned by `vibe_seller_get_new_emails`, written **verbatim**. That is the only accepted source: the server records it as a floor and **rejects** any `email_watermark` you set by hand, from an email `Date` header, or below that floor (a store with linked email accounts also rejects the write entirely until you have called `get_new_emails`). Unix epoch seconds as an integer string, e.g. `"1776441057"` — never an ISO timestamp, and never an epoch you computed yourself from a date. |
| Order audit | `last_order_id` | highest order id you processed |
| Messaging / chat | `last_message_id` | newest message id handled |
| Product sync | `last_sku_batch_id` | newest batch id fully applied |
| Analytics backfill | `last_report_date` | latest `YYYY-MM-DD` you finished |

### How to call the tool

- `vibe_seller_set_schedule_state(key=<canonical key>, value=<concrete string>)`.
- `value` MUST be a non-empty string. Never pass `null` or `""`.
  If you have nothing concrete to save (no rows processed, run
  bailed out, etc.), skip the tool entirely — it is safe to
  leave the cursor unchanged.
- Prefer writing the watermark AFTER each successful batch rather
  than only at the end — a mid-run crash otherwise forces the
  next run to re-process everything from the last checkpoint.

Skip this step entirely if the task did not consume any ordered
resource.
