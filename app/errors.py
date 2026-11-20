"""Error category constants for task failures."""

import re

# Claude Code stream-json error types → our categories.
# Keys are the raw `error` strings from assistant/system events.
STREAM_ERROR_MAP = {
    'rate_limit': 'rate_limit',
    'authentication_failed': 'auth_failed',
    'billing_error': 'billing_error',
    'invalid_request': 'invalid_request',
    'server_error': 'server_error',
    'max_output_tokens': 'max_tokens',
    'overloaded': 'overloaded',
    'unknown': 'unknown',
}

# Fallback regex patterns — last resort when no structured error
# is available from stream-json events.
# Each tuple: (compiled_regex, category)
FALLBACK_PATTERNS = [
    (
        re.compile(r'rate.?limit|too many request', re.IGNORECASE),
        'rate_limit',
    ),
    (
        re.compile(
            r'authentication failed|invalid.*api.?key|unauthorized',
            re.IGNORECASE,
        ),
        'auth_failed',
    ),
    (
        re.compile(
            r'billing|insufficient.?credit|usage.?limit',
            re.IGNORECASE,
        ),
        'billing_error',
    ),
    (
        re.compile(r'overloaded|over.?capacity', re.IGNORECASE),
        'overloaded',
    ),
]


def ziniao_error_category(exc: BaseException) -> str | None:
    """The ``task.error_category`` a Ziniao start failure carries.

    Read from the exception (``ZiniaoStartError.task_category`` and
    friends), never parsed out of its message.
    """
    return getattr(exc, 'task_category', None)


def categorize_error_text(text: str) -> str | None:
    """Categorize error from raw text using fallback regex patterns.

    Returns a category string or None if no pattern matches.
    """
    if not text:
        return None
    for pattern, category in FALLBACK_PATTERNS:
        if pattern.search(text):
            return category
    return None
