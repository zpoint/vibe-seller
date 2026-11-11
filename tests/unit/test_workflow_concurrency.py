"""A reusable workflow's concurrency group names the workflow itself.

Inside a ``workflow_call``, ``github.workflow`` is the CALLER's name. The
release called two reusable gates whose groups were both
``${{ github.workflow }}-${{ github.ref }}`` — i.e. ``Release-<tag>`` —
so with ``cancel-in-progress`` the second one cancelled the first and the
release lost a required upgrade gate. Each reusable workflow keys its
group on a literal of its own.
"""

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

WORKFLOWS = Path(__file__).resolve().parents[2] / '.github' / 'workflows'


def _reusable():
    for wf in sorted(WORKFLOWS.glob('*.yml')):
        doc = yaml.safe_load(wf.read_text())
        # PyYAML reads the bare key ``on`` as True.
        triggers = doc.get('on', doc.get(True)) or {}
        if isinstance(triggers, dict) and 'workflow_call' in triggers:
            yield wf.name, doc


def test_there_are_reusable_workflows():
    assert list(_reusable())


@pytest.mark.parametrize('name,doc', list(_reusable()))
def test_a_reusable_group_does_not_borrow_the_callers_name(name, doc):
    conc = doc.get('concurrency')
    if conc is None:
        return
    group = conc['group'] if isinstance(conc, dict) else str(conc)
    assert 'github.workflow' not in group, (
        f'{name}: concurrency group {group!r} uses github.workflow, which '
        'is the CALLER inside workflow_call — two reusable gates of one '
        'release then share a group and cancel each other'
    )
