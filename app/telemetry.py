"""Anonymous PostHog telemetry. Opt out: VIBE_SELLER_TELEMETRY=0."""

from datetime import datetime
from importlib.metadata import version
import logging
import os
import platform
import sys
import uuid

from posthog import Posthog

from app.config import DATA_DIR

logger = logging.getLogger(__name__)


POSTHOG_PROJECT_KEY = 'phc_NP0EO5Koq1dWqXEHwR14Po7bVqqtAdWINXiWypKU6H7'
POSTHOG_HOST = 'https://t.vibe-sellers.com'
INSTALL_ID_FILE = DATA_DIR / 'install_id'
SETTINGS_KEY = 'telemetry_enabled'

# Single source of truth: pyproject.toml's setuptools-scm config
# (with its `fallback_version`) feeds the package metadata at build
# time; importlib.metadata reads that here. If this raises at import,
# the install is broken — let it surface loudly rather than masking
# behind a duplicate fallback string.
APP_VERSION = version('vibe-seller')

_client = None
_install_id: str | None = None
_db_disabled: bool = False
# Latest known frontend UI locale (set via /api/telemetry/locale on
# i18n init and on language change). 'unknown' until any browser
# has reported. Attached to base_properties() so dashboards can group
# installs by language; updated person-property via PostHog identify
# so subsequent events inherit the value.
_app_locale: str = 'unknown'


def _disabled_via_env() -> bool:
    return os.environ.get('VIBE_SELLER_TELEMETRY', '').strip() == '0'


def _running_under_pytest() -> bool:
    return 'pytest' in sys.modules or 'PYTEST_CURRENT_TEST' in os.environ


def _running_in_ci() -> bool:
    """Detect common CI runners — used to skip telemetry from CI jobs
    that boot a real uvicorn (where ``_running_under_pytest`` doesn't
    fire). Each fresh CI runner gets its own install_id and would
    pollute the dashboard with apparent installs that aren't users.
    """
    ci_signals = (
        'CI',
        'GITHUB_ACTIONS',
        'BUILDKITE',
        'GITLAB_CI',
        'CIRCLECI',
        'JENKINS_URL',
    )
    return any(os.environ.get(name) for name in ci_signals)


def _resolve_install_id() -> str:
    try:
        if INSTALL_ID_FILE.exists():
            value = INSTALL_ID_FILE.read_text().strip()
            if value:
                return value
        value = str(uuid.uuid4())
        INSTALL_ID_FILE.write_text(value)
        return value
    except Exception:
        return str(uuid.uuid4())


def init() -> None:
    global _client, _install_id
    if _client is not None:
        return
    if _disabled_via_env() or _running_under_pytest() or _running_in_ci():
        return
    try:
        _install_id = _resolve_install_id()
        _client = Posthog(
            project_api_key=POSTHOG_PROJECT_KEY,
            host=POSTHOG_HOST,
            disable_geoip=True,
        )
    except Exception:
        logger.warning('Telemetry init failed', exc_info=True)
        _client = None


def set_db_disabled(disabled: bool) -> None:
    global _db_disabled
    _db_disabled = disabled


def is_enabled() -> bool:
    if _client is None or _disabled_via_env():
        return False
    return not _db_disabled


def install_id() -> str | None:
    return _install_id


def send(event: str, properties: dict | None = None) -> None:
    if not is_enabled() or _install_id is None or _client is None:
        return
    try:
        # Merge base properties (app_version, os, python_version, …) into
        # EVERY event, not just app_started, so any event can be
        # segmented by release version or platform. Caller-supplied keys
        # win over the base defaults.
        props = base_properties()
        if properties:
            props.update(properties)
        _client.capture(
            distinct_id=_install_id,
            event=event,
            properties=props,
        )
    except Exception:
        logger.debug('Telemetry send failed for %s', event, exc_info=True)


def runtime_os() -> str:
    s = platform.system().lower()
    if s == 'darwin':
        return 'mac'
    if s == 'windows':
        return 'windows'
    if s == 'linux':
        try:
            with open('/proc/version') as f:
                if 'microsoft' in f.read().lower():
                    return 'wsl'
        except OSError:
            pass
        return 'linux'
    return 'other'


def base_properties() -> dict:
    return {
        'app_version': APP_VERSION,
        'os': runtime_os(),
        'os_release': platform.release(),
        'python_version': f'{sys.version_info[0]}.{sys.version_info[1]}',
        'is_docker': os.path.exists('/.dockerenv'),
        'app_locale': _app_locale,
    }


def set_app_locale(locale: str) -> None:
    """Record the frontend UI locale on this install.

    Called from the `POST /api/telemetry/locale` endpoint when the
    frontend boots or the user toggles language. Updates the global
    state (so the next ``base_properties()`` call carries it) and
    pushes a PostHog ``identify`` so the locale becomes a person
    property attached to every subsequent event.
    """
    global _app_locale
    _app_locale = locale or 'unknown'
    if _client is None or _install_id is None:
        return
    try:
        _client.identify(
            distinct_id=_install_id,
            properties={'app_locale': _app_locale},
        )
    except Exception:
        logger.debug('Telemetry identify failed', exc_info=True)


def count_bucket(n: int) -> str:
    if n <= 0:
        return '0'
    if n <= 5:
        return '1-5'
    if n <= 20:
        return '6-20'
    if n <= 100:
        return '21-100'
    return '100+'


def duration_bucket(seconds: int | float | None) -> str:
    if seconds is None:
        return 'unknown'
    secs = max(0, int(seconds))
    if secs < 30:
        return '<30s'
    if secs < 120:
        return '30s-2m'
    if secs < 600:
        return '2m-10m'
    if secs < 1800:
        return '10m-30m'
    return '>30m'


def duration_bucket_from_iso(start_iso: str | None, end_iso: str | None) -> str:
    return duration_bucket(duration_seconds_from_iso(start_iso, end_iso))


def duration_seconds_from_iso(
    start_iso: str | None, end_iso: str | None
) -> int | None:
    if not start_iso or not end_iso:
        return None
    try:
        start = datetime.fromisoformat(start_iso)
        end = datetime.fromisoformat(end_iso)
        return max(0, int((end - start).total_seconds()))
    except (ValueError, TypeError):
        return None


def length_bucket(n: int) -> str:
    if n <= 0:
        return '0'
    if n < 50:
        return '<50'
    if n < 200:
        return '50-200'
    if n < 1000:
        return '200-1k'
    if n < 5000:
        return '1k-5k'
    return '5k+'


def age_bucket_days(days: float | None) -> str:
    """Bucket an age in days — for entities that live for days/weeks
    rather than the seconds-to-minutes range ``duration_bucket`` covers.
    """
    if days is None:
        return 'unknown'
    if days < 1:
        return '<1d'
    if days < 7:
        return '1-7d'
    if days < 30:
        return '1-4w'
    if days < 90:
        return '1-3m'
    if days < 365:
        return '3-12m'
    return '>1y'


def retention_days_bucket(days: int) -> str:
    if days <= 0:
        return '0-disabled'
    if days < 7:
        return '<7'
    if days <= 30:
        return '7-30'
    if days <= 90:
        return '30-90'
    return '90+'


def tz_continent_bucket(iana: str | None) -> str:
    if not iana:
        return 'UTC'
    if '/' not in iana:
        return iana if iana == 'UTC' else 'other'
    head = iana.split('/', 1)[0]
    if head in {
        'Asia',
        'America',
        'Europe',
        'Africa',
        'Australia',
        'Pacific',
        'Atlantic',
        'Indian',
        'Antarctica',
    }:
        return head
    return 'other'


_EMAIL_DOMAIN_KIND = {
    'gmail.com': 'gmail',
    'googlemail.com': 'gmail',
    'outlook.com': 'outlook',
    'hotmail.com': 'outlook',
    'live.com': 'outlook',
    'msn.com': 'outlook',
    'icloud.com': 'icloud',
    'me.com': 'icloud',
    'yahoo.com': 'yahoo',
    'yahoo.co.jp': 'yahoo',
    'ymail.com': 'yahoo',
}


def email_provider_kind(email: str | None) -> str:
    if not email or '@' not in email:
        return 'other'
    domain = email.rsplit('@', 1)[1].lower().strip()
    return _EMAIL_DOMAIN_KIND.get(domain, 'other')


def shutdown() -> None:
    if _client is None:
        return
    try:
        _client.flush()
    except Exception:
        pass
