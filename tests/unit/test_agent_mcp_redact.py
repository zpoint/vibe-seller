"""The secret-redaction layer of the hosted MCP toolset.

Four jobs:

* pin the READ helper's behaviour — what gets masked, what passes,
  and that the input is never mutated;
* pin the WRITE helper (:func:`restore_masked`) — an echoed mask is
  restored to the stored secret, a mask with nothing stored is an
  error, a fresh real secret passes;
* the READ drift guard: every field of every response model the
  operator-parity tools can return is CLASSIFIED (secret by name
  pattern, secret by exact key, or explicitly plaintext). A new
  field on one of those schemas that matches nothing fails here
  until somebody classifies it — "new field" must never be
  indistinguishable from "new leak". See ``app/agent_mcp/redact.py``
  for the precedence rules;
* the WRITE drift guard: every tool that ACCEPTS a secret-classified
  argument routes its writes through ``restore_masked`` — a new
  secret-bearing argument without the restore is the read-side bug
  worn backwards (the agent's own echo overwrites the secret).
"""

import inspect

from pydantic import BaseModel
import pytest

from app.agent_mcp.redact import (
    PLAINTEXT_FIELDS,
    SECRET_ARGUMENT_HINTS,
    SECRET_EXACT_KEYS,
    ToolError,
    is_mask,
    is_secret_key,
    mask_secret,
    redact,
    restore_masked,
)
from app.agent_mcp.registry import TOOLS
from app.schemas.chat_bot import ChatBotResponse, ChatBotSummary
from app.schemas.email_account import (
    EmailAccountResponse,
    StoreEmailLinkResponse,
)
from app.schemas.store import StoreResponse
from app.schemas.ziniao_account import (
    ZiniaoAccountResponse,
    ZiniaoBrowserProfile,
)

pytestmark = pytest.mark.unit

SECRET = 'super-secret-value-1234'


# Every response model whose rows the operator-parity tools return.
# A model that belongs here but is missing is itself a drift — the
# workflow redaction tests cover shapes these cannot see (profile
# env dicts, settings, dida365 status), this list covers the typed
# REST payloads.
IN_SCOPE_MODELS = [
    StoreResponse,
    ZiniaoAccountResponse,
    ZiniaoBrowserProfile,
    EmailAccountResponse,
    StoreEmailLinkResponse,
    ChatBotResponse,
    ChatBotSummary,
]

# Secret field names that must be masked no matter where they
# appear — the known real ones plus the shapes a schema drift would
# plausibly add.
KNOWN_SECRET_NAMES = [
    'password',
    'encrypted_password',
    'smtp_password',
    'imap_password',
    'secret',
    'client_secret',
    'client_secret_enc',
    'api_key',
    'kie_api_key',
    'access_token',
    'access_token_enc',
    'refresh_token',
    'refresh_token_enc',
    'ANTHROPIC_API_KEY',
    'ANTHROPIC_AUTH_TOKEN',
    'webhook_url',
    'bot_token',
    'credentials',
    'private_key',
]


class TestMasking:
    def test_known_secret_names_are_masked(self):
        for name in KNOWN_SECRET_NAMES:
            result = redact({name: SECRET})
            assert result[name] != SECRET, name
            assert SECRET not in result[name], name

    def test_mask_keeps_the_last_four(self):
        assert mask_secret('sk-ant-1234') == '****1234'
        assert redact({'api_key': 'sk-ant-1234'})['api_key'] == ('****1234')

    def test_url_secrets_mask_whole_no_last_four(self):
        # For a webhook URL the secret IS the tail (?key=…, path
        # token) — "keep the last 4" would keep exactly the part
        # that must not leave the server.
        url = 'https://qyapi.example.com/send?key=abcd'
        assert mask_secret(url) == '****'
        assert redact({'webhook_url': url})['webhook_url'] == '****'

    def test_short_secrets_fully_masked(self):
        assert mask_secret('ab') == '****'
        assert mask_secret('abcd') == '****'

    def test_none_and_empty_stay_honest(self):
        # "Not set" is not a leak, and masking it would claim a
        # secret exists.
        assert redact({'secret': None})['secret'] is None
        assert redact({'secret': ''})['secret'] == ''

    def test_plain_fields_pass_through(self):
        payload = {
            'name': 'acme',
            'browser_backend': 'chrome',
            'imap_host': 'imap.example.com',
            'keyword': 'vibe',
        }
        assert redact(payload) == payload

    def test_nested_dicts_and_lists(self):
        payload = {
            'env': {
                'ANTHROPIC_AUTH_TOKEN': SECRET,
                'ANTHROPIC_API_KEY': SECRET,
                'ANTHROPIC_BASE_URL': 'https://api.example.com',
                'ANTHROPIC_MODEL': 'model-x',
            },
            'bots': [
                {
                    'name': 'ops',
                    'webhook_url': 'https://qyapi.example.com/send?key=k123',
                }
            ],
        }
        out = redact(payload)
        assert out['env']['ANTHROPIC_AUTH_TOKEN'] != SECRET
        assert out['env']['ANTHROPIC_API_KEY'] != SECRET
        assert out['env']['ANTHROPIC_BASE_URL'] == 'https://api.example.com'
        assert out['env']['ANTHROPIC_MODEL'] == 'model-x'
        assert 'k123' not in out['bots'][0]['webhook_url']

    def test_pydantic_models_are_walked(self):
        class _Holder(BaseModel):
            api_key: str
            name: str

        out = redact(_Holder(api_key=SECRET, name='acme'))
        assert out == {'api_key': '****1234', 'name': 'acme'}

    def test_input_is_not_mutated(self):
        payload = {'api_key': SECRET, 'env': {'token': SECRET}}
        redact(payload)
        assert payload['api_key'] == SECRET
        assert payload['env']['token'] == SECRET

    def test_pattern_matching_bool_flags_pass(self):
        # has_secret / *_set flags match the pattern by name but
        # carry no readable secret; the plaintext classification
        # keeps them as booleans.
        assert redact({'has_secret': True}) == {'has_secret': True}

    def test_routes_own_masks_pass_through(self):
        masked = 'sk-***abcd'
        assert redact({'kie_api_key_masked': masked}) == {
            'kie_api_key_masked': masked
        }


class TestClassificationDrift:
    """Every in-scope model field is classified — a new field fails
    until it is added to the pattern coverage, SECRET_EXACT_KEYS or
    PLAINTEXT_FIELDS (whichever is true)."""

    def test_every_model_field_is_classified(self):
        unclassified = []
        for model in IN_SCOPE_MODELS:
            for field in model.model_fields:
                if is_secret_key(field):
                    continue
                if field in PLAINTEXT_FIELDS:
                    continue
                unclassified.append(f'{model.__name__}.{field}')
        assert not unclassified, (
            'These response-model fields are unclassified. If the '
            'field is a secret, it must be masked (it needs a name '
            'that matches the pattern in app/agent_mcp/redact.py, or '
            'an entry in SECRET_EXACT_KEYS); if it is not, allowlist '
            f'it in PLAINTEXT_FIELDS: {unclassified}'
        )

    def test_secret_named_fields_really_are_masked(self):
        for model in IN_SCOPE_MODELS:
            for field in model.model_fields:
                if is_secret_key(field):
                    masked = redact({field: SECRET})[field]
                    assert masked != SECRET, f'{model.__name__}.{field}'

    def test_plaintext_fields_are_really_passthrough(self):
        for field in sorted(PLAINTEXT_FIELDS):
            value = 'a-plain-value'
            assert redact({field: value})[field] == value, field

    def test_exact_secrets_are_never_allowlisted(self):
        overlap = SECRET_EXACT_KEYS & PLAINTEXT_FIELDS
        assert not overlap, (
            f'{overlap} is classified as BOTH secret and plaintext — '
            'the exact-secret set wins, so the allowlist entry lies'
        )

    def test_a_new_secret_field_on_a_schema_fails_until_classified(
        self,
    ):
        """The drift this guard exists for, demonstrated rather than
        trusted: an innocent-named field nobody classified fails the
        classification test — the reviewer must then decide whether
        it is a secret (exact set) or not (allowlist)."""

        class _Drift(BaseModel):
            oauth: str

        unclassified = [
            field
            for field in _Drift.model_fields
            if not is_secret_key(field) and field not in PLAINTEXT_FIELDS
        ]
        assert unclassified == ['oauth']


class TestIsMask:
    def test_the_exact_shapes_mask_secret_emits(self):
        assert is_mask('****')
        assert is_mask('****1234')
        assert is_mask(mask_secret(SECRET))
        assert is_mask(mask_secret('https://qyapi.example.com/hook?key=k1'))

    def test_the_vision_display_masks_are_masks_too(self):
        """``app.vision.mask_key`` emits ``••••`` shapes for the
        kie.ai key — an agent echoing what it READ (the masked form)
        must be recognised, or the bullets get stored as the "fresh"
        secret."""
        assert is_mask('••••')
        assert is_mask('••••1234')
        assert not is_mask('••••12345')  # longer than a mask

    def test_real_values_are_not_masks(self):
        assert not is_mask(SECRET)
        assert not is_mask('****12345')  # 9 chars: longer than a mask
        assert not is_mask('***')
        assert not is_mask('')
        assert not is_mask(None)
        assert not is_mask(1234)


class TestRestoreMasked:
    """The write side of the contract: an echoed mask means "keep the
    stored secret", never "store the mask"."""

    def test_a_mask_is_restored_to_the_stored_secret(self):
        stored = {'api_key': SECRET, 'name': 'acme'}
        incoming = {'api_key': '****1234', 'name': 'renamed'}
        out = restore_masked(incoming, stored)
        assert out['api_key'] == SECRET
        assert out['name'] == 'renamed'
        # the input is not mutated
        assert incoming['api_key'] == '****1234'

    def test_a_mask_without_a_stored_secret_is_an_error(self):
        with pytest.raises(ToolError) as exc:
            restore_masked({'password': '****'}, {'password': None})
        assert 'real secret' in str(exc.value)
        with pytest.raises(ToolError):
            restore_masked({'password': '****'}, {})

    def test_a_fresh_real_secret_passes_through(self):
        out = restore_masked(
            {'password': 'brand-new'}, {'password': 'the-old-one'}
        )
        assert out['password'] == 'brand-new'

    def test_nested_env_dicts(self):
        stored = {
            'env': {
                'ANTHROPIC_AUTH_TOKEN': SECRET,
                'ANTHROPIC_MODEL': 'old',
            }
        }
        incoming = {
            'env': {
                'ANTHROPIC_AUTH_TOKEN': mask_secret(SECRET),
                'ANTHROPIC_MODEL': 'new',
            }
        }
        out = restore_masked(incoming, stored)
        assert out['env']['ANTHROPIC_AUTH_TOKEN'] == SECRET
        assert out['env']['ANTHROPIC_MODEL'] == 'new'

    def test_null_is_not_a_mask_and_survives(self):
        """A null under a secret key is the profile env's DELETE
        marker — the merge layer reads it after this pass."""
        out = restore_masked({'env': {'KEY': None}}, {'env': {'KEY': 'x'}})
        assert out['env']['KEY'] is None

    def test_a_masked_url_is_restored(self):
        url = 'https://qyapi.example.com/send?key=abcd'
        out = restore_masked({'webhook_url': '****'}, {'webhook_url': url})
        assert out['webhook_url'] == url

    def test_a_vision_style_mask_is_restored(self):
        """The kie.ai key's display mask is bullet-filled; echoing it
        must keep the stored key, not store the bullets."""
        stored = {'kie_api_key': 'sk-live-abcd1234'}
        out = restore_masked({'kie_api_key': '••••1234'}, stored)
        assert out['kie_api_key'] == 'sk-live-abcd1234'

    def test_non_secret_fields_are_untouched(self):
        payload = {'name': 'acme', 'imap_host': 'imap.example.com'}
        assert restore_masked(payload, {'name': 'other'}) == payload

    def test_roundtrip_with_redact(self):
        stored = {'env': {'ANTHROPIC_AUTH_TOKEN': SECRET}}
        echoed = redact(stored)
        assert restore_masked(echoed, stored) == stored


class TestEnvScopedClassification:
    """Q1: inside an ``env`` dict, a key containing ``key`` is a
    secret — profile env names are owner-typed, and provider
    credentials that miss the general pattern (``MY_PROVIDER_KEY``)
    are the norm there. Scoped to env dicts only."""

    def test_a_key_named_env_secret_is_masked(self):
        out = redact({'env': {'MY_PROVIDER_KEY': SECRET}})
        assert out['env']['MY_PROVIDER_KEY'] != SECRET
        assert out['env']['MY_PROVIDER_KEY'].startswith('****')

    def test_non_secret_env_names_stay_readable(self):
        env = {
            'ANTHROPIC_BASE_URL': 'https://api.example.com',
            'ANTHROPIC_MODEL': 'model-x',
            'MINIMAX_MODEL': 'abab-6',
            'HTTP_PROXY': 'http://127.0.0.1:7890',
        }
        assert redact({'env': dict(env)}) == {'env': env}

    def test_the_env_rule_does_not_leak_out_of_env_dicts(self):
        """``keyword`` on a chat bot contains "key" and is plaintext —
        the wider rule must be scoped to env dicts, not global."""
        payload = {'keyword': 'vibe', 'env': {'keyword': SECRET}}
        out = redact(payload)
        assert out['keyword'] == 'vibe'
        assert out['env']['keyword'] != SECRET

    def test_an_env_key_mask_is_restored(self):
        stored = {'env': {'MY_PROVIDER_KEY': 'sk-provider-real'}}
        incoming = {'env': {'MY_PROVIDER_KEY': '****real'}}
        out = restore_masked(incoming, stored)
        assert out['env']['MY_PROVIDER_KEY'] == 'sk-provider-real'

    def test_an_env_key_mask_with_nothing_stored_is_an_error(self):
        with pytest.raises(ToolError):
            restore_masked({'env': {'MY_PROVIDER_KEY': '****'}}, {'env': {}})

    def test_a_top_level_env_dict_takes_the_scope_argument(self):
        """The profile tools pass the env dict itself as the payload —
        ``env_scope=True`` must classify its keys the same way the
        nested walk does."""
        out = restore_masked(
            {'MY_PROVIDER_KEY': '****real'},
            {'MY_PROVIDER_KEY': 'sk-real'},
            env_scope=True,
        )
        assert out['MY_PROVIDER_KEY'] == 'sk-real'

    def test_the_rest_mechanism_roundtrips_the_wider_rule(self):
        stored = {'env': {'MY_PROVIDER_KEY': SECRET}}
        echoed = redact(stored)
        assert restore_masked(echoed, stored) == stored


class TestWriteSideDriftGuard:
    """Every tool that accepts a secret-classified ARGUMENT must route
    its writes through restore_masked — or an agent's get → edit →
    update round trip overwrites the real secret with the mask it
    read. ``SECRET_ARGUMENT_HINTS`` covers argument names (a profile's
    ``env``) that carry secret-classified keys without being named
    like one."""

    def _secret_arguments(self, definition) -> list[str]:
        properties = definition.input_schema.get('properties', {})
        return sorted(
            name
            for name in properties
            if is_secret_key(name) or name in SECRET_ARGUMENT_HINTS
        )

    def test_every_secret_accepting_tool_restores_masks(self):
        offenders = []
        for definition in TOOLS:
            if not self._secret_arguments(definition):
                continue
            source = inspect.getsource(definition.handler)
            if 'restore_masked' not in source:
                offenders.append(definition.name)
        assert not offenders, (
            'these tools accept secret-bearing arguments but never '
            'call restore_masked — a masked echo from a read would be '
            f'persisted verbatim: {offenders}'
        )

    def test_the_secret_argument_hints_are_real_argument_names(self):
        """A hint for an argument nobody serves is a stale hint."""
        served = {
            name
            for definition in TOOLS
            for name in definition.input_schema.get('properties', {})
        }
        stale = SECRET_ARGUMENT_HINTS - served
        assert not stale, f'stale SECRET_ARGUMENT_HINTS: {stale}'

    def test_every_registered_handler_is_its_tools_function(self):
        """The @tool decorator attaches to the function directly under
        it. A helper inserted between the decorator and its tool
        registers THE HELPER as the handler — the tool then silently
        returns garbage (observed once in review). The handler must be
        a public, snake_case function of its module, reachable under
        its own name."""

        def offenders() -> list[str]:
            bad = []
            for definition in TOOLS:
                name = definition.handler.__name__
                module = inspect.getmodule(definition.handler)
                if (
                    name.startswith('_')
                    or name != definition.name
                    or getattr(module, name, None) is not definition.handler
                ):
                    bad.append(f'{definition.name} → {name}')
            return bad

        assert not offenders(), offenders()
