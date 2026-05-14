# Prompt Template Placeholders

Prompt templates in `app/prompts/*.md` may contain placeholder markers
replaced at runtime by `_build_system_extra()` in `app/task_runner.py`.

## Current placeholders

| Placeholder | Replaced with | Used in | When |
|---|---|---|---|
| `<slug>` | Store directory slug (e.g. `acme-test`) | `design_system.md`, `reflection.md` | Every store-scoped task |
| `{workspace_guidance}` | `CATALOG_RESTRICTION_PROMPT_L2/L3` for catalog sync, empty for regular tasks | `design_system.md` | Catalog sync tasks only |

## Where prompts are assembled

Single function: `_build_system_extra()` in `app/task_runner.py`.
All task-launching paths call this one function — no inline prompt assembly.
See `TaskHeader` enum for the list of task types.

## Adding a new placeholder

1. Add the placeholder marker in the relevant `.md` template.
2. Add the replacement logic inside `_build_system_extra()`.
3. Document it in the table above.
4. Add a test case in `tests/unit/test_prompt_assembly.py`.
