import os
from pathlib import Path
import secrets

from app.env_options import Options

BASE_DIR = Path(__file__).resolve().parent.parent

# Runtime root. Demo mode (VIBE_HOME=~/.vibe-seller-demo) points this at a
# parallel directory so the entire on-disk surface — DB, wrappers, downloads,
# knowledge, tasks — is isolated from production data.
VIBE_SELLER_DIR = Path(
    os.environ.get('VIBE_HOME') or str(Path.home() / '.vibe-seller')
)
DEMO_MODE = os.environ.get('VIBE_DEMO_MODE', '').lower() in ('1', 'true', 'yes')
DEMO_AMAZON_BASE = (
    os.environ.get('VIBE_DEMO_AMAZON_BASE') or 'http://127.0.0.1:8866'
)

DATA_DIR = VIBE_SELLER_DIR / 'data'

# Frontend assets live in two places depending on install mode:
#   - Dev clone: `frontend/` source is present and `pnpm build` produces
#     `frontend/dist/`. Prefer this so a fresh `pnpm build` is picked up
#     without re-staging into the package.
#   - Wheel install: the release workflow stages built assets into
#     `app/static/` and includes them in the package via
#     setuptools.package-data, so the web UI works after a plain
#     `pip install vibe-seller` with no clone.
#
# Order: dev dist if actually built → packaged static → dev dist (empty).
# Probing `frontend/dist/index.html` rather than just the directory
# avoids the case where a dev clone has `frontend/src/` present but
# `pnpm build` hasn't run yet — in that state a stale empty
# `frontend/dist/` would shadow a valid packaged `app/static/`.
_PKG_STATIC = Path(__file__).resolve().parent / 'static'
_DEV_DIST = BASE_DIR / 'frontend' / 'dist'
if _DEV_DIST.exists() and (_DEV_DIST / 'index.html').exists():
    FRONTEND_DIST = _DEV_DIST
elif _PKG_STATIC.exists() and (_PKG_STATIC / 'index.html').exists():
    FRONTEND_DIST = _PKG_STATIC
else:
    # Neither is built. app/main.py checks `FRONTEND_DIST.exists()`
    # before mounting; pointing at the dev path keeps the existing
    # "build the frontend" failure mode unchanged.
    FRONTEND_DIST = _DEV_DIST
SCREENSHOTS_DIR = DATA_DIR / 'screenshots'
ATTACHMENTS_DIR = DATA_DIR / 'attachments'
EMAIL_DBS_DIR = DATA_DIR / 'email_dbs'
DB_PATH = DATA_DIR / 'vibe_seller.db'
DATABASE_URL = f'sqlite+aiosqlite:///{DB_PATH}'

# Ensure dirs exist
VIBE_SELLER_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
SCREENSHOTS_DIR.mkdir(exist_ok=True)
ATTACHMENTS_DIR.mkdir(exist_ok=True)
EMAIL_DBS_DIR.mkdir(exist_ok=True)

# Auth
ADMIN_EMAIL = Options.ADMIN_EMAIL.get()
ADMIN_PASSWORD = Options.ADMIN_PASSWORD.get()


def _resolve_jwt_secret() -> str:
    """JWT_SECRET = ``$JWT_SECRET`` env var, or a per-install random
    secret persisted at ``~/.vibe-seller/data/jwt_secret`` (mode 0600,
    generated on first boot).
    """
    env_val = os.environ.get('JWT_SECRET', '').strip()
    if env_val:
        return env_val
    secret_file = DATA_DIR / 'jwt_secret'
    if secret_file.exists():
        value = secret_file.read_text().strip()
        if value:
            return value
    value = secrets.token_urlsafe(48)
    secret_file.write_text(value)
    try:
        secret_file.chmod(0o600)
    except OSError:
        pass
    return value


JWT_SECRET = _resolve_jwt_secret()
AI_BOT_USER_ID = '00000000-0000-0000-0000-000000000002'

DEFAULT_USER_ID = '00000000-0000-0000-0000-000000000001'

LOCALHOST = '127.0.0.1'
LOCALHOST_NAME = 'localhost'
BACKEND_PORT = Options.BACKEND_PORT.get_int()
FRONTEND_URL = Options.FRONTEND_URL.get() or f'http://{LOCALHOST_NAME}:5173'

LOG_DIR = Path(Options.LOG_DIR.get() or (BASE_DIR / 'logs'))
LOG_DIR.mkdir(exist_ok=True)

# Browser-use CLI
BROWSER_USE_BIN_DIR = VIBE_SELLER_DIR / 'bin'
BROWSER_USE_BIN_DIR.mkdir(parents=True, exist_ok=True)

# Orchestrator (no-store) "web" browser. A single generic Chrome
# session, not tied to any store, that no-store tasks use for neutral
# public web work (search, tracking/logistics pages, research). Distinct
# from a store's per-store ``{slug}-aux`` session — hence a reserved slug
# that ``store_slug()`` can never produce for a real store (it strips
# leading dashes, so no store slug starts with ``_``). Doubles as the
# in-memory BrowserManager key and the wrapper/profile/downloads dir name.
WEB_BROWSER_SLUG = '_web'

# Per-store download directory (used by CDP proxy to override
# browser-use's random /tmp dirs with a stable path).
DOWNLOADS_DIR = VIBE_SELLER_DIR / 'downloads'
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Knowledge sync
KNOWLEDGE_REPO_URL = (
    Options.KNOWLEDGE_REPO_URL.get()
    or 'https://raw.githubusercontent.com/zpoint/vibe-seller/main/app/knowledge'
)

# Skills sync
SKILLS_REPO_URL = (
    Options.SKILLS_REPO_URL.get()
    or 'https://raw.githubusercontent.com/zpoint/vibe-seller/main/app/skills'
)
