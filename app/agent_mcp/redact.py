"""The ONE place an MCP tool result gets its secrets masked — and the
ONE place a masked secret sent back in is restored.

Design rule (the ops-expansion brief): API keys, passwords, OAuth
tokens, SMTP/IMAP passwords, bot webhook secrets and the kie.ai key
are **write-only** over MCP. Every read/list/get/create/update
result from the operator-parity tools passes through
:func:`redact` before it is returned, so a secret cannot leak by a
tool forgetting to trim one field — the mask is not per-tool
knowledge, it is a property of the layer.

The write side of the SAME contract is :func:`restore_masked`: an
agent that reads a resource, edits one field and posts the whole
thing back sends our masks back too, and routes persist what they
are given — without the restore, the echoed mask would overwrite
the real secret in storage. Every create/update action that accepts
a secret-classified argument goes through it (enforced by a drift
guard on the served schemas, like the read side).

How a field NAME is judged, in precedence order:

1. it is in ``SECRET_EXACT_KEYS`` — secrets whose name alone looks
   innocent (``webhook_url``: a WeCom/DingTalk webhook URL
   *carries* its key in the query string or path);
2. it is in ``PLAINTEXT_FIELDS`` — explicitly classified as not a
   secret, which also lets a pattern-LOOKING name through on
   purpose (``has_secret`` is a bool; ``kie_api_key_masked`` and
   ``webhook_url_masked`` are the route's own masks — re-masking a
   mask only destroys information the web UI shows);
3. otherwise its name matches ``_SECRET_KEY_RE`` (password /
   secret / api_key / token / credential / private_key,
   case-insensitive) — masked automatically, so the common drift
   (a new ``smtp_password`` on a schema) is covered with no code
   change.

Every field of the wrapped resources' response models sits in
``SECRET_EXACT_KEYS`` or ``PLAINTEXT_FIELDS`` or matches the
pattern — and a drift test
(``tests/unit/test_agent_mcp_redact.py``) enforces it: a NEW field
on one of those models that matches nothing fails the test until
somebody classifies it (secret → pattern/exact set, or plaintext →
allowlist). Without the allowlist, "new field" would be
indistinguishable from "new leak".

A value that is ``None`` or empty stays as it is — "not set" is
not a leak, and turning it into ``'****'`` would lie that a secret
exists. A non-string under a secret name (a bool, a number) also
passes: there is nothing to read from it.

The write path is untouched: an action that ACCEPTS a secret
(create/update) takes the plaintext argument and hands it to the
route; only results going back out are masked.
"""

import re

from pydantic import BaseModel

from app.agent_mcp.support import ToolError

# Substring pattern on the lowercased field name. Substring, not
# word-boundary: ``encrypted_password`` and ``client_secret_enc``
# must both match, and no unclassified plaintext field in scope
# contains these substrings (the drift test pins that).
_SECRET_KEY_RE = re.compile(
    r'password|passwd|secret|api[_-]?key|apikey|token|credential|'
    r'private[_-]?key'
)

# Secrets whose NAME does not match the pattern. Checked FIRST —
# an exact-secret is a secret even if someone also allowlists it.
SECRET_EXACT_KEYS = frozenset({
    # A chat-bot webhook URL embeds its key (?key=…, ?access_token=…)
    # or a path token. The REST single-bot GET returns it raw for the
    # edit form; over MCP it is masked (rule: write-only).
    'webhook_url',
})

# Fields explicitly classified as not-a-secret. Second in
# precedence: an explicit classification overrides the pattern —
# see the masked-mask and boolean-flag entries below.
PLAINTEXT_FIELDS = frozenset({
    # StoreResponse / store results. ``browser_oauth`` is the Ziniao
    # browser PROFILE selector, shown in the UI's store form; it
    # grants nothing without the local Ziniao client.
    'id',
    'name',
    'browser_backend',
    'browser_config',
    'ziniao_account_id',
    'browser_oauth',
    'platforms',
    'countries',
    'platform_countries',
    'created_at',
    'updated_at',
    # ZiniaoBrowserProfile
    'browser_name',
    # ZiniaoAccountResponse (the model has no password field at all)
    'company',
    'username',
    'socket_port',
    'client_path',
    # EmailAccountResponse (the model has no password field at all)
    'email',
    'imap_host',
    'imap_port',
    'use_ssl',
    'smtp_host',
    'smtp_port',
    'smtp_use_tls',
    # StoreEmailLinkResponse
    'store_id',
    'email_account_id',
    'watermark_date',
    'last_polled_at',
    # ChatBotResponse / ChatBotSummary
    'platform',
    'keyword',
    'can_send_file',
    # The route's own masks — public by design.
    'webhook_url_masked',
    'kie_api_key_masked',
    # Boolean flags whose names merely LOOK secret-like.
    'has_secret',
    'kie_api_key_set',
})


def is_secret_key(name: str) -> bool:
    """Whether a field NAME is classified as carrying a secret."""
    if name in SECRET_EXACT_KEYS:
        return True
    if name in PLAINTEXT_FIELDS:
        return False
    return bool(_SECRET_KEY_RE.search(name.lower()))


def mask_secret(value):
    """Mask one secret value, keeping the last 4 characters.

    A caller that just WROTE the secret still gets the mask: the
    write path takes the plaintext, the read path never gives it
    back.

    A URL is masked WHOLE, no last-4: for a webhook URL the secret
    IS the tail (``?key=…``, a path token), so "keep the last 4"
    would keep exactly the part that must not leave the server
    (pinned by a unit test).
    """
    if not isinstance(value, str) or not value:
        return value
    if value.startswith(('http://', 'https://')):
        return '****'
    if len(value) <= 4:
        return '****'
    return f'****{value[-4:]}'


def redact(value):
    """A copy of *value* with every secret-named field masked.

    Walks dicts, lists and pydantic models recursively. The input is
    never mutated. Anything else (str, int, bool, None, datetime) is
    returned as-is.
    """
    if isinstance(value, BaseModel):
        return redact(value.model_dump())
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if isinstance(key, str) and is_secret_key(key):
                out[key] = (
                    mask_secret(item)
                    if item is None or isinstance(item, str)
                    else redact(item)
                )
            else:
                out[key] = redact(item)
        return out
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    return value


# ── the write side of the same contract ───────────────────────
#
# redact() makes every secret an agent can READ a mask — so the
# natural get → edit → update flow sends those masks BACK. Routes
# persist what they are given (ProfileManager.update_profile does a
# top-level dict.update; a create/update body is stored verbatim),
# so an echoed mask would OVERWRITE the real secret with "****1234".
# The fix is not per-resource honesty checks; it is the mirror of
# the read side: every value we might have handed out as a mask is
# recognised on the way in and restored to the stored secret.


# Argument names that are not secrets themselves but whose VALUES
# are dicts that carry secret-classified keys (a profile's ``env``).
# The write-side drift guard treats them like secret arguments.
SECRET_ARGUMENT_HINTS = frozenset({'env'})


def is_mask(value) -> bool:
    """Whether *value* is exactly a shape a server-side masker emits.

    Two maskers, same shapes with different fills:

    * :func:`mask_secret` — ``'****'`` / ``'****'`` + 4 chars;
    * ``app.vision.mask_key`` (the route's own display mask, e.g. the
      kie.ai key in Settings → AI → Vision) — ``'••••'`` / ``'••••'``
      + 4 chars. Recognising it is what makes an echoed
      ``kie_api_key_masked`` restorable instead of a "fresh secret"
      that silently stores the bullets.

    Matching the exact shapes — not "starts with ****" — keeps a
    longer real secret that happens to contain asterisks out of the
    net.
    """
    if not isinstance(value, str):
        return False
    return any(
        value == prefix or (len(value) == 8 and value.startswith(prefix))
        for prefix in ('****', '••••')
    )


def restore_masked(incoming, stored):
    """The write-side counterpart of :func:`redact`.

    Walks *incoming* (the payload an agent sent) against *stored*
    (the same-shape value the server currently holds, fetched by the
    tool through the resource's own read path). For every
    secret-classified field:

    * a value that is one of OUR masks is replaced with the stored
      secret — the agent echoed what it read, and meant "keep it";
    * a mask with NO stored value raises :class:`ToolError`: the
      agent believes it sent a secret, and silently storing (or
      dropping) the mask would let it believe a credential was set
      when none exists. The error says what to do: send the real
      secret or omit the field;
    * anything else passes through untouched — a fresh real secret
      is a legitimate write.

    Non-secret fields are walked for nested dicts (``env``) but
    never altered. ``None`` is not a mask, so a null under a secret
    key stays null (the profile tool uses null to DELETE an env
    key).

    The input is never mutated; a corrected copy is returned.
    """
    if isinstance(incoming, dict):
        stored_map = stored if isinstance(stored, dict) else {}
        out = {}
        for key, value in incoming.items():
            stored_value = stored_map.get(key)
            if isinstance(key, str) and is_secret_key(key):
                if is_mask(value):
                    if stored_value is None or stored_value == '':
                        raise ToolError(
                            f'{key} arrives masked but no stored '
                            'secret exists to keep — send the real '
                            'secret or omit the field'
                        )
                    out[key] = stored_value
                else:
                    out[key] = value
            else:
                out[key] = restore_masked(value, stored_value)
        return out
    if isinstance(incoming, list):
        stored_list = stored if isinstance(stored, list) else []
        return [
            restore_masked(
                item,
                stored_list[i] if i < len(stored_list) else None,
            )
            for i, item in enumerate(incoming)
        ]
    return incoming
