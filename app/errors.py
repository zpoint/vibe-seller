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


def categorize_ziniao_error(text: str) -> str | None:
    """Categorize Ziniao-specific error strings."""
    if not text:
        return None
    if 'not running' in text:
        return 'ziniao_not_running'
    if 'not listening' in text:
        return 'ziniao_wrong_port'
    if 'WSL cannot launch Ziniao' in text:
        return 'ziniao_cannot_launch'
    return None


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
