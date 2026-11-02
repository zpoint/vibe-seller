"""The catalog-sync e2e checks catalog-first by what it means.

It used to cap TOTAL turns at 8. In CI that passed a GLM run whose first
move was `grep -r secret knowledge/` (refused by the server's catalog
guard) and failed MiniMax runs that went catalog -> file directly, then
re-checked their answer and narrated between calls. The check is now
"after reading the catalog, the agent opens the secret's file without
searching". Each case below is the shape of a real run.
"""

import json

import pytest

from tests.e2e.test_catalog_sync import (
    MAX_STEPS_CATALOG_TO_SECRET,
    catalog_to_secret_gap,
)

pytestmark = pytest.mark.unit

SECRET_FILE = 'amazon/PL-0000example.md'
CATALOG = 'stores/acme/CATALOG.md'


def _tool(tool, **inp):
    return {
        'role': 'tool_use',
        'content': json.dumps({'tool': tool, 'input': inp}),
    }


def _say(text):
    return {'role': 'assistant', 'content': text}


def _read(path):
    return _tool('Read', file_path=f'/tasks/t1/{path}')


def _bash(cmd):
    return _tool('Bash', command=cmd)


def _ok(messages):
    gap = catalog_to_secret_gap(messages, SECRET_FILE)
    assert gap is not None
    searches, steps = gap
    return not searches and steps <= MAX_STEPS_CATALOG_TO_SECRET


def test_glm_run_whose_grep_the_guard_refused_passes():
    run = [
        _bash('grep -rni "secret" stores/acme/ knowledge/ -l'),  # refused
        _read(CATALOG),
        _read(f'knowledge/{SECRET_FILE}'),
        _say('VERIFY-CATALOG-0000-0000'),
        _tool('mcp__vibe-seller__vibe_seller_set_task_result', result='x'),
    ]
    assert _ok(run)


def test_chatty_minimax_run_that_rechecks_its_answer_passes():
    run = [
        _say("I'll look for the SECRET value."),
        _bash('ls -la . && ls -la stores/'),
        _read(CATALOG),
        _say('The catalog points to a verification file.'),
        _read(f'knowledge/{SECRET_FILE}'),
        _bash('for f in STORE.md notes.md; do cat "$f"; done'),
        _bash('grep -rn "SECRET" knowledge/ stores/'),
        _tool('mcp__vibe-seller__vibe_seller_set_task_result', result='x'),
        _say('VERIFY-CATALOG-0000-0000'),
    ]
    assert _ok(run)


def test_a_store_profile_read_on_the_way_passes():
    run = [
        _read(CATALOG),
        _bash('ls stores/acme/'),
        _read(f'knowledge/{SECRET_FILE}'),
    ]
    assert _ok(run)


def test_a_stubbed_catalog_that_sends_the_agent_searching_fails():
    """The regression the old cap was written for: the catalog row said
    "Empty/stub", so after reading it the agent had to search."""
    run = [
        _read(CATALOG),
        _bash('grep -rn "SECRET" knowledge/'),
        _read(f'knowledge/{SECRET_FILE}'),
    ]
    searches, _ = catalog_to_secret_gap(run, SECRET_FILE)
    assert searches


def test_reading_file_after_file_instead_of_following_the_catalog_fails():
    run = [
        _read(CATALOG),
        _read('knowledge/amazon/a.md'),
        _read('knowledge/amazon/b.md'),
        _read('knowledge/amazon/c.md'),
        _read(f'knowledge/{SECRET_FILE}'),
    ]
    assert not _ok(run)


def test_never_reading_the_catalog_is_reported():
    run = [_read(f'knowledge/{SECRET_FILE}')]
    assert catalog_to_secret_gap(run, SECRET_FILE) is None
