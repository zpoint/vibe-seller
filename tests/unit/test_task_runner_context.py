"""Locale-independence of the prompt-assembly file reads.

The server-side reads of workspace knowledge files must name their
encoding: ``read_text()`` without one decodes with the process locale
codec, which on native Windows (zh-CN) is GBK. A store task on such a
machine died at prompt assembly with
``UnicodeDecodeError: 'gbk' codec can't decode byte ...`` inside
``_get_platform_sites`` (2026-11-27, native-Windows AdsPower run).
"""

import json
import os
from pathlib import Path
import subprocess
import sys

os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-testing-only')

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]

# A *-sites.md row with non-ASCII text: valid UTF-8, but not
# decodable by a non-UTF-8 locale codec (the GBK-on-Windows class).
_SITES_MD = (
    '| Demo platform 美国站 | US | `https://seller.example.com` |\n'
    '| Demo platform 德国站 | DE | `https://seller.example.de` |\n'
)


def test_platform_sites_reads_utf8_under_locale_codec(tmp_path):
    """_get_platform_sites parses UTF-8 knowledge files whatever the
    process locale codec is.

    The default codec is chosen by the C io layer at interpreter
    startup, so patching ``locale`` in-process has no effect; the test
    runs a subprocess under the C locale (ASCII codec — the same
    "locale codec is not UTF-8" class as GBK, with PEP 538 coercion
    and UTF-8 mode disabled). Pre-fix, that subprocess dies with the
    same ``UnicodeDecodeError`` the native-Windows server logged.
    """
    knowledge = tmp_path / 'knowledge' / 'project' / 'common'
    knowledge.mkdir(parents=True)
    (knowledge / 'demo-sites.md').write_text(_SITES_MD, encoding='utf-8')
    code = '\n'.join([
        'import json',
        'from pathlib import Path',
        'import app.task_runner_context as trc',
        f'trc.VIBE_SELLER_DIR = Path({str(tmp_path)!r})',
        "print(json.dumps(trc._get_platform_sites().get('demo')))",
    ])
    env = {
        **os.environ,
        'VIBE_HOME': str(tmp_path),
        'PYTHONPATH': str(_REPO_ROOT),
        'LC_ALL': 'C',
        'LANG': 'C',
        'PYTHONCOERCECLOCALE': '0',
        'PYTHONUTF8': '0',
        # stdio only; does not affect read_text()'s default codec.
        'PYTHONIOENCODING': 'utf-8',
    }
    proc = subprocess.run(
        [sys.executable, '-c', code],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, f'subprocess failed:\n{proc.stderr}'
    parsed = json.loads(proc.stdout.strip().splitlines()[-1])
    assert parsed == {
        'US': 'https://seller.example.com',
        'DE': 'https://seller.example.de',
    }
