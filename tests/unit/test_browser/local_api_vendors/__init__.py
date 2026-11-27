"""Real-vendor contract fixtures — one file per vendor, no suite edits.

A vendor whose adapter subclasses
:class:`~app.browser.local_api_client.LocalApiClient` adds ONE file
here named ``<vendor_key>.py`` exporting::

    CONTRACT = ContractFixture(
        client_cls=MyClient,  # the real adapter class
        routes={...},  # its documented happy-path API
        expected_refs=[...],  # what its list must parse to
        expected_cdp_port=...,  # the port its open payload carries
        auth_env={...},  # credential fields that ride calls
        logged_out_routes={...},  # REQUIRED: the logged-out shape
        unknown_ref_routes={...},  # REQUIRED: the unknown-ref shape
        paid_gate_routes={...},  # the paid-gate shape, OR the
        # explicit NO_PAID_GATE sentinel
    )

``logged_out_routes`` and ``unknown_ref_routes`` may never be omitted
(the convention check refuses the fixture); ``paid_gate_routes`` is
either the documented shape or ``paid_gate_routes=NO_PAID_GATE`` (from
``local_api_fixture``) with a reason.

The contract suite collects every module in this package automatically
and runs ALL its parametrized contract tests over each fixture — a
vendor never edits ``local_api_contract.py`` (and the convention check
fails if a discovered vendor is missing its fixture file here).
"""
