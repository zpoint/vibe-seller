"""Unit tests for ``app.skill_fetch`` — URL shape, guards, resolution.

The pure-logic half of the URL source: what counts as a GitHub-style
tree/blob URL, and which addresses / schemes the SSRF guard refuses.
The live-fetch journey (loopback fixture serving an archive and a
fake GitHub API) is workflow-tier:
``tests/workflow/test_wf_skill_import.py``.
"""

import asyncio
import socket

import pytest

from app import skill_fetch

pytestmark = pytest.mark.unit


class TestGithubUrlParsing:
    def test_tree_url_resolves_to_repo_path(self):
        got = skill_fetch.parse_github_source(
            'https://github.com/acme/skills/tree/main/invoice-check'
        )
        assert got == {
            'api_root': 'https://api.github.com',
            'owner': 'acme',
            'repo': 'skills',
            'ref': 'main',
            'subpath': 'invoice-check',
        }

    def test_tree_url_without_path_is_repo_root(self):
        got = skill_fetch.parse_github_source(
            'https://github.com/acme/skills/tree/v2'
        )
        assert got['ref'] == 'v2'
        assert got['subpath'] == ''

    def test_blob_url_takes_the_containing_directory(self):
        got = skill_fetch.parse_github_source(
            'https://github.com/acme/skills/blob/main/tools/invoice-check/'
            'SKILL.md'
        )
        assert got['subpath'] == 'tools/invoice-check'

    def test_root_level_blob_is_the_repo_root(self):
        got = skill_fetch.parse_github_source(
            'https://github.com/acme/skills/blob/main/SKILL.md'
        )
        assert got['subpath'] == ''

    def test_non_github_shape_returns_none(self):
        assert (
            skill_fetch.parse_github_source(
                'https://files.example.com/skills/invoice-check.tar.gz'
            )
            is None
        )

    def test_non_github_host_keeps_its_own_api_root(self):
        # A loopback fixture (or any GitHub-compatible API host)
        # serves the SAME /repos/… shape on its own origin.
        got = skill_fetch.parse_github_source(
            'http://127.0.0.1:8899/acme/skills/tree/main/invoice-check'
        )
        assert got['api_root'] == 'http://127.0.0.1:8899'

    def test_https_url_on_github_shares_the_api_host(self):
        got = skill_fetch.parse_github_source(
            'https://www.github.com/acme/skills/tree/main/x'
        )
        assert got['api_root'] == 'https://api.github.com'

    def test_nonstandard_port_keeps_the_given_origin(self):
        """Real GitHub is never on a custom port: a github.com URL
        with one IS a GitHub-compatible origin (enterprise server,
        proxy, fixture). Jumping to api.github.com:443 would fetch a
        DIFFERENT machine than the user named (observed: a 404 from
        real GitHub for a local fixture URL)."""
        got = skill_fetch.parse_github_source(
            'https://github.com:8443/acme/skills/tree/main/x'
        )
        assert got['api_root'] == 'https://github.com:8443'
        got = skill_fetch.parse_github_source(
            'http://github.com:8443/acme/skills/tree/main/x'
        )
        assert got['api_root'] == 'http://github.com:8443'

    def test_explicit_standard_port_still_maps_to_api(self):
        got = skill_fetch.parse_github_source(
            'https://github.com:443/acme/skills/tree/main/x'
        )
        assert got['api_root'] == 'https://api.github.com'


class TestSchemeAndShape:
    def test_non_http_scheme_is_refused(self):
        with pytest.raises(ValueError, match='only http\\(s\\)'):
            skill_fetch._check_literal_host('file:///tmp/x')

    def test_userinfo_is_refused(self):
        with pytest.raises(ValueError, match='credentials'):
            skill_fetch._check_literal_host('https://user:pw@example.com/a.tgz')

    def test_missing_host_is_refused(self):
        with pytest.raises(ValueError, match='no host'):
            skill_fetch._check_literal_host('https:///a.tgz')


class TestAddressGuard:
    def _flag(self, monkeypatch, on: bool):
        monkeypatch.setattr(skill_fetch, 'loopback_test_enabled', lambda: on)

    def test_public_literal_ip_passes(self, monkeypatch):
        self._flag(monkeypatch, False)
        asyncio.run(skill_fetch.guard_url('http://93.184.216.34/a.tgz'))

    @pytest.mark.parametrize('host', ['127.0.0.1:8899', '[::1]'])
    def test_loopback_needs_the_flag(self, monkeypatch, host):
        self._flag(monkeypatch, False)
        with pytest.raises(ValueError, match='not a public address'):
            asyncio.run(skill_fetch.guard_url(f'http://{host}/a'))
        self._flag(monkeypatch, True)
        asyncio.run(skill_fetch.guard_url(f'http://{host}/a'))

    @pytest.mark.parametrize(
        'url',
        [
            'http://10.0.0.5/a',
            'http://192.168.1.1/a',
            'http://172.16.0.1/a',
            'http://169.254.169.254/latest/meta-data',  # cloud metadata
        ],
    )
    def test_private_and_link_local_are_refused_even_with_the_flag(
        self, monkeypatch, url
    ):
        self._flag(monkeypatch, True)  # the flag is LOOPBACK only
        with pytest.raises(ValueError, match='not a public address'):
            asyncio.run(skill_fetch.guard_url(url))

    def test_dns_answer_mixing_private_refuses_whole(self, monkeypatch):
        self._flag(monkeypatch, False)

        def fake_info(host, port):
            return [
                (2, 1, 6, '', ('93.184.216.34', port)),
                (2, 1, 6, '', ('10.1.2.3', port)),
            ]

        monkeypatch.setattr(socket, 'getaddrinfo', fake_info)
        with pytest.raises(ValueError, match='not a public address'):
            asyncio.run(skill_fetch.guard_url('https://skills.example/x'))

    def test_lookalike_name_resolving_public_is_allowed(self, monkeypatch):
        """'127.evil.com' is NOT 'loopback': what decides is the
        RESOLVED address (the webhook guard's rule, same here)."""
        self._flag(monkeypatch, False)
        monkeypatch.setattr(
            socket,
            'getaddrinfo',
            lambda h, p: [(2, 1, 6, '', ('93.184.216.34', p))],
        )
        asyncio.run(skill_fetch.guard_url('http://127.evil.com/x'))
