"""Browser-vendor registry — the spec that makes a vendor declarative.

The target invariant (ARCHITECTURE.md): **a new vendor = one vendor module
(+ translations + one docs page), zero edits to shared code.** Everything
shared — the manager, the generic routes, the generic frontend — reads this
spec; nothing downstream of the registry names a vendor.

A spec is declared in the vendor's own module and registered through the
plugin context (``ctx.register_browser_backend(name, cls, spec=...)``), so
core vendors register from the builtin plugin and private plugins can add
vendors the same way.

Field groups
------------
Identity / UI:
    ``key`` (the backend key stores are saved with), display names (the
    UI picks by language; ``antidetect_display_name`` composes the
    bilingual form the agent prompts use), ``kind`` (``'antidetect'`` vs
    ``'plain'`` — replaces the old ``ANTIDETECT_BACKENDS`` set),
    ``platforms`` (``get_platform()`` values), ``selectable`` (offered in
    the create-store form; ``winchrome`` is deployment-set, not selected).

Accounts / credentials (machine scope):
    ``account_cardinality`` — ``'none'`` (no login to configure, chrome),
    ``'single'`` (one machine login shared by all of the vendor's stores)
    or ``'multi'`` (an account list with add/edit/delete,
    ziniao's shape — supported by the field so a later ziniao migration
    needs no spec redesign). ``'single'`` vendors get the generic
    credential routes and the machine-scope storage
    (:mod:`app.browser.vendor_creds`); ``'multi'`` means per-account rows,
    a sibling route family that does not exist yet.
    ``credential_fields`` — the form + storage schema. ``profiles_scope``
    — profiles list per login (``'login'``) or per selected account
    (``'account'``).

Picker / wiring:
    ``lists_profiles`` (the store picker lists profiles), ``picker_kind``
    (``'generic'`` — rendered by the shared VendorSection — vs
    ``'custom'`` — the vendor keeps its own UI; ziniao until migrated),
    ``client_kind`` (which client family drives it), ``client_factory``
    (builds the :class:`~app.browser.inject.client.InjectClient` the
    engine runs), ``win_app`` (the install-location descriptor
    :class:`~app.browser.win_app_locator.WinAppSpec`, served through the
    locator's module attributes), ``client_path_setting`` (an optional
    app_settings override for the client's install path).

Text:
    ``i18n_prefix`` — the generic UI derives its string keys as
    ``<prefix><Suffix>`` (e.g. ``'settings.<vendor>'`` +
    ``'ClientNotRunning'``), so a vendor's strings live under its own
    prefix. ``docs`` — per-topic help links (``'errors'``,
    ``'one_account'``) with the topic label the UI shows and the full
    zh/en URLs; ``tests/unit/test_docs_links.py`` checks the URLs against
    ``docs/`` because they live in ``app/*.py`` source.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.plugins import get_extension_context

if TYPE_CHECKING:
    from app.browser.inject.client import InjectClient
    from app.browser.win_app_locator import WinAppSpec

# ── field values ─────────────────────────────────────────────────────

KIND_ANTIDETECT = 'antidetect'
KIND_PLAIN = 'plain'

CARDINALITY_NONE = 'none'  # no stored credentials: the local API
# acts as the login (the user signs in once in the client)
CARDINALITY_SINGLE = 'single'  # one machine login
CARDINALITY_MULTI = 'multi'  # account list, add/edit/delete (ziniao)

PROFILES_LOGIN = 'login'  # profiles visible to the machine login
PROFILES_ACCOUNT = 'account'  # profiles of the selected account

CLIENT_GUI_INJECT = 'gui_inject'  # Node-inspector DOM + CDP injection
CLIENT_LOCAL_API = 'local_api'  # 127.0.0.1 HTTP API (PR B's LocalApiClient)
CLIENT_LAUNCH_MODE = 'launch_mode'  # launched in a WebDriver mode (ziniao)

PICKER_NONE = 'none'  # no picker section (plain backends)
PICKER_GENERIC = 'generic'  # rendered by the shared VendorSection
PICKER_CUSTOM = 'custom'  # vendor keeps its own UI (ziniao, until migrated)

# get_platform() values a spec may list.
ALL_PLATFORMS = frozenset({'windows', 'wsl', 'mac', 'linux'})


# ── spec types ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class VendorCredentialField:
    """One credential field of a vendor's (machine- or account-scope) login.

    ``key`` names both the stored setting (``<vendor>_<key>`` in
    app_settings for machine-scope vendors) and the injected
    browser-config key. ``label_i18n`` is the full i18n key of the field's
    label. Secret fields are Fernet-encrypted at rest and never returned.
    """

    key: str
    label_i18n: str
    secret: bool = False
    required: bool = True


@dataclass(frozen=True)
class VendorDocsLink:
    """One docs help link a vendor's UI shows, in both site languages.

    ``topic`` is the label the UI puts on the link (the DocsHelpLink
    ``data-topic``), not a key into any frontend map — the URLs are the
    source of truth and are checked by ``test_docs_links.py``.
    """

    topic: str
    zh: str
    en: str


@dataclass(frozen=True)
class BrowserVendorSpec:
    """Everything shared code needs to know about one browser vendor."""

    key: str
    display_name_en: str
    display_name_zh: str
    kind: str = KIND_ANTIDETECT
    platforms: frozenset[str] = ALL_PLATFORMS
    selectable: bool = True
    account_cardinality: str = CARDINALITY_NONE
    credential_fields: tuple[VendorCredentialField, ...] = ()
    lists_profiles: bool = False
    profiles_scope: str = PROFILES_LOGIN
    picker_kind: str = PICKER_GENERIC
    client_kind: str = CLIENT_GUI_INJECT
    i18n_prefix: str = ''
    docs: Mapping[str, VendorDocsLink] = field(default_factory=dict)
    client_factory: Callable[[dict], InjectClient] | None = None
    win_app: WinAppSpec | None = None
    client_path_setting: str = ''

    def credential_field(self, key: str) -> VendorCredentialField | None:
        for f in self.credential_fields:
            if f.key == key:
                return f
        return None

    def public_metadata(self) -> dict:
        """The UI-facing metadata (no secrets, no wiring callables)."""
        return {
            'key': self.key,
            'display_name_en': self.display_name_en,
            'display_name_zh': self.display_name_zh,
            'kind': self.kind,
            'platforms': sorted(self.platforms),
            'selectable': self.selectable,
            'account_cardinality': self.account_cardinality,
            'credential_fields': [
                {
                    'key': f.key,
                    'label_i18n': f.label_i18n,
                    'secret': f.secret,
                    'required': f.required,
                }
                for f in self.credential_fields
            ],
            'lists_profiles': self.lists_profiles,
            'profiles_scope': self.profiles_scope,
            'picker_kind': self.picker_kind,
            'i18n_prefix': self.i18n_prefix,
            'docs': {
                name: {
                    'topic': link.topic,
                    'zh': link.zh,
                    'en': link.en,
                }
                for name, link in self.docs.items()
            },
        }


# ── registry access ──────────────────────────────────────────────────


def registered_vendor_specs() -> dict[str, BrowserVendorSpec]:
    """Every registered vendor's spec, keyed by backend key."""
    return get_extension_context().vendor_specs


def vendor_spec(key: str) -> BrowserVendorSpec | None:
    """The spec for ``key``, or None when the backend has none."""
    return registered_vendor_specs().get(key)


def vendor_specs_metadata() -> list[dict]:
    """UI metadata for every registered vendor, in registration order."""
    return [
        spec.public_metadata() for spec in registered_vendor_specs().values()
    ]
