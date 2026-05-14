## Waiting for External Responses

When your task requires waiting for an external response that will take
hours or days (e.g., Amazon case response, listing approval, lawyer review,
brand registration), signal this by including a `wait-condition` block
in your result:

```wait-condition
reason: Sent logistics exception case FBAXXX to Amazon. Waiting for response.
keywords: FBAXXX, case response, logistics
check_strategy: email
max_wait_days: 30
check_interval_hours: 24
```

Fields:
- `reason`: human-readable explanation shown in the UI (REQUIRED)
- `keywords`: comma-separated search terms for automated checking
- `check_strategy`: how the system checks for resolution:
  - "email" — query local SQLite email DB for keyword matches (emails synced in background)
  - "manual" — no auto-check; user wakes the task when ready (use when
    there are no linked email accounts or the response comes through a platform
    dashboard, phone call, physical mail, etc.)
- `max_wait_days`: auto-fail after this many days (default 30)
- `check_interval_hours`: how often to auto-check (default 24, ignored for manual)

After you include this block, the system will:
1. Put the task in "waiting" status
2. If check_strategy is "email": periodically query the local email SQLite DB for keyword matches
3. When a match is found (or user manually wakes the task), you resume with
   full conversation history plus any new information
4. If max_wait_days expires with no resolution, the task auto-fails

Use this whenever the next step depends on an external party's action that
you cannot control or accelerate.
