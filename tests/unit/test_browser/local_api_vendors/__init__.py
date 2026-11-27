"""Real-vendor contract fixtures — one file per vendor, no suite edits.

A vendor whose adapter subclasses
:class:`~app.browser.local_api_client.LocalApiClient` adds ONE file
here named ``<vendor_key>.py`` exporting::

    CONTRACT = ContractFixture(
        client_cls=MyClient,  # the real adapter class
        routes={...},  # its documented happy-path API
        expected_refs=[...],  # what its list must parse to
        auth_env={...},  # credential fields that ride calls
        logged_out_routes={...},  # optional documented shapes
        paid_gate_routes={...},
        unknown_ref_routes={...},
    )

The contract suite collects every module in this package automatically
and runs ALL its parametrized contract tests over each fixture — a
vendor never edits ``local_api_contract.py`` (and the convention check
fails if a discovered vendor is missing its fixture file here).
"""
