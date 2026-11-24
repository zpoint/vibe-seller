"""How a custom MCP server is SHOWN — credentials masked.

Kept apart from :mod:`app.custom_mcp`, which sits on the task launch
path: masking reuses the shared classifier in
:mod:`app.agent_mcp.redact`, and that package imports the launch.
Only the REST route renders servers.
"""

from app.agent_mcp.redact import (
    is_secret_env_key,
    is_secret_key,
    mask_secret,
)
from app.url_redact import mask_url


def _mask_args(args: list[str]) -> list[str]:
    """Args with the values of secret-looking flags masked.

    ``--api-key X``, ``--token=X``, ``-e API_KEY=X`` (docker) and URLs
    are covered; anything else is shown as typed.
    """
    out: list[str] = []
    hide_next = False
    for arg in args:
        if hide_next:
            out.append(mask_secret(arg))
            hide_next = False
            continue
        if arg.startswith(('http://', 'https://')):
            out.append(mask_url(arg))
            continue
        if arg.startswith('-'):
            flag, sep, value = arg.partition('=')
            secret = is_secret_key(flag.lstrip('-').replace('-', '_'))
            if sep:
                out.append(f'{flag}={mask_secret(value)}' if secret else arg)
            else:
                out.append(arg)
                hide_next = secret
            continue
        key, sep, value = arg.partition('=')
        if sep and is_secret_env_key(key):
            out.append(f'{key}={mask_secret(value)}')
        else:
            out.append(arg)
    return out


def public_view(config: dict) -> dict:
    """The server as every API response shows it: credentials masked.

    Header and env VALUES are masked whatever their names — a header
    is how a remote MCP server is authenticated, so ``Authorization``
    is the common case, but vendors use any name (``X-Api-Key``,
    ``mcp-token``).
    """
    view: dict = {'type': config.get('type', 'stdio')}
    if 'url' in config:
        view['url'] = mask_url(config['url'])
    if 'headers' in config:
        view['headers'] = {
            k: mask_secret(v) for k, v in config['headers'].items()
        }
    if 'command' in config:
        view['command'] = config['command']
        view['args'] = _mask_args(list(config.get('args', [])))
    if 'env' in config:
        view['env'] = {k: mask_secret(v) for k, v in config['env'].items()}
    return view
