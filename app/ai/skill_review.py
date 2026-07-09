"""Parse a skill's ``review:`` Definition-of-Done block from SKILL.md.

Phase 2 generalizes the active ``ads-report-review`` reviewer (which was
hardcoded to the ads skills) into a per-skill contract. A skill opts into
the live-cross-check reviewer by declaring a ``review:`` block in its
SKILL.md frontmatter::

    ---
    name: amazon-listing
    gates: [listing_submitted]        # deterministic floor (existing)
    review:
      criteria: |
        - Every attempted SKU is actually live (real ASIN, intended
          content), and the processing report parsed to 0 blocking errors.
      evidence:
        - "*REPORT*.xlsm"
      verify_by: |
        Open Manage Inventory for each SKU and confirm it exists with the
        intended content; for a delete, confirm it is gone.
    ---

- ``gates:`` (unchanged) names the **deterministic floors** — server-side
  code that produces uncheatable denies (coverage, file-exists, 0-errors).
- ``review:`` names the **active reviewer** contract — the semantic bar an
  adversarial subagent applies by opening the live source of truth. Its
  presence is what makes the reviewer gate fire for this skill.

This module only PARSES the block; the two enforcement paths
(``routers/tasks_files.apply_report_reviewer_gate`` and
``bash_safety.check_review_status``) decide, from whether any bound skill
declares one, whether to require a reviewer verdict.

Kept separate from the pure-leaf ``skill_gate_utils`` because it uses
PyYAML (that module is deliberately stdlib-only to stay import-cycle-free).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from app.ai.skill_gate_utils import find_skill_md


@dataclass(frozen=True)
class SkillReview:
    """A skill's parsed ``review:`` contract (all fields optional)."""

    criteria: str = ''
    evidence: tuple[str, ...] = ()
    verify_by: str = ''


def _frontmatter(text: str) -> dict | None:
    """Return the YAML frontmatter mapping, or None if absent/invalid."""
    if not text.startswith('---\n'):
        return None
    end = text.find('\n---\n', 4)
    if end == -1:
        return None
    try:
        data = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def parse_skill_review(skill_md_path: Path | None) -> SkillReview | None:
    """Return the ``review:`` contract this SKILL.md declares, else None.

    None means the skill does NOT opt into the active reviewer (only its
    ``gates:`` deterministic floors, if any, apply). A present-but-empty
    ``review:`` block returns an empty ``SkillReview`` — enough to make
    the reviewer gate fire (the reviewer decides what "done" means when
    no explicit criteria are given).
    """
    if skill_md_path is None or not skill_md_path.is_file():
        return None
    try:
        text = skill_md_path.read_text(encoding='utf-8')
    except OSError:
        return None
    fm = _frontmatter(text)
    if not fm or 'review' not in fm:
        return None
    block = fm.get('review')
    # ``review:`` with no mapping under it (just the key) → opt-in, empty.
    if not isinstance(block, dict):
        return SkillReview()
    evidence = block.get('evidence') or []
    if isinstance(evidence, str):
        evidence = [evidence]
    elif not isinstance(evidence, list | tuple):
        # A mapping/other type would otherwise iterate its keys as
        # bogus globs — ignore malformed evidence rather than guess.
        evidence = []
    return SkillReview(
        criteria=str(block.get('criteria') or '').strip(),
        evidence=tuple(str(e).strip() for e in evidence if str(e).strip()),
        verify_by=str(block.get('verify_by') or '').strip(),
    )


def skills_requiring_review(
    skills: frozenset[str] | set[str],
    workspace_dir: Path,
) -> dict[str, SkillReview]:
    """Map skill name → its ``review:`` contract, for skills that declare one.

    Given the set of skills bound to a task (``recorded_skills``), returns
    only those whose SKILL.md carries a ``review:`` block. An empty result
    means no active reviewer is required for the task.
    """
    out: dict[str, SkillReview] = {}
    for skill in skills:
        review = parse_skill_review(find_skill_md(workspace_dir, skill))
        if review is not None:
            out[skill] = review
    return out
