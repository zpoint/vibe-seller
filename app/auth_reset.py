"""Account recovery for a locked-out install.

The login screen can appear on an install where nobody remembers the
password, and the two documented ways back in both have a catch:

* The seeded default (``admin`` / ``admin``) only holds until someone
  changes the password in the UI. After that the env defaults are
  ignored, and bcrypt is one-way — the old password cannot be read
  back out of the database.
* ``FORCE_ADMIN_RESET=true`` is the supported reset path, but it is
  applied by the boot seed, so it needs a server restart — and the
  agent tasks run as children of that server, so the restart kills
  whatever was running.

This module is the third way in: a standalone command that edits the
same SQLite file the app reads. The server can stay up and the running
tasks keep going.

The work is done on the raw SQLite file (no SQLAlchemy, no asyncio) on
purpose — the command has to work when the app itself cannot start, and
the server may be holding the database open while it runs.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import sys

from app.password import hash_password, verify_password

# The credentials the boot seed writes on a fresh install
# (app/env_options.py). Kept here so `status` can answer the question
# people actually have — "would the default still get me in?" — without
# anyone having to guess.
SEED_USERNAME = 'admin'
SEED_EMAIL = 'admin@vibe-seller.local'
SEED_PASSWORD = 'admin'

# bcrypt silently ignores everything past 72 bytes.
BCRYPT_MAX_BYTES = 72


class RecoveryError(Exception):
    """A recovery action could not be carried out."""


def _default_admin_id() -> str:
    """The id the boot seed gives the default admin account.

    Imported lazily: `app.config` creates the runtime directories on
    import, and this module must stay importable on a machine where
    nothing has been installed yet.
    """
    from app.config import DEFAULT_USER_ID  # noqa: PLC0415

    return DEFAULT_USER_ID


def resolve_db_path(explicit: str | None = None) -> Path:
    """Pick the SQLite file to operate on.

    `--db` wins; otherwise the app's own runtime location is used
    (`VIBE_HOME`-aware, so a demo-mode install is not touched by
    accident).
    """
    if explicit:
        return Path(explicit).expanduser()
    from app.config import DB_PATH  # noqa: PLC0415

    return Path(DB_PATH)


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the app database for writing.

    The timeout covers the case where the running server holds the
    write lock for a moment; the writes here are single-row and finish
    in milliseconds.
    """
    if not db_path.exists():
        raise RecoveryError(
            f'No database at {db_path}. Pass --db to point at the file.'
        )
    conn = sqlite3.connect(str(db_path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    return conn


def _get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        'SELECT value FROM app_settings WHERE key = ?', (key,)
    ).fetchone()
    return None if row is None else row['value']


def _set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        'INSERT INTO app_settings (key, value) VALUES (?, ?) '
        'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
        (key, value),
    )


def _is_true(value: str | None) -> bool:
    return (value or '').strip().lower() in ('true', '1', 'yes')


def _password_matches(plain: str, hashed: str | None) -> bool:
    """`verify_password` that survives a missing or malformed hash."""
    if not hashed:
        return False
    try:
        return verify_password(plain, hashed)
    except ValueError:
        return False


def login_users(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Accounts that can log in — the AI bot cannot."""
    return conn.execute(
        'SELECT id, username, email, role, is_active, password_hash '
        "FROM users WHERE role != 'ai_bot' ORDER BY username"
    ).fetchall()


def collect_status(conn: sqlite3.Connection) -> dict:
    """Everything `status` prints, as data."""
    default_id = _default_admin_id()
    users = []
    for row in login_users(conn):
        users.append({
            'username': row['username'],
            'email': row['email'],
            'role': row['role'],
            'is_active': bool(row['is_active']),
            'is_default_admin': row['id'] == default_id,
            'seed_password_works': _password_matches(
                SEED_PASSWORD, row['password_hash']
            ),
        })
    return {
        'auth_required': _is_true(_get_setting(conn, 'auth_required')),
        'admin_credentials_set': _is_true(
            _get_setting(conn, 'admin_credentials_set')
        ),
        'users': users,
    }


def set_auth_required(conn: sqlite3.Connection, required: bool) -> None:
    """Turn the login screen on or off.

    The setting is read per request, so this takes effect immediately —
    no restart. The frontend caches it, so the browser needs a reload.
    """
    _set_setting(conn, 'auth_required', 'true' if required else 'false')
    conn.commit()


def reset_password(
    conn: sqlite3.Connection,
    username: str | None,
    password: str,
    rename: bool = False,
) -> dict:
    """Reset an account's password.

    `username` selects the account; the default is the account the boot
    seed created — the one that owns the admin role, whatever it was
    renamed to. With `rename`, an account name that does not exist yet
    renames that seeded account instead of failing, which is how a
    locked-out operator restores the canonical `admin` login.
    """
    default_id = _default_admin_id()
    requested = username.strip().lower() if username else None

    if requested:
        row = conn.execute(
            'SELECT * FROM users WHERE username = ?', (requested,)
        ).fetchone()
        if row is None:
            if not rename:
                known = ', '.join(u['username'] for u in login_users(conn))
                raise RecoveryError(
                    f"No account named '{requested}'. Known accounts: "
                    f'{known}. Pass --rename to rename the default admin '
                    'account to that name instead.'
                )
            row = conn.execute(
                'SELECT * FROM users WHERE id = ?', (default_id,)
            ).fetchone()
            if row is None:
                raise RecoveryError(
                    'The default admin account is missing from the '
                    'database — there is nothing to reset.'
                )
    else:
        row = conn.execute(
            'SELECT * FROM users WHERE id = ?', (default_id,)
        ).fetchone()
        if row is None:
            raise RecoveryError(
                'The default admin account is missing from the database '
                '— there is nothing to reset.'
            )

    if row['role'] == 'ai_bot':
        raise RecoveryError(
            'The AI bot account cannot log in and has no password.'
        )

    previous_username = row['username']
    renamed = False
    if requested and requested != previous_username:
        conn.execute(
            'UPDATE users SET username = ? WHERE id = ?',
            (requested, row['id']),
        )
        renamed = True

    conn.execute(
        'UPDATE users SET password_hash = ?, updated_at = ?, is_active = 1 '
        'WHERE id = ?',
        (
            hash_password(password),
            datetime.now(UTC).isoformat(),
            row['id'],
        ),
    )

    # The boot seed re-applies ADMIN_USERNAME / ADMIN_PASSWORD on every
    # start while this flag is unset, so a password reset that left it
    # unset would be overwritten by the next restart. Pin it.
    if row['id'] == default_id:
        _set_setting(conn, 'admin_credentials_set', 'true')

    conn.commit()
    return {
        'username': requested or previous_username,
        'previous_username': previous_username,
        'renamed': renamed,
        'is_default_admin': row['id'] == default_id,
        'was_inactive': not bool(row['is_active']),
    }


def prompt_password() -> str:
    """Ask for a new password on the terminal, twice."""
    import getpass  # noqa: PLC0415

    try:
        first = getpass.getpass('New password: ')
    except (EOFError, OSError) as exc:
        raise RecoveryError(
            'Cannot read a password from this terminal — pass '
            '--password instead.'
        ) from exc
    second = getpass.getpass('Confirm password: ')
    if not first:
        raise RecoveryError('The password must not be empty.')
    if first != second:
        raise RecoveryError('The two passwords did not match.')
    return first


def _print_status(status: dict, db_path: Path) -> None:
    print(f'Database: {db_path}')
    print(
        'Login screen: '
        + ('ON (a password is required)' if status['auth_required'] else 'OFF')
    )
    print(
        'Default credentials: '
        + (
            'overridden (the seeded admin/admin no longer applies)'
            if status['admin_credentials_set']
            else 'untouched (admin/admin still applies)'
        )
    )
    print('Accounts that can log in:')
    for user in status['users']:
        flags = []
        if user['is_default_admin']:
            flags.append('default admin')
        if not user['is_active']:
            flags.append('disabled')
        if user['seed_password_works']:
            flags.append('password is the default "admin"')
        suffix = f' [{", ".join(flags)}]' if flags else ''
        email = user['email'] or '-'
        print(f'  - {user["username"]} <{email}> ({user["role"]}){suffix}')
    if not status['users']:
        print('  (none)')


def _run_status(args: argparse.Namespace) -> int:
    db_path = resolve_db_path(args.db)
    with connect(db_path) as conn:
        _print_status(collect_status(conn), db_path)
    return 0


def _run_password(args: argparse.Namespace) -> int:
    password = args.password or prompt_password()
    if len(password.encode('utf-8')) > BCRYPT_MAX_BYTES:
        print(
            f'Note: bcrypt only uses the first {BCRYPT_MAX_BYTES} bytes '
            'of a password — the rest is ignored.',
            file=sys.stderr,
        )
    db_path = resolve_db_path(args.db)
    with connect(db_path) as conn:
        result = reset_password(conn, args.user, password, rename=args.rename)
    if result['renamed']:
        print(
            f'Renamed the default admin account '
            f'"{result["previous_username"]}" -> "{result["username"]}".'
        )
    if result['was_inactive']:
        print('Reactivated the account — it was disabled.')
    print(f'Password reset for "{result["username"]}".')
    print('Log in with that username and the new password.')
    return 0


def _run_disable(args: argparse.Namespace) -> int:
    db_path = resolve_db_path(args.db)
    with connect(db_path) as conn:
        set_auth_required(conn, False)
    print('Login screen OFF.')
    print(
        'Anyone who can reach this server now acts as the default '
        'admin. Reload the browser page.'
    )
    print('Turn it back on with: vibe-seller reset-auth enable')
    return 0


def _run_enable(args: argparse.Namespace) -> int:
    db_path = resolve_db_path(args.db)
    with connect(db_path) as conn:
        set_auth_required(conn, True)
    print('Login screen ON.')
    print(
        'Reload the browser page. If you do not know the password, '
        'reset it first: vibe-seller reset-auth password'
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The `reset-auth` command's own parser."""
    common = argparse.ArgumentParser(add_help=False)
    # SUPPRESS so the subcommand copy never overwrites a --db given
    # before the action — argparse copies the sub-namespace over the
    # parent one, and a plain default would clobber it.
    common.add_argument(
        '--db',
        default=argparse.SUPPRESS,
        help='Path to vibe_seller.db (default: the app runtime path).',
    )

    parser = argparse.ArgumentParser(
        prog='vibe-seller reset-auth',
        description=(
            'Recover access to a Vibe Seller install whose admin '
            'password is unknown: reset it, or turn the login screen '
            'off. Edits the app database directly, so the server can '
            'stay up and running tasks are not interrupted.'
        ),
    )
    parser.add_argument(
        '--db',
        default=None,
        help='Path to vibe_seller.db (default: the app runtime path).',
    )
    subs = parser.add_subparsers(dest='action', metavar='<action>')
    subs.required = True

    status = subs.add_parser(
        'status',
        parents=[common],
        help=(
            'Show whether a password is required and which accounts can log in.'
        ),
    )
    status.set_defaults(func=_run_status)

    password = subs.add_parser(
        'password',
        parents=[common],
        help='Reset an account password.',
    )
    password.add_argument(
        '--user',
        default=None,
        help=(
            'Account to reset (default: the seeded admin account, '
            'whatever it was renamed to).'
        ),
    )
    password.add_argument(
        '--password',
        default=None,
        help='The new password (prompted for when omitted).',
    )
    password.add_argument(
        '--rename',
        action='store_true',
        help=(
            'Allow renaming the default admin account to --user when '
            'no account has that name yet.'
        ),
    )
    password.set_defaults(func=_run_password)

    disable = subs.add_parser(
        'disable',
        parents=[common],
        help=(
            'Turn the login screen off — anyone who can reach the '
            'server acts as the default admin.'
        ),
    )
    disable.set_defaults(func=_run_disable)

    enable = subs.add_parser(
        'enable',
        parents=[common],
        help='Turn the login screen back on.',
    )
    enable.set_defaults(func=_run_enable)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except RecoveryError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
