"""Unit tests for the workplace-CLI framework (``app.cli_integrations``).

Platform-independent pieces: checksums, safe archive reading, the
umbrella skill, the on-disk state that decides "enabled", and the PATH
an agent gets for any combination of enabled platforms.
"""

import ast
import base64
import hashlib
import io
import os
from pathlib import Path
import tarfile
import urllib.request
import zipfile

import pytest

from app.ai.claude_backend_utils import apply_agent_venv_path
from app.cli_integrations import download, paths, registry, skills
from app.cli_integrations.registry import INTEGRATIONS
from tests.fixtures.fake_cli_integration import SKILLS, FakeIntegration

pytestmark = pytest.mark.unit


# ── checksums ──────────────────────────────────────────


def test_sha256_verifier_accepts_and_rejects():
    data = b'binary bytes'
    ok = download.sha256_verifier(hashlib.sha256(data).hexdigest().upper())
    ok(data)
    with pytest.raises(download.IntegrityError):
        ok(data + b'!')


def test_sri_verifier_accepts_npm_integrity_and_rejects_tampering():
    data = b'tarball'
    sri = 'sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode()
    verify = download.sri_verifier(sri)
    verify(data)
    with pytest.raises(download.IntegrityError):
        verify(b'tarbalL')
    with pytest.raises(download.IntegrityError):
        download.sri_verifier('md5-abc')


def test_parse_checksums_reads_sha256sum_lines():
    text = (
        f'{"a" * 64}  tool-1.0-linux-amd64.tar.gz\n'
        f'{"B" * 64} *tool-1.0-windows-amd64.zip\n'
        'garbage line\n'
    )
    assert download.parse_checksums(text) == {
        'tool-1.0-linux-amd64.tar.gz': 'a' * 64,
        'tool-1.0-windows-amd64.zip': 'b' * 64,
    }


# ── archives ───────────────────────────────────────────


def _tar(entries) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tf:
        for info, body in entries:
            tf.addfile(info, io.BytesIO(body) if body is not None else None)
    return buf.getvalue()


def _file(name: str, body: bytes) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(body)
    return info, body


def test_iter_archive_tar_skips_escapes_and_links():
    link = tarfile.TarInfo('pkg/evil-link')
    link.type = tarfile.SYMTYPE
    link.linkname = '/etc/passwd'
    data = _tar([
        _file('pkg/bin/tool', b'ELF'),
        _file('../outside', b'x'),
        _file('/abs/path', b'x'),
        (link, None),
    ])
    assert dict(download.iter_archive(data)) == {'pkg/bin/tool': b'ELF'}


def test_iter_archive_zip_skips_escapes_and_symlinks():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('pkg/tool.exe', b'MZ')
        zf.writestr('../escape.txt', b'x')
        zf.writestr('C:/windows/x', b'x')
        sym = zipfile.ZipInfo('pkg/link')
        sym.external_attr = 0o120777 << 16
        zf.writestr(sym, 'target')
    assert dict(download.iter_archive(buf.getvalue())) == {
        'pkg/tool.exe': b'MZ'
    }


def test_archive_member_and_non_archive():
    data = _tar([_file('a/readme', b'r'), _file('a/bin/tool', b'T')])
    assert download.archive_member(data, lambda n: n.endswith('/tool')) == b'T'
    with pytest.raises(download.DownloadError):
        download.archive_member(data, lambda n: n.endswith('/nope'))
    with pytest.raises(download.DownloadError):
        list(download.iter_archive(b'not an archive at all'))


def test_fetch_tries_candidates_in_order_and_skips_bad_bytes(tmp_path):
    good = tmp_path / 'good.bin'
    good.write_bytes(b'right')
    wrong = tmp_path / 'wrong.bin'
    wrong.write_bytes(b'wrong')
    verify = download.sha256_verifier(hashlib.sha256(b'right').hexdigest())
    data, url = download.fetch(
        [
            (tmp_path / 'missing.bin').as_uri(),  # unreachable
            wrong.as_uri(),  # answers, but wrong bytes
            good.as_uri(),
        ],
        verify=verify,
    )
    assert (data, url) == (b'right', good.as_uri())
    with pytest.raises(download.DownloadError) as e:
        download.fetch([wrong.as_uri()], verify=verify)
    assert 'wrong.bin' in str(e.value)


# ── umbrella skill ─────────────────────────────────────


@pytest.fixture
def fake(tmp_path) -> FakeIntegration:
    return FakeIntegration('fakecli', tmp_path)


def _subs(fake):
    return skills.select(
        download.iter_archive(fake.skills.read_bytes()), fake.layout
    )


def test_select_keeps_prefixed_skill_dirs_only(fake):
    subs = _subs(fake)
    # fake-internal excluded, other/ unprefixed, fake-nodoc has no SKILL.md
    assert sorted(subs) == ['calendar', 'shared']
    assert sorted(subs['calendar']) == ['SKILL.md', 'references/agenda.md']


def test_rewrite_refs_only_touches_relative_sibling_paths():
    text = 'see ../fake-shared/SKILL.md; the fake-shared skill; ./fake-x/a'
    assert skills.rewrite_refs(text, 'fake-') == (
        'see ../shared/SKILL.md; the fake-shared skill; ./fake-x/a'
    )


def test_install_builds_umbrella_with_rules_and_catalog(fake, tmp_path):
    home = tmp_path / 'home'
    names = skills.install(fake, '1.2.3', _subs(fake), home)
    assert names == ['calendar', 'shared']
    root = paths.skill_dir('fakecli', home)
    umbrella = (root / 'SKILL.md').read_text(encoding='utf-8')
    meta = skills.frontmatter(umbrella)
    assert meta['name'] == 'fakecli'
    assert meta['description'] == fake.description
    # The agent is told never to log in itself, and what to do instead.
    assert '`fakecli config init`' in umbrella
    assert '`fakecli auth login`' in umbrella
    assert 'Settings → Integrations' in umbrella
    # Catalog rows point at files that exist; '|' in a description
    # cannot break the table.
    assert '`./calendar/SKILL.md`' in umbrella
    assert 'Auth, flags / output.' in umbrella
    for name in names:
        assert (root / name / 'SKILL.md').is_file()
    cal = (root / 'calendar' / 'SKILL.md').read_text(encoding='utf-8')
    assert '../shared/SKILL.md' in cal
    assert 'The fake-shared skill is prose.' in cal
    ref = (root / 'calendar' / 'references' / 'agenda.md').read_text(
        encoding='utf-8'
    )
    assert ref == 'See ../shared/SKILL.md and ../calendar/SKILL.md\n'


def test_install_swaps_atomically_and_leaves_nothing_in_skills_dir(
    fake, tmp_path
):
    home = tmp_path / 'home'
    skills.install(fake, '1.2.3', _subs(fake), home)
    stale = paths.skill_dir('fakecli', home) / 'stale.txt'
    stale.write_text('old')
    fake.publish('1.3.0')
    skills.install(fake, '1.3.0', _subs(fake), home)
    root = paths.skill_dir('fakecli', home)
    assert not stale.exists()
    assert 'version 1.3.0' in (root / 'calendar' / 'SKILL.md').read_text(
        encoding='utf-8'
    )
    assert '`fakecli` 1.3.0' in (root / 'SKILL.md').read_text(encoding='utf-8')
    # No staging / backup ever lands where task workspaces copy from.
    assert sorted(p.name for p in paths.skills_root(home).iterdir()) == [
        'fakecli'
    ]
    staging = paths.skills_root(home).parent / '.sync-staging'
    assert list(staging.iterdir()) == []


def test_install_refuses_an_archive_without_skills(fake, tmp_path):
    with pytest.raises(ValueError):
        skills.install(fake, '1.2.3', {}, tmp_path / 'home')


def test_uninstall_is_idempotent(fake, tmp_path):
    home = tmp_path / 'home'
    skills.install(fake, '1.2.3', _subs(fake), home)
    assert skills.uninstall(fake, home) is True
    assert not paths.skill_dir('fakecli', home).exists()
    assert skills.uninstall(fake, home) is False


# ── enabled = binary + umbrella, and nothing else ──────


def _resolve(name: str, parts: list[str]) -> Path | None:
    """The file a shell (Git Bash on Windows) runs for bare *name*."""
    for d in parts:
        for candidate in (name, f'{name}.exe'):
            if (Path(d) / candidate).is_file():
                return Path(d) / candidate
    return None


def _plant(home: Path, key: str, binary: str, *, skill: bool) -> None:
    paths.tool_dir(key, home).mkdir(parents=True, exist_ok=True)
    paths.write_manifest(key, {'version': '1.0.0'}, home)
    exe = paths.binary_path(key, binary, home)
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b'')
    if skill:
        d = paths.skill_dir(key, home)
        d.mkdir(parents=True, exist_ok=True)
        (d / 'SKILL.md').write_text('---\nname: x\n---\n')


@pytest.mark.parametrize(
    'enabled_keys',
    [
        (),
        ('wecom',),
        ('feishu',),
        ('dingtalk',),
        ('wecom', 'dingtalk'),
        ('wecom', 'feishu', 'dingtalk'),
    ],
)
def test_agent_path_carries_exactly_the_enabled_clis(tmp_path, enabled_keys):
    """One, two or three platforms enabled → exactly those bin dirs on
    the agent's PATH (and their env switches); an installed-but-not-
    enabled CLI and a stray skill without a binary stay off."""
    home = tmp_path / 'home'
    for key, integ in INTEGRATIONS.items():
        _plant(home, key, integ.binary, skill=key in enabled_keys)
    # A skill dir whose binary was removed is not "enabled".
    paths.skill_dir('ghost', home).mkdir(parents=True)
    assert [i.key for i in registry.enabled(home)] == [
        k for k in INTEGRATIONS if k in enabled_keys
    ]
    # The machine also has personal installs of all three (npm -g), on
    # the PATH the server inherited — signed in as somebody personally.
    personal = tmp_path / 'inherited'
    personal.mkdir()
    for integ in INTEGRATIONS.values():
        (personal / integ.binary).write_text('personal')
    inherited = str(personal)
    env = {'PATH': inherited}
    apply_agent_venv_path(env, None, vibe_home=home, server_bin=tmp_path)
    parts = env['PATH'].split(os.pathsep)
    # What a bare `<binary>` runs: the managed build when enabled, the
    # erroring guard when not — never the personal install.
    for key, integ in INTEGRATIONS.items():
        hit = _resolve(integ.binary, parts)
        if key in enabled_keys:
            assert hit == paths.binary_path(key, integ.binary, home), key
        else:
            assert hit is not None and hit.parent.name == '_guard', (key, hit)
            assert 'not enabled for agents' in hit.read_text(encoding='utf-8')
    for key in INTEGRATIONS:
        bin_dir = str(paths.bin_dir(key, home))
        assert (bin_dir in parts) is (key in enabled_keys), key
    # Above the inherited PATH, so a user's own npm install never wins.
    for key in enabled_keys:
        assert parts.index(str(paths.bin_dir(key, home))) < parts.index(
            inherited
        )
    want_env: dict = {}
    for key in enabled_keys:
        want_env.update(INTEGRATIONS[key].env(home))
    for var, value in want_env.items():
        assert env[var] == value


def test_skills_fixture_is_what_the_tests_assume():
    assert 'fake-shared/SKILL.md' in SKILLS


def test_fetch_goes_through_the_proxy_aware_urlopen(tmp_path, monkeypatch):
    """Every candidate is requested via ``system_proxy.urlopen`` — the
    path that picks the exported proxy, the OS proxy or direct by what
    actually reaches the host — never a bare urllib call."""
    seen = []
    real = urllib.request.urlopen

    def spy(request, timeout):
        seen.append(request.full_url)
        return real(request, timeout=timeout)

    monkeypatch.setattr(download.system_proxy, 'urlopen', spy)
    good = tmp_path / 'good.bin'
    good.write_bytes(b'x')
    missing = (tmp_path / 'missing.bin').as_uri()
    assert download.fetch([missing, good.as_uri()])[0] == b'x'
    assert seen == [missing, good.as_uri()]


def test_cli_output_is_never_decoded_with_the_locale_codec():
    """The CLIs print UTF-8. A ``text=True`` subprocess decodes with the
    locale codec — GBK on a Chinese Windows, where ``dws --version``
    then raised UnicodeDecodeError and DingTalk could not be installed.
    Every text-mode subprocess call in the package names its encoding."""
    root = Path(download.__file__).parent
    offenders = []
    for py in sorted(root.glob('*.py')):
        tree = ast.parse(py.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            kw = {k.arg for k in node.keywords}
            if ('text' in kw or 'universal_newlines' in kw) and (
                'encoding' not in kw
            ):
                offenders.append(f'{py.name}:{node.lineno}')
    assert not offenders, offenders
