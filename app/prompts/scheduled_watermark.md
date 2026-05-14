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
| Daily email sweep | `email_watermark` | The integer in the `epoch` column of the newest row your SELECT returned (you should have projected `CAST(strftime('%s', date) AS INTEGER) AS epoch` on purpose — do NOT compute epoch mentally from the ISO date, that loses the year). Unix epoch seconds as an integer string, e.g. `"1776441057"`. NOT an ISO timestamp; the server rejects ISO for this key because lex comparison under tz/microseconds is unsafe. |
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
