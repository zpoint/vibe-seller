---
type: regex
pattern: create_task
target: mock_calls
---

The run must have created a task on the acme store — the skill's whole
purpose is to start a task rather than answer from memory. The
`create_task` mock aborts the run if the description is missing or the
store id is not one `list_stores` returned.
