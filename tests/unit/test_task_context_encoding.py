"""Knowledge files must parse on every locale (found live on Windows).

`_get_platform_sites` reads *-sites.md with the platform default codec
when no encoding is passed — on a Chinese Windows box that is GBK, and
one UTF-8 smart quote in a sites file crashed EVERY store task's
context build (C1 integration smoke, native Windows).
"""

from pathlib import Path

from app import task_runner_context as ctx


def test_platform_sites_reads_utf8_regardless_of_locale(
    tmp_path: Path, monkeypatch
):
    knowledge = tmp_path / 'knowledge' / 'project' / 'common'
    knowledge.mkdir(parents=True)
    # A smart quote + CJK text: pure UTF-8, fatal under GBK.
    (knowledge / 'amazon-sites.md').write_text(
        '| 美国 | US | `https://example.com/us“x` |\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(ctx, 'VIBE_SELLER_DIR', tmp_path)
    ctx._get_platform_sites.cache_clear()
    sites = ctx._get_platform_sites()
    assert sites['amazon']['US'] == 'https://example.com/us“x'
    ctx._get_platform_sites.cache_clear()
