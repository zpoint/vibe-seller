"""Vendor account rules (R8) — the spec-level contract, import-free.

R8 (user decision, 2026-11-27): one account per machine, unless the
vendor's OFFICIAL API natively serves several accounts at once.

This module is a LEAF (no app imports) so both the plugin context
(registration-time validation) and the vendor registry (which re-exports
the constants) can use it without an import cycle.
"""

# ── field values ─────────────────────────────────────────────────────

KIND_ANTIDETECT = 'antidetect'
KIND_PLAIN = 'plain'

# The ACCOUNT RULE:
CARDINALITY_SINGLE = 'single'  # one account per machine (the default
# for anti-detect vendors — every one whose API cannot serve several
# accounts at once — the standard single-account shape)
CARDINALITY_MULTI = 'multi'  # the API takes the account on every call
# (an account list with add/edit/delete) — allowed only for a vendor
# with a MULTI_ACCOUNT_VENDORS entry citing its API docs
CARDINALITY_NONE = 'none'  # plain browsers only (chrome / winchrome):
# no account concept at all

# SEPARATE from the account rule: whether the spec declares
# ``credential_fields`` (something to STORE). Empty tuple means nothing
# is stored — the user signs in in the client and the local API acts as
# that login (a credential-less vendor is ``single`` + empty fields,
# NOT ``none``).

# R8 allowlist: the anti-detect vendors whose OFFICIAL API natively
# serves several accounts at once. The entry IS the justification —
# the URL cites the vendor's multi-account API docs. Today only Ziniao
# qualifies (its API takes the account on every call).
MULTI_ACCOUNT_VENDORS: dict[str, str] = {
    'ziniao': 'https://open.ziniao.com/docSupport?docId=99',
}


def validate_spec(spec) -> None:
    """Enforce the account-rule shapes (R8) at registration time.

    - an anti-detect vendor is ``single`` or ``multi`` — never ``none``
      (a credential-less anti-detect vendor is ``single`` with empty
      ``credential_fields``);
    - ``multi`` is allowed only for a vendor with a
      ``MULTI_ACCOUNT_VENDORS`` entry citing its multi-account API docs
      (today only Ziniao qualifies);
    - a plain browser is ``none`` (no account concept at all).
    """
    if spec.kind == KIND_ANTIDETECT:
        if spec.account_cardinality == CARDINALITY_NONE:
            raise ValueError(
                f'{spec.key!r}: an anti-detect vendor cannot be '
                f'account_cardinality={CARDINALITY_NONE!r} — a '
                'credential-less vendor is '
                f'{CARDINALITY_SINGLE!r} with empty credential_fields'
            )
        if spec.account_cardinality not in (
            CARDINALITY_SINGLE,
            CARDINALITY_MULTI,
        ):
            raise ValueError(
                f'{spec.key!r}: account_cardinality '
                f'{spec.account_cardinality!r} is not one of '
                f'{CARDINALITY_SINGLE!r}/{CARDINALITY_MULTI!r}'
            )
        if (
            spec.account_cardinality == CARDINALITY_MULTI
            and spec.key not in MULTI_ACCOUNT_VENDORS
        ):
            raise ValueError(
                f'{spec.key!r}: account_cardinality={CARDINALITY_MULTI!r} '
                'requires a MULTI_ACCOUNT_VENDORS entry citing the '
                "vendor's multi-account API docs (R8: one account per "
                'machine unless the official API serves several at once)'
            )
    elif spec.account_cardinality != CARDINALITY_NONE:
        raise ValueError(
            f'{spec.key!r}: a plain browser is '
            f'account_cardinality={CARDINALITY_NONE!r}'
        )
