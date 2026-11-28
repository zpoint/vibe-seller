"""BitBrowser profile-running status for the attach-ownership hook."""

from app.browser.inject.errors import StoreUnavailable
from app.browser.local_api_client import LocalApiClient


async def env_was_open(client: LocalApiClient, ref: str) -> bool:
    """Whether this profile is already open, from the running list.

    The client's ``/browser/pids/all`` response maps profile ids to
    kernel pids. A status-API failure raises (never ``None``): the
    engine then treats the env as not ours, so a hand-opened window is
    never closed on a guess.
    """
    status, payload = await client.api_call(
        'POST', '/browser/pids/all', json_body={}
    )
    err = client.map_error(status, payload)
    if err is not None:
        raise err
    if not isinstance(payload, dict) or payload.get('success') is not True:
        raise StoreUnavailable(
            '/browser/pids/all did not return a running-profile list'
        )
    running = payload.get('data')
    if not isinstance(running, dict):
        raise StoreUnavailable(
            '/browser/pids/all returned an invalid running-profile list'
        )
    return str(ref) in {str(profile_id) for profile_id in running}
