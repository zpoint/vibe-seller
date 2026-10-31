"""Chat-bot (group robot) webhook senders.

Importing this package registers every platform sender with
``app.notifiers.base.PLATFORMS`` — the registry is the single source
of truth for which platforms exist and how they behave.
"""

from app.notifiers import (  # noqa: F401
    base,
    dingtalk,
    feishu,
    slack,
    wecom,
)

__all__ = ['base', 'dingtalk', 'feishu', 'slack', 'wecom']
