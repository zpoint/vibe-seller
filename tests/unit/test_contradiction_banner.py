"""The unresolved-problem banner names no cause of its own.

It once carried the explanation of one check (search-term spend above
targeting spend) and printed it over every gap — observed on a bid
applied at a different value than approved, where it told the user the
campaign's figures came from the wrong campaign.
"""

import pytest

from app.ai.stop_gates import contradiction_banner

pytestmark = pytest.mark.unit


def test_the_banner_lists_each_problem_and_explains_none():
    gap = 'A1234567 / widget red (exact): bid set to 1.35, approved 1.32'
    banner = contradiction_banner([gap])
    assert f'- {gap}' in banner
    assert '搜索词' not in banner
    assert 'search-term' not in banner.lower()
