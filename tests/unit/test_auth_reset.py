"""Tests for `vibe-seller reset-auth` — the account-recovery command.

The schema is built from the real SQLAlchemy models so a column rename
in the app cannot silently turn these tests into a green lie: the
recovery command talks to the same file the server does.
"""

from datetime import UTC, datetime
import sqlite3

import pytest
from sqlalchemy import create_engine

from app.auth_reset import (
    RecoveryError,
    collect_status,
    login_users,
    main,
    reset_password,
    set_auth_required,
)
from app.models.app_settings import AppSettings
from app.models.base import Base
from app.models.user import User
from app.password import hash_password, verify_password

pytestmark = pytest.mark.unit

DEFAULT_ADMIN_ID = '00000000-0000-0000-0000-000000000001'
AI_BOT_ID = '00000000-0000-0000-0000-000000000002'


@pytest.fixture
def db_path(tmp_path):
    """A database shaped like the app's, with one admin and one bot."""
    path = tmp_path / 'vibe_seller.db'
    engine = create_engine(f'sqlite:///{path}')
    Base.metadata.create_all(
        engine, tables=[User.__table__, AppSettings.__table__]
    )
    now = datetime.now(UTC).isoformat()
    with engine.begin() as conn:
        conn.execute(
            User.__table__.insert(),
            [
                {
                    'id': DEFAULT_ADMIN_ID,
                    'username': 'operator',
                    'email': 'admin@vibe-seller.local',
                    'password_hash': 'x',
                    'role': 'admin',
                    'is_active': 1,
                    'created_at': now,
                    'updated_at': now,
                },
                {
                    'id': AI_BOT_ID,
                    'username': 'ai_bot',
                    'email': 'ai@vibe-seller.local',
                    'password_hash': 'disabled',
                    'role': 'ai_bot',
                    'is_active': 1,
                    'created_at': now,
                    'updated_at': now,
                },
            ],
        )
        conn.execute(
            AppSettings.__table__.insert(),
            [{'key': 'auth_required', 'value': 'true'}],
        )
    engine.dispose()
    return path


def _connect(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _hash_of(path, username):
    with _connect(path) as conn:
        row = conn.execute(
            'SELECT password_hash FROM users WHERE username = ?',
            (username,),
        ).fetchone()
    return row['password_hash']


def _setting(path, key):
    with _connect(path) as conn:
        row = conn.execute(
            'SELECT value FROM app_settings WHERE key = ?', (key,)
        ).fetchone()
    return None if row is None else row['value']


def _usernames(path):
    with _connect(path) as conn:
        return [
            r[0] for r in conn.execute('SELECT username FROM users').fetchall()
        ]


def _add_user(path, username, role='admin'):
    """Add a user the way the app does — SQLAlchemy fills the defaults."""
    engine = create_engine(f'sqlite:///{path}')
    now = datetime.now(UTC).isoformat()
    with engine.begin() as conn:
        conn.execute(
            User.__table__.insert(),
            [
                {
                    'id': f'id-{username}',
                    'username': username,
                    'email': None,
                    'password_hash': 'x',
                    'role': role,
                    'is_active': 1,
                    'created_at': now,
                    'updated_at': now,
                }
            ],
        )
    engine.dispose()


class TestStatus:
    def test_reports_auth_state_and_login_capable_users(self, db_path):
        with _connect(db_path) as conn:
            status = collect_status(conn)
        assert status['auth_required'] is True
        assert status['admin_credentials_set'] is False
        # The AI bot cannot log in, so it is not listed.
        assert [u['username'] for u in status['users']] == ['operator']
        assert status['users'][0]['is_default_admin'] is True
        assert status['default_admin']['username'] == 'operator'

    def test_flags_an_account_whose_password_is_the_seed_default(self, db_path):
        with _connect(db_path) as conn:
            conn.execute(
                'UPDATE users SET password_hash = ? WHERE username = ?',
                (hash_password('admin'), 'operator'),
            )
            conn.commit()
            status = collect_status(conn)
        assert status['default_admin']['seed_password_works'] is True

    def test_a_missing_default_admin_is_reported_as_missing(self, db_path):
        # Another admin can hard-delete the default admin (the delete
        # route only blocks the AI bot and self) — the claim "the seeded
        # admin/admin still applies" must not survive that.
        with _connect(db_path) as conn:
            conn.execute('DELETE FROM users WHERE id = ?', (DEFAULT_ADMIN_ID,))
            conn.commit()
            status = collect_status(conn)
        assert status['default_admin'] is None
        assert status['users'] == []

    def test_reports_the_force_admin_reset_hazard(self, db_path, monkeypatch):
        monkeypatch.setenv('FORCE_ADMIN_RESET', 'true')
        with _connect(db_path) as conn:
            status = collect_status(conn)
        assert status['force_admin_reset'] is True


class TestResetPassword:
    def test_resets_the_default_admin_and_pins_the_credentials_flag(
        self, db_path
    ):
        with _connect(db_path) as conn:
            result = reset_password(conn, None, 'new-secret')
        assert result['username'] == 'operator'
        assert result['is_default_admin'] is True
        assert verify_password('new-secret', _hash_of(db_path, 'operator'))
        # The boot seed re-applies the env defaults while this flag is
        # unset — a reset that left it unset would not survive a
        # restart.
        assert _setting(db_path, 'admin_credentials_set') == 'true'

    def test_resets_a_named_account(self, db_path):
        with _connect(db_path) as conn:
            result = reset_password(conn, 'operator', 'other-secret')
        assert result['username'] == 'operator'
        assert verify_password('other-secret', _hash_of(db_path, 'operator'))

    def test_unknown_account_is_refused_with_the_known_names(self, db_path):
        with _connect(db_path) as conn:
            with pytest.raises(RecoveryError) as exc:
                reset_password(conn, 'admin', 'x')
        assert 'operator' in str(exc.value)

    def test_rename_restores_the_canonical_admin_login(self, db_path):
        with _connect(db_path) as conn:
            result = reset_password(conn, 'admin', 'x', rename=True)
        assert result['renamed'] is True
        assert result['previous_username'] == 'operator'
        assert result['username'] == 'admin'
        assert verify_password('x', _hash_of(db_path, 'admin'))

    def test_rename_refuses_a_name_that_could_never_log_in(self, db_path):
        # The login screen routes an identifier containing '@' to the
        # email lookup, so a username with one could never log in — the
        # tool must not write it.
        with _connect(db_path) as conn:
            with pytest.raises(RecoveryError) as exc:
                reset_password(conn, 'ops@corp.com', 'x', rename=True)
        assert 'email' in str(exc.value).lower()
        # The account was not touched.
        assert _hash_of(db_path, 'operator') == 'x'

    def test_rename_refuses_a_name_the_user_schema_would_reject(self, db_path):
        for bad in ('ab', 'has space', 'UPPER!', 'x' * 51):
            with _connect(db_path) as conn:
                with pytest.raises(RecoveryError):
                    reset_password(conn, bad, 'x', rename=True)
            assert 'operator' in _usernames(db_path)

    def test_an_email_identifies_an_account_like_the_login_screen(
        self, db_path
    ):
        with _connect(db_path) as conn:
            result = reset_password(conn, 'admin@vibe-seller.local', 'x')
        assert result['username'] == 'operator'
        assert verify_password('x', _hash_of(db_path, 'operator'))

    def test_an_unknown_email_is_refused(self, db_path):
        with _connect(db_path) as conn:
            with pytest.raises(RecoveryError):
                reset_password(conn, 'nobody@vibe-seller.local', 'x')

    def test_an_empty_or_blank_user_is_refused_not_treated_as_absent(
        self, db_path
    ):
        # `--user '   '` must not silently reset the default admin.
        for blank in ('', '   '):
            with _connect(db_path) as conn:
                with pytest.raises(RecoveryError):
                    reset_password(conn, blank, 'x')
            assert _hash_of(db_path, 'operator') == 'x'

    def test_reports_the_force_admin_reset_hazard(self, db_path, monkeypatch):
        monkeypatch.setenv('FORCE_ADMIN_RESET', 'true')
        with _connect(db_path) as conn:
            result = reset_password(conn, None, 'x')
        assert result['force_admin_reset'] is True

    def test_reactivates_a_disabled_account(self, db_path):
        with _connect(db_path) as conn:
            conn.execute(
                'UPDATE users SET is_active = 0 WHERE username = ?',
                ('operator',),
            )
            conn.commit()
            result = reset_password(conn, None, 'x')
        assert result['was_inactive'] is True
        with _connect(db_path) as conn:
            row = conn.execute(
                'SELECT is_active FROM users WHERE username = ?',
                ('operator',),
            ).fetchone()
        assert row['is_active'] == 1

    def test_ai_bot_cannot_be_reset(self, db_path):
        with _connect(db_path) as conn:
            with pytest.raises(RecoveryError):
                reset_password(conn, 'ai_bot', 'x')

    def test_non_admin_account_does_not_pin_the_credentials_flag(self, db_path):
        _add_user(db_path, 'milly', role='admin')
        with _connect(db_path) as conn:
            reset_password(conn, 'milly', 'x')
        assert _setting(db_path, 'admin_credentials_set') is None


class TestAuthToggle:
    def test_disable_then_enable(self, db_path):
        with _connect(db_path) as conn:
            set_auth_required(conn, False)
        assert _setting(db_path, 'auth_required') == 'false'
        with _connect(db_path) as conn:
            set_auth_required(conn, True)
        assert _setting(db_path, 'auth_required') == 'true'


class TestCommandLine:
    def test_status_exits_zero(self, db_path, capsys):
        rc = main(['status', '--db', str(db_path)])
        assert rc == 0
        assert 'Login screen: ON' in capsys.readouterr().out

    def test_db_flag_before_the_action_also_works(self, db_path):
        rc = main(['--db', str(db_path), 'status'])
        assert rc == 0

    def test_password_reset_through_the_command_line(self, db_path):
        rc = main([
            'password',
            '--user',
            'admin',
            '--rename',
            '--password',
            'brand-new',
            '--db',
            str(db_path),
        ])
        assert rc == 0
        assert verify_password('brand-new', _hash_of(db_path, 'admin'))

    def test_disable_through_the_command_line(self, db_path):
        assert main(['disable', '--db', str(db_path)]) == 0
        assert _setting(db_path, 'auth_required') == 'false'
        assert main(['enable', '--db', str(db_path)]) == 0
        assert _setting(db_path, 'auth_required') == 'true'

    def test_unknown_account_exits_non_zero(self, db_path, capsys):
        rc = main([
            'password',
            '--user',
            'nobody',
            '--password',
            'x',
            '--db',
            str(db_path),
        ])
        assert rc == 1
        assert 'No account matches' in capsys.readouterr().err

    def test_an_empty_password_is_refused_not_prompted(self, db_path, capsys):
        # An empty --password must not fall through to the interactive
        # prompt, which a script can never answer.
        rc = main([
            'password',
            '--password',
            '',
            '--db',
            str(db_path),
        ])
        assert rc == 1
        assert 'must not be empty' in capsys.readouterr().err

    def test_a_corrupt_database_is_a_one_liner_not_a_traceback(
        self, tmp_path, capsys
    ):
        broken = tmp_path / 'broken.db'
        broken.write_bytes(b'this is not a database')
        rc = main(['status', '--db', str(broken)])
        assert rc == 1
        err = capsys.readouterr().err
        assert 'cannot read the database' in err
        assert 'Traceback' not in err

    def test_missing_database_is_reported(self, tmp_path, capsys):
        rc = main(['status', '--db', str(tmp_path / 'nope.db')])
        assert rc == 1
        assert 'No database at' in capsys.readouterr().err


def test_login_users_skips_the_ai_bot(db_path):
    with _connect(db_path) as conn:
        users = login_users(conn)
    assert [u['username'] for u in users] == ['operator']
