---
expect:
  task_id: string
---

{
  "id": "task-00000000-0000-4000-8000-000000000001",
  "status": "completed",
  "plan": null,
  "result": "Top sellers, last 7 days (store acme, US):\n- widget-006: 42 units, 1200.00 revenue\n- widget-011: 17 units, 510.00 revenue\nTotal: 59 units, 1710.00 revenue.",
  "error": null,
  "pending_question": null,
  "steps": [
    {"n": 1, "summary": "Opened the orders report"},
    {"n": 2, "summary": "Exported and grouped by item"}
  ]
}
