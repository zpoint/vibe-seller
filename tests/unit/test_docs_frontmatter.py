"""Every docs page's front matter must be valid YAML.

MkDocs' meta parser tolerates an unquoted ``: `` inside a value, but
GitHub renders the same page with a strict YAML parser and shows
"Error in user YAML" above the content. The strict mkdocs build cannot
catch that, so this test parses each block the way GitHub does.
"""

from pathlib import Path

import pytest
import yaml

DOCS = Path(__file__).resolve().parents[2] / 'docs'
PAGES = sorted(DOCS.rglob('*.md'))


@pytest.mark.unit
@pytest.mark.parametrize('page', PAGES, ids=lambda p: str(p.relative_to(DOCS)))
def test_front_matter_is_valid_yaml(page):
    text = page.read_text(encoding='utf-8')
    if not text.startswith('---\n'):
        return
    block = text.split('---', 2)[1]
    meta = yaml.safe_load(block)
    assert isinstance(meta, dict), f'{page}: front matter is not a mapping'
