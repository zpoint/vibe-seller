"""The OSS builtin plugin — core registers itself through the registry.

Every gate, PreToolUse guard, browser backend, and skill source that
ships in OSS core is registered here, through the exact same
:class:`~app.plugins.ExtensionContext` API a plugin uses. This
dogfoods the contract (the registry is never empty) and is the single
place core's contributions are declared.

Core registers only its own, customer-agnostic contributions. Customer
gates/guards/skills (e.g. ad-audit completeness for a specific platform,
a money-transfer guard) arrive via externally-installed plugin wheels,
never from here.
"""

from __future__ import annotations

from app.ai.bash_safety import (
    check_bid_value_shape,
    check_catalog_first,
    check_dangerous_kill,
    check_report_script_write,
)
from app.ai.stop_gates import (
    ad_completeness_review,
    ad_execution_fidelity,
    ad_negation_allowlist,
    review_completeness_review,
    review_output_gate,
)
from app.browser.chrome import ChromeBackend
from app.browser.winchrome import WinChromeBackend
from app.browser.ziniao import ZiniaoBackend
from app.plugins import ExtensionContext, Plugin


class BuiltinPlugin(Plugin):
    """OSS core, registered as the always-present builtin plugin.

    Note: core's own ``app/skills`` dir is synced directly by
    ``skills_sync`` (it is not a plugin contribution); plugins register
    ADDITIONAL skill dirs via ``ctx.register_skill_source``.
    """

    @property
    def name(self) -> str:
        return 'builtin'

    def install(self, ctx: ExtensionContext) -> None:
        self._install_gates(ctx)
        self._install_pretool_gates(ctx)
        self._install_browser_backends(ctx)

    @staticmethod
    def _install_gates(ctx: ExtensionContext) -> None:
        ctx.register_gate('ad_completeness_review', ad_completeness_review)
        ctx.register_gate('ad_negation_allowlist', ad_negation_allowlist)
        ctx.register_gate('ad_execution_fidelity', ad_execution_fidelity)
        ctx.register_gate(
            'review_completeness_review', review_completeness_review
        )
        ctx.register_gate('review_output_gate', review_output_gate)

    @staticmethod
    def _install_pretool_gates(ctx: ExtensionContext) -> None:
        # Order matches the historical first_bash_deny chain:
        # kill → bid value → report-script → catalog-first.
        ctx.register_pretool_gate(
            'Bash safety',
            lambda cmd, task_dir, catalog_read: check_dangerous_kill(cmd),
        )
        ctx.register_pretool_gate(
            'Bid-value sanity',
            lambda cmd, task_dir, catalog_read: check_bid_value_shape(cmd),
        )
        ctx.register_pretool_gate(
            'Report-script guard',
            lambda cmd, task_dir, catalog_read: check_report_script_write(
                cmd, task_dir
            ),
        )
        ctx.register_pretool_gate(
            'Catalog-first',
            lambda cmd, task_dir, catalog_read: check_catalog_first(
                cmd, catalog_read
            ),
        )

    @staticmethod
    def _install_browser_backends(ctx: ExtensionContext) -> None:
        ctx.register_browser_backend('chrome', ChromeBackend)
        ctx.register_browser_backend('winchrome', WinChromeBackend)
        ctx.register_browser_backend('ziniao', ZiniaoBackend)
