"""noon sign-in has one home: the ``noon-shared`` skill.

Knowledge every task reads (``app/knowledge``, read first by the base
prompt's knowledge recall) used to repeat the login host and the whole
OTP flow. An agent that read that copy answered with the bare host and
never loaded the skill (nightly e2e, test_skill_prereq_hook), and a copy
can drift from the skill the sign-in script actually follows. Knowledge
points at the skill instead; this keeps it that way.
"""

from pathlib import Path
import re
import urllib.parse

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_SIGNIN = _ROOT / 'app/skills_v2/noon-shared/scripts/bh_noon_signin.py'


def _login_host() -> str:
    m = re.search(r"^LOGIN = '([^']+)'", _SIGNIN.read_text(), re.M)
    assert m, f'no LOGIN constant in {_SIGNIN}'
    return urllib.parse.urlsplit(m.group(1)).hostname


def test_knowledge_never_states_the_noon_login_address():
    host = _login_host()
    hits = [
        f'{path.relative_to(_ROOT)}:{n}'
        for path in sorted((_ROOT / 'app/knowledge').rglob('*.md'))
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if host in line
    ]
    assert not hits, (
        f'{host} is stated outside the noon-shared skill — point at the '
        f'skill (§ 1) instead: {hits}'
    )
