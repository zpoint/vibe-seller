"""Unit tests for the chat-bot platform registry.

Covers, per platform:
- detect_platform: accepts the 6 real host shapes, rejects every URL
  trick (cleartext, look-alike hosts, query-string mentions, userinfo,
  odd ports, wrong paths).
- DingTalk and Feishu signing against an INDEPENDENT reference
  implementation written from the official formulas inside this file —
  never by calling the code under test — plus a guard that the two
  algorithms produce different values for the same input.
- Payload shapes per platform x msgtype via httpx.MockTransport,
  DingTalk title derivation, Slack escaping, Slack's plain-text `ok`
  vs `invalid_payload`, Feishu `code` vs `StatusCode`.
- The size pre-check failing BEFORE any network call.
- The secret-leak guard: a transport error must not echo the URL, the
  token, or the signing secret.
"""

import base64
import hashlib
import hmac
import json
from urllib.parse import parse_qs, quote_plus, unquote_plus, urlsplit

import httpx
import pytest

from app.notifiers import dingtalk, feishu, slack, wecom
from app.notifiers.base import (
    PLATFORMS,
    detect_platform,
    mask_webhook_url,
    platform_for_url,
)

pytestmark = pytest.mark.unit

WECOM_URL = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abcdefgh1234'
DINGTALK_URL = 'https://oapi.dingtalk.com/robot/send?access_token=tok1234abcd'
FEISHU_URL = 'https://open.feishu.cn/open-apis/bot/v2/hook/hook-tok-9876'
LARK_URL = 'https://open.larksuite.com/open-apis/bot/v2/hook/hook-tok-9876'
SLACK_URL = 'https://hooks.slack.com/services/T000/B000/xxxxxxxxxxxx'
SLACK_GOV_URL = 'https://hooks.slack-gov.com/services/T000/B000/xxxx9876abcd'


# ── detect_platform ─────────────────────────────────


class TestDetectPlatform:
    def test_accepts_six_real_host_shapes(self):
        assert detect_platform(WECOM_URL) == 'wecom'
        assert detect_platform(DINGTALK_URL) == 'dingtalk'
        assert detect_platform(FEISHU_URL) == 'feishu'
        assert detect_platform(LARK_URL) == 'feishu'
        assert detect_platform(SLACK_URL) == 'slack'
        assert detect_platform(SLACK_GOV_URL) == 'slack'

    def test_https_default_port_443_is_accepted(self):
        assert (
            detect_platform(
                'https://oapi.dingtalk.com:443/robot/send?access_token=x'
            )
            == 'dingtalk'
        )

    def test_rejects_cleartext_to_real_host(self):
        assert (
            detect_platform(
                'http://oapi.dingtalk.com/robot/send?access_token=x'
            )
            is None
        )
        assert detect_platform('http://hooks.slack.com/services/T/B/x') is None

    def test_rejects_lookalike_subdomain(self):
        assert (
            detect_platform(
                'https://oapi.dingtalk.com.evil.com/robot/send?access_token=x'
            )
            is None
        )
        assert (
            detect_platform(
                'https://evil-qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x'
            )
            is None
        )

    def test_rejects_platform_host_in_query_only(self):
        assert detect_platform('https://evil.com/?u=oapi.dingtalk.com') is None
        assert (
            detect_platform(
                'https://evil.com/redirect?to=https%3A%2F%2Fhooks.slack.com'
            )
            is None
        )

    def test_rejects_userinfo_tricks(self):
        # The real host is evil.com; the platform host is only the
        # username.
        assert (
            detect_platform(
                'https://oapi.dingtalk.com@evil.com/robot/send?access_token=x'
            )
            is None
        )
        assert (
            detect_platform(
                'https://hooks.slack.com:secret@evil.com/services/T/B/x'
            )
            is None
        )

    def test_rejects_non_default_port(self):
        assert (
            detect_platform(
                'https://oapi.dingtalk.com:8443/robot/send?access_token=x'
            )
            is None
        )

    def test_rejects_wrong_path_on_right_host(self):
        assert detect_platform('https://oapi.dingtalk.com/other') is None
        assert detect_platform('https://qyapi.weixin.qq.com/evil') is None

    def test_rejects_garbage(self):
        assert detect_platform('') is None
        assert detect_platform('not a url') is None
        assert detect_platform('ftp://oapi.dingtalk.com/robot/send') is None
        assert detect_platform('https://evil.com') is None

    def test_loopback_rejected_without_the_flag(self, monkeypatch):
        """Production never accepts loopback webhook URLs.

        A webhook secret may only ever go to a real platform host over
        https; loopback (plain http included) is a test-only hatch
        behind VIBE_ALLOW_LOOPBACK_WEBHOOKS, OFF by default.
        """
        monkeypatch.delenv('VIBE_ALLOW_LOOPBACK_WEBHOOKS', raising=False)
        assert (
            detect_platform(
                'http://127.0.0.1:9999/cgi-bin/webhook/send?key=stub'
            )
            is None
        )
        assert detect_platform('http://localhost/cgi-bin/webhook/send') is None
        assert (
            detect_platform('http://[::1]:9999/cgi-bin/webhook/send?key=x')
            is None
        )
        # https loopback is not a platform host either.
        assert (
            detect_platform('https://127.0.0.1:9999/cgi-bin/webhook/send')
            is None
        )

    def test_loopback_allowed_only_with_the_flag(self, monkeypatch):
        """With the flag, a STRICT loopback check applies.

        The host must BE a loopback address (ipaddress) or the exact
        name `localhost` — never a string prefix. `127.evil.com` and
        `127.0.0.1.nip.io` both start with "127." and both resolve to
        arbitrary internet hosts; accepting them would post the
        webhook secret in cleartext to an attacker-chosen machine.
        """
        monkeypatch.setenv('VIBE_ALLOW_LOOPBACK_WEBHOOKS', '1')

        # Real loopback addresses — 127.0.0.0/8, ::1, localhost.
        assert (
            detect_platform(
                'http://127.0.0.1:9999/cgi-bin/webhook/send?key=stub'
            )
            == 'wecom'
        )
        assert (
            detect_platform(
                'http://127.44.0.9:9999/cgi-bin/webhook/send?key=stub'
            )
            == 'wecom'
        )
        assert (
            detect_platform('http://localhost:9999/robot/send?access_token=s')
            == 'dingtalk'
        )
        assert (
            detect_platform('http://[::1]:9999/cgi-bin/webhook/send?key=x')
            == 'wecom'
        )
        # A loopback URL with an unknown path is still rejected.
        assert detect_platform('http://127.0.0.1:9999/nope') is None

        # Look-alike names that merely START with a loopback prefix.
        assert (
            detect_platform('http://127.evil.com/cgi-bin/webhook/send?key=x')
            is None
        )
        assert (
            detect_platform(
                'http://127.0.0.1.nip.io/cgi-bin/webhook/send?key=x'
            )
            is None
        )
        assert (
            detect_platform(
                'http://localhost.evil.com/cgi-bin/webhook/send?key=x'
            )
            is None
        )
        # And loopback does not open the door to other hosts.
        assert detect_platform('http://10.0.0.5:9999/robot/send') is None

    def test_platform_for_url_returns_spec(self):
        plat = platform_for_url(SLACK_URL)
        assert plat is not None
        assert plat.name == 'slack'
        assert plat.uses_secret is False


# ── Signing (independent reference implementations) ─


def _reference_dingtalk_sign(secret: str, timestamp: str) -> str:
    """Reference written from DingTalk's documented formula.

    key = secret, message = "<ts>\\n<secret>", HMAC-SHA256, base64,
    then URL-encode.
    """
    string_to_sign = f'{timestamp}\n{secret}'
    digest = hmac.new(
        secret.encode(), string_to_sign.encode(), hashlib.sha256
    ).digest()
    return quote_plus(base64.b64encode(digest))


def _reference_feishu_sign(secret: str, timestamp: str) -> str:
    """Reference written from Feishu's documented formula.

    key = "<ts>\\n<secret>", message = b"", HMAC-SHA256, base64, NOT
    URL-encoded.
    """
    string_to_sign = f'{timestamp}\n{secret}'
    digest = hmac.new(string_to_sign.encode(), b'', hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


class TestDingTalkSign:
    def test_matches_reference(self):
        assert dingtalk.sign('SECtest', '1700000000000') == (
            _reference_dingtalk_sign('SECtest', '1700000000000')
        )

    def test_sign_is_url_encoded_and_lands_in_query(self):
        url = dingtalk.signed_url(DINGTALK_URL, 'SECtest', '1700000000000')
        raw_query = urlsplit(url).query
        assert 'timestamp=1700000000000' in raw_query
        # The sign value is URL-ENCODED into the query string — compare
        # against the raw query (parse_qs would unquote it back).
        sign_value = dingtalk.sign('SECtest', '1700000000000')
        assert f'sign={sign_value}' in raw_query
        # And it is a percent-encoded form of the base64 digest.
        decoded = unquote_plus(sign_value)
        assert (
            decoded
            == base64.b64encode(
                hmac.new(
                    b'SECtest', b'1700000000000\nSECtest', hashlib.sha256
                ).digest()
            ).decode()
        )

    def test_unsigned_when_no_secret(self):
        assert dingtalk.signed_url(DINGTALK_URL, None) == DINGTALK_URL
        assert dingtalk.signed_url(DINGTALK_URL, '') == DINGTALK_URL

    def test_different_timestamps_different_signs(self):
        assert dingtalk.sign('SECtest', '1700000000000') != (
            dingtalk.sign('SECtest', '1700000000001')
        )


class TestFeishuSign:
    def test_matches_reference(self):
        assert feishu.sign('SECtest', '1700000000') == (
            _reference_feishu_sign('SECtest', '1700000000')
        )

    def test_sign_lands_in_body_not_url(self):
        payload = feishu.build_payload('hello', 'text', 'SECtest', '1700000000')
        assert payload['timestamp'] == '1700000000'
        assert payload['sign'] == _reference_feishu_sign(
            'SECtest', '1700000000'
        )
        # NOT in the URL: the sender posts to the bare webhook URL.
        assert 'sign' not in FEISHU_URL
        assert 'timestamp' not in FEISHU_URL

    def test_unsigned_when_no_secret(self):
        payload = feishu.build_payload('hello', 'text', None)
        assert 'sign' not in payload
        assert 'timestamp' not in payload

    def test_algorithms_differ_for_same_input(self):
        """Guard against someone "unifying" the two signing schemes.

        DingTalk signs milliseconds with the secret as key; Feishu signs
        seconds with "<ts>\\n<secret>" as key and an empty message.
        For any fixed (ts, secret) pair the outputs must differ.
        """
        assert dingtalk.sign('SECtest', '1700000000000') != feishu.sign(
            'SECtest', '1700000000000'
        )
        assert _reference_dingtalk_sign('SECtest', '1700000000000') != (
            _reference_feishu_sign('SECtest', '1700000000000')
        )


# ── Payload shapes via mock transport ───────────────


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _capture(captured: list, response_json=None, status=200, text=None):
    """Build a MockTransport handler that records the request."""

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if text is not None:
            return httpx.Response(status, text=text)
        return httpx.Response(status, json=response_json or {})

    return handler


def _patch_transport(monkeypatch, transport):
    """Route base.post through the mock transport."""

    async def _fake_post(url, **kwargs):
        client = httpx.AsyncClient(transport=transport, timeout=10)
        try:
            resp = await client.post(
                url,
                json=kwargs.get('json_payload'),
                content=kwargs.get('content'),
                headers=kwargs.get('headers'),
            )
            return resp, ''
        finally:
            await client.aclose()

    monkeypatch.setattr('app.notifiers.base.post', _fake_post)


class TestWecomPayloads:
    async def test_text_shape(self, monkeypatch):
        captured = []
        _patch_transport(
            monkeypatch, _transport(_capture(captured, {'errcode': 0}))
        )
        ok, msg = await wecom.send_message(WECOM_URL, None, 'hi')
        assert ok is True
        body = json.loads(captured[0].read())
        assert body == {'msgtype': 'text', 'text': {'content': 'hi'}}

    async def test_markdown_shape(self, monkeypatch):
        captured = []
        _patch_transport(
            monkeypatch, _transport(_capture(captured, {'errcode': 0}))
        )
        ok, _ = await wecom.send_message(
            WECOM_URL, None, '# h', msgtype='markdown'
        )
        assert ok is True
        body = json.loads(captured[0].read())
        assert body == {
            'msgtype': 'markdown',
            'markdown': {'content': '# h'},
        }

    async def test_errcode_error_surfaced(self, monkeypatch):
        captured = []
        _patch_transport(
            monkeypatch,
            _transport(
                _capture(captured, {'errcode': 93000, 'errmsg': 'invalid url'})
            ),
        )
        ok, msg = await wecom.send_message(WECOM_URL, None, 'hi')
        assert ok is False
        assert 'invalid url' in msg


class TestDingtalkPayloads:
    async def test_text_shape(self, monkeypatch):
        captured = []
        _patch_transport(
            monkeypatch, _transport(_capture(captured, {'errcode': 0}))
        )
        ok, _ = await dingtalk.send_message(DINGTALK_URL, None, 'hi')
        assert ok is True
        body = json.loads(captured[0].read())
        assert body == {'msgtype': 'text', 'text': {'content': 'hi'}}

    async def test_markdown_requires_title(self, monkeypatch):
        captured = []
        _patch_transport(
            monkeypatch, _transport(_capture(captured, {'errcode': 0}))
        )
        content = '## Daily report\n- item'
        ok, _ = await dingtalk.send_message(
            DINGTALK_URL, None, content, msgtype='markdown'
        )
        assert ok is True
        body = json.loads(captured[0].read())
        assert body['markdown']['title'] == 'Daily report'
        assert body['markdown']['text'] == content

    async def test_secret_signs_the_query(self, monkeypatch):
        captured = []
        _patch_transport(
            monkeypatch, _transport(_capture(captured, {'errcode': 0}))
        )
        ok, _ = await dingtalk.send_message(DINGTALK_URL, 'SECtest', 'hi')
        assert ok is True
        query = parse_qs(urlsplit(str(captured[0].url)).query)
        assert 'timestamp' in query
        assert 'sign' in query
        assert query['access_token'] == ['tok1234abcd']

    async def test_security_error_surfaces_errmsg(self, monkeypatch):
        captured = []
        _patch_transport(
            monkeypatch,
            _transport(
                _capture(
                    captured,
                    {
                        'errcode': 310000,
                        'errmsg': 'sign not match... keyword not in content',
                    },
                )
            ),
        )
        ok, msg = await dingtalk.send_message(DINGTALK_URL, 'SECtest', 'hi')
        assert ok is False
        assert 'sign not match' in msg


class TestFeishuPayloads:
    async def test_text_shape(self, monkeypatch):
        captured = []
        _patch_transport(
            monkeypatch,
            _transport(_capture(captured, {'code': 0, 'msg': 'success'})),
        )
        ok, _ = await feishu.send_message(FEISHU_URL, None, 'hi')
        assert ok is True
        body = json.loads(captured[0].read())
        assert body == {'msg_type': 'text', 'content': {'text': 'hi'}}

    async def test_markdown_is_interactive_card(self, monkeypatch):
        captured = []
        _patch_transport(
            monkeypatch,
            _transport(_capture(captured, {'code': 0, 'msg': 'success'})),
        )
        ok, _ = await feishu.send_message(
            FEISHU_URL, None, '# h', msgtype='markdown'
        )
        assert ok is True
        body = json.loads(captured[0].read())
        assert body['msg_type'] == 'interactive'
        assert body['card']['schema'] == '2.0'
        assert body['card']['body']['elements'] == [
            {'tag': 'markdown', 'content': '# h'}
        ]

    async def test_secret_in_body_not_url(self, monkeypatch):
        captured = []
        _patch_transport(
            monkeypatch,
            _transport(_capture(captured, {'code': 0, 'msg': 'success'})),
        )
        ok, _ = await feishu.send_message(FEISHU_URL, 'SECtest', 'hi')
        assert ok is True
        sent = captured[0]
        body = json.loads(sent.read())
        assert 'timestamp' in body
        assert 'sign' in body
        # The bare webhook URL — no sign/timestamp query was added.
        assert urlsplit(str(sent.url)).query == ''

    async def test_code_vs_statuscode_success(self, monkeypatch):
        captured = []
        _patch_transport(
            monkeypatch,
            _transport(_capture(captured, {'StatusCode': 0})),
        )
        ok, msg = await feishu.send_message(FEISHU_URL, None, 'hi')
        assert ok is True
        assert msg == ''

    async def test_error_code_surfaced(self, monkeypatch):
        captured = []
        _patch_transport(
            monkeypatch,
            _transport(
                _capture(captured, {'code': 19021, 'msg': 'sign match fail'})
            ),
        )
        ok, msg = await feishu.send_message(FEISHU_URL, 'SECtest', 'hi')
        assert ok is False
        assert 'sign match fail' in msg


class TestSlackPayloads:
    async def test_text_escapes_markup(self, monkeypatch):
        captured = []
        _patch_transport(monkeypatch, _transport(_capture(captured, text='ok')))
        ok, _ = await slack.send_message(SLACK_URL, None, 'a & b <c> d')
        assert ok is True
        body = json.loads(captured[0].read())
        assert body == {'text': 'a &amp; b &lt;c&gt; d'}

    async def test_markdown_uses_markdown_block(self, monkeypatch):
        captured = []
        _patch_transport(monkeypatch, _transport(_capture(captured, text='ok')))
        content = '# Head\n**bold** and [link](https://x.test)'
        ok, _ = await slack.send_message(
            SLACK_URL, None, content, msgtype='markdown'
        )
        assert ok is True
        body = json.loads(captured[0].read())
        assert body['blocks'] == [{'type': 'markdown', 'text': content}]
        assert body['text']

    async def test_ok_is_plain_text_not_json(self, monkeypatch):
        captured = []
        _patch_transport(monkeypatch, _transport(_capture(captured, text='ok')))
        ok, msg = await slack.send_message(SLACK_URL, None, 'hi')
        assert ok is True
        assert msg == ''

    async def test_invalid_payload_error_surfaced(self, monkeypatch):
        captured = []
        _patch_transport(
            monkeypatch,
            _transport(_capture(captured, status=400, text='invalid_payload')),
        )
        ok, msg = await slack.send_message(SLACK_URL, None, 'hi')
        assert ok is False
        assert msg == 'invalid_payload'

    async def test_markdown_block_falls_back_to_mrkdwn(self, monkeypatch):
        """If Slack rejects the markdown block, retry as converted text."""
        captured = []
        calls = {'n': 0}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            calls['n'] += 1
            if calls['n'] == 1:
                return httpx.Response(400, text='invalid_blocks')
            return httpx.Response(200, text='ok')

        _patch_transport(monkeypatch, _transport(handler))
        ok, msg = await slack.send_message(
            SLACK_URL, None, '# Head\n**bold**', msgtype='markdown'
        )
        assert ok is True
        assert msg == ''
        assert len(captured) == 2
        second = json.loads(captured[1].read())
        assert second['text'] == '*Head*\n*bold*'


class TestDingTalkTitle:
    def test_first_heading_line_stripped(self):
        assert dingtalk.derive_title('## Daily report\nbody') == (
            'Daily report'
        )

    def test_skips_blank_lines(self):
        assert dingtalk.derive_title('\n\n  # Title\nbody') == 'Title'

    def test_truncated_to_64(self):
        assert len(dingtalk.derive_title('x' * 100)) == 64

    def test_fallback_when_empty(self):
        assert dingtalk.derive_title('') == 'Vibe Seller'
        assert dingtalk.derive_title('\n \n') == 'Vibe Seller'


class TestSizePreCheck:
    """Oversized content must fail BEFORE any network call."""

    CASES = [
        ('wecom', wecom.send_message, WECOM_URL, 4096, 'bytes'),
        ('dingtalk', dingtalk.send_message, DINGTALK_URL, 20000, 'bytes'),
        ('feishu', feishu.send_message, FEISHU_URL, 20000, 'bytes'),
        ('slack', slack.send_message, SLACK_URL, 40000, 'chars'),
    ]

    @pytest.mark.parametrize(
        'name,fn,url,limit,unit', CASES, ids=[c[0] for c in CASES]
    )
    async def test_oversized_makes_no_network_call(
        self, monkeypatch, name, fn, url, limit, unit
    ):
        def _boom(*a, **kw):
            raise AssertionError('network must not be reached')

        monkeypatch.setattr('app.notifiers.base.post', _boom)
        content = 'x' * (limit + 1)
        ok, msg = await fn(url, None, content)
        assert ok is False
        assert 'split it into several messages' in msg
        assert str(limit) in msg

    async def test_slack_markdown_limit_is_tighter(self, monkeypatch):
        def _boom(*a, **kw):
            raise AssertionError('network must not be reached')

        monkeypatch.setattr('app.notifiers.base.post', _boom)
        # 12001 chars: fine for text, over the markdown-block limit.
        content = 'x' * 12001
        ok, _ = await slack.send_message(
            SLACK_URL, None, content, msgtype='markdown'
        )
        assert ok is False


class TestSecretLeakGuard:
    """A transport failure must never echo URL, token, or secret."""

    SECRET = 'SECsigningsecret999'

    @pytest.mark.parametrize(
        'name,fn,url',
        [
            ('wecom', wecom.send_message, WECOM_URL),
            ('dingtalk', dingtalk.send_message, DINGTALK_URL),
            ('feishu', feishu.send_message, FEISHU_URL),
            ('slack', slack.send_message, SLACK_URL),
        ],
    )
    async def test_connect_error_does_not_leak(
        self, monkeypatch, name, fn, url
    ):
        # Break the TRANSPORT, not base.post — the catch-and-sanitize
        # behavior under test lives inside base.post, so that function
        # must run for real. httpx exception strings embed the request
        # URL, which is exactly the leak this test pins.
        async def _raise(*a, **kw):
            raise httpx.ConnectError(f'cannot connect to {url}')

        monkeypatch.setattr(httpx.AsyncClient, 'post', _raise)
        ok, msg = await fn(url, self.SECRET, 'hi')
        assert ok is False
        assert url not in msg
        assert 'abcdefgh1234' not in msg
        assert 'tok1234abcd' not in msg
        assert 'hook-tok-9876' not in msg
        assert 'xxxxxxxxxxxx' not in msg
        assert self.SECRET not in msg


class TestMasking:
    def test_wecom_masks_key(self):
        masked = mask_webhook_url(WECOM_URL, 'wecom')
        assert masked == 'https://qyapi.weixin.qq.com/...****1234'
        assert 'abcdefgh' not in masked

    def test_dingtalk_masks_access_token(self):
        masked = mask_webhook_url(DINGTALK_URL, 'dingtalk')
        assert masked == 'https://oapi.dingtalk.com/...****abcd'
        assert 'tok1234' not in masked

    def test_feishu_masks_path_token(self):
        masked = mask_webhook_url(FEISHU_URL, 'feishu')
        assert masked == 'https://open.feishu.cn/...****9876'
        assert 'hook-tok' not in masked

    def test_slack_masks_path_token(self):
        masked = mask_webhook_url(SLACK_URL, 'slack')
        assert masked == 'https://hooks.slack.com/...****xxxx'
        assert '/services/T000/B000' not in masked

    def test_short_secret_shows_no_tail(self):
        url = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=ab'
        assert mask_webhook_url(url, 'wecom') == (
            'https://qyapi.weixin.qq.com/...'
        )

    def test_url_without_host_shows_placeholder(self):
        # urlsplit does not raise for 'not a url'; the host becomes a
        # placeholder and nothing of the input is echoed back.
        masked = mask_webhook_url('not a url', 'wecom')
        assert masked == 'https://***/...'
        assert 'not a url' not in masked

    def test_unparseable_url_falls_back(self):
        # A URL urlsplit itself rejects ('http://[') → total fallback.
        assert mask_webhook_url('http://[', 'wecom') == '***'


class TestRegistryMetadata:
    def test_every_platform_registers_a_sender(self):
        # Importing app.notifiers (the package __init__) loads all four
        # platform modules; each sets Platform.send.
        for name, plat in PLATFORMS.items():
            assert plat.send is not None, name

    def test_file_support_matrix(self):
        assert PLATFORMS['wecom'].supports_file is True
        assert PLATFORMS['dingtalk'].supports_file is False
        assert PLATFORMS['feishu'].supports_file is False
        assert PLATFORMS['slack'].supports_file is False

    def test_secret_support_matrix(self):
        assert PLATFORMS['wecom'].uses_secret is False
        assert PLATFORMS['slack'].uses_secret is False
        assert PLATFORMS['dingtalk'].uses_secret is True
        assert PLATFORMS['feishu'].uses_secret is True
