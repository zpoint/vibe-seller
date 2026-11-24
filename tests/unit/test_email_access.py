"""What a task may do with a mailbox: one function decides.

``app.email.access`` is the only place that says whether a mailbox can
read and send, and why not. The in-task tools, the send gate and the
prompt all report its answer, so these cases are the spec for each
mailbox state.
"""

from types import SimpleNamespace

import pytest

from app.email.access import (
    NO_STORE_MAILBOX,
    MailboxAccess,
    all_mailboxes_line,
    read_problem,
    send_problem,
    store_prompt_section,
)

pytestmark = pytest.mark.unit


def _account(**over):
    acct = {
        'id': 'a1',
        'email': 'shop@example.com',
        'allow_send': True,
        'smtp_host': 'smtp.example.com',
        'sync_error': None,
        'synced_at': '2026-11-24T08:00:00+00:00',
    }
    acct.update(over)
    return SimpleNamespace(**acct)


def _access(account, stores=('Shop A',)) -> MailboxAccess:
    return MailboxAccess(
        account_id=account.id,
        email=account.email,
        stores=stores,
        read_problem=read_problem(account),
        send_problem=send_problem(account),
    )


class TestEachMailboxState:
    def test_configured_reads_and_sends(self):
        m = _access(_account())
        assert (m.can_read, m.can_send) == (True, True)
        assert m.summary() == 'shop@example.com (read and send)'

    @pytest.mark.parametrize('off', [False, 0])
    def test_sending_turned_off_is_read_only(self, off):
        m = _access(_account(allow_send=off))
        assert (m.can_read, m.can_send) == (True, False)
        assert '"Email sending permission"' in m.send_problem
        assert m.summary().startswith('shop@example.com (read only: ')

    @pytest.mark.parametrize('smtp', [None, ''])
    def test_no_outgoing_server_is_read_only(self, smtp):
        """IMAP-only mailboxes (SMTP discovery failed) used to be shown
        as "Sending is allowed" and then failed at send time."""
        m = _access(_account(smtp_host=smtp))
        assert (m.can_read, m.can_send) == (True, False)
        assert 'no outgoing (SMTP) server' in m.send_problem
        assert 'Settings → Email' in m.send_problem

    def test_sync_failing_is_send_only(self):
        m = _access(_account(sync_error='LOGIN failed'))
        assert (m.can_read, m.can_send) == (False, True)
        assert 'LOGIN failed' in m.read_problem
        assert '2026-11-24T08:00:00+00:00' in m.read_problem
        assert m.summary().startswith('shop@example.com (send only: ')

    def test_never_synced_and_failing_says_so(self):
        m = _access(_account(sync_error='timed out', synced_at=None))
        assert 'it has never synced' in m.read_problem

    def test_broken_both_ways(self):
        m = _access(_account(sync_error='LOGIN failed', smtp_host=None))
        assert (m.can_read, m.can_send) == (False, False)
        assert m.summary().startswith('shop@example.com (cannot read: ')

    def test_unflushed_new_account_may_send(self):
        """``allow_send`` None is a row not flushed yet; its default is on."""
        assert send_problem(_account(allow_send=None)) is None


class TestPromptLine:
    def test_store_without_mailbox_says_it_cannot_read_or_send(self):
        assert NO_STORE_MAILBOX in store_prompt_section([])

    def test_store_lists_every_mailbox_with_what_it_can_do(self):
        section = store_prompt_section([
            _access(_account()),
            _access(_account(id='a2', email='help@example.com', smtp_host='')),
        ])
        # The heading and the addresses stay: noon-shared § 1 compares a
        # login's email with the bound addresses listed here.
        assert section.startswith('\n\n## Email System\n')
        assert 'shop@example.com (read and send)' in section
        assert 'help@example.com (read only: ' in section
        assert section.count('\n') == 3  # heading line + one line

    def test_no_store_task_names_each_mailbox_store(self):
        line = all_mailboxes_line([
            _access(_account()),
            _access(_account(id='a2', email='spare@example.com'), stores=()),
        ])
        assert 'shop@example.com (read and send) → Shop A' in line
        assert 'spare@example.com (read and send) → no store' in line

    def test_no_store_task_without_mailboxes_adds_nothing(self):
        assert all_mailboxes_line([]) is None
