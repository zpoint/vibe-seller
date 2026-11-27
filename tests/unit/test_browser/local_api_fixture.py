"""The shared shape of a LocalApiClient contract fixture.

One type for every contract fixture — the in-suite doc-derived ones and
a real vendor's file under ``local_api_vendors/`` — so the contract
suite treats them identically and a real vendor joins every parametrized
contract test by adding ONE file, never editing the suite.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NoPaidGate:
    """A real vendor's explicit declaration that it HAS no paid gate.

    A registered vendor's CONTRACT must never silently omit
    ``paid_gate_routes``: either it documents the paid-gate shape, or it
    declares ``paid_gate_routes=NO_PAID_GATE`` with a reason (e.g. "the
    vendor's local API has no paid tier"). Skipping the check by
    omission is refused by the convention check.
    """

    reason: str


NO_PAID_GATE = NoPaidGate(reason="the vendor's local API has no paid tier")


@dataclass(frozen=True)
class ContractFixture:
    """Everything the contract suite runs against one vendor.

    Attributes:
        client_cls: the ``LocalApiClient`` subclass under contract (the
            real class for a vendor fixture; a doc-derived test class
            for the in-suite ones).
        routes: the happy-path fake local API — ``{(method, path):
            (status, payload) | async handler(request) -> (status,
            payload)}``, exactly what the suite's fake server serves.
        expected_refs: the store names ``list_stores`` must produce, in
            order (also pins de-duplication when a page repeats a row).
        expected_cdp_port: the CDP port the documented open payload
            carries (the open test asserts ``open_store`` reaches it).
        auth_env: browser-config overrides injected before the call
            (the credential fields the manager would inject, keyed
            ``<vendor_key>_<field>``); the auth test asserts the value
            rides the request per the client's declared auth mode.
        logged_out_routes: routes whose list answer is the vendor's
            documented logged-out shape → ``LoginRequired``; for a
            REGISTERED (real) vendor this is REQUIRED (the
            "logged-out ≠ empty list" guarantee is the point of the
            suite — it may never be skipped by omission).
        paid_gate_routes: routes whose list answer is the vendor's
            documented paid-gate shape → ``ApiNotEnabled``; for a
            REGISTERED (real) vendor this is required — either routes
            or the explicit ``NO_PAID_GATE`` sentinel.
        unknown_ref_routes: routes whose open answer is the vendor's
            documented unknown-ref shape → ``StoreNotFound``; for a
            REGISTERED (real) vendor this is REQUIRED.
    """

    client_cls: type
    routes: dict
    expected_refs: list
    expected_cdp_port: int
    auth_env: dict = field(default_factory=dict)
    logged_out_routes: dict | None = None
    paid_gate_routes: 'dict | NoPaidGate | None' = None
    unknown_ref_routes: dict | None = None
