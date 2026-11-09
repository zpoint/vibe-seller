"""The communication tools: ``manage_email_account`` and
``manage_chat_bot`` (``manage_ticktick`` lives in tools_ticktick.py).

Thin adapters over ``app/routers/email_accounts.py`` and
``app/routers/chat_bots.py`` — the same code the Settings pages run. Every result passes through
:func:`app.agent_mcp.redact.redact`: email/Ziniao passwords and bot
webhook secrets are write-only, and the chat-bot webhook URL (which
embeds its key) is masked even though the REST single-bot GET
returns it raw for the edit form.

Side effects worth naming in the tool descriptions (and here, so
the drift guard on docs cannot lose them):

* ``manage_email_account(send=…)`` sends a REAL email through the
  account's SMTP — only when the mailbox's ``allow_send`` is on (the
  gate is in ``app/email/sender.py``);
* ``manage_chat_bot(send=…)`` posts a REAL message to the group.
"""

import base64
import binascii
from pathlib import Path
import tempfile

from app.agent_mcp.actions import (
    action_arg,
    action_schema,
    flag,
    opt_int,
    opt_str,
)
from app.agent_mcp.redact import redact, restore_masked
from app.agent_mcp.registry import tool
from app.agent_mcp.support import (
    McpContext,
    ToolError,
    call_route,
    require,
)
from app.models.email_account import EmailAccount
from app.routers import (
    chat_bots as chat_bots_route,
    email_accounts as email_route,
)
from app.routers.email_accounts import (
    _SendEmailRequest,
    _SyncEmailNowRequest,
    _TestConnectionRequest,
)
from app.schemas.chat_bot import (
    ChatBotCreate,
    ChatBotSendFileRequest,
    ChatBotSendRequest,
    ChatBotTestRequest,
    ChatBotUpdate,
)
from app.schemas.email_account import (
    EmailAccountCreate,
    EmailAccountResponse,
    EmailAccountUpdate,
    StoreEmailLinkCreate,
)
from app.utils.crypto import decrypt_password

# Rule 4 upload cap, applied to send_file's content_base64.
_MAX_SEND_FILE_BYTES = 10 * 1024 * 1024

_EMAIL_ACTIONS = (
    'list',
    'get',
    'create',
    'update',
    'delete',
    'test',
    'test_smtp',
    'send',
    'sync_now',
    'links',
    'link',
    'unlink',
    'info_by_store',
)

_EMAIL_EDIT_FIELDS = (
    'email',
    'password',
    'imap_host',
    'imap_port',
    'use_ssl',
    'smtp_host',
    'smtp_port',
    'smtp_use_tls',
)


def _email_row(account) -> dict:
    """One email account as the web UI sees it.

    The routes return the ORM row; the HTTP layer's response model
    (which has no password field at all) is what the UI trusts, so
    the adapter validates through it before returning anything.
    """
    return EmailAccountResponse.model_validate(account).model_dump()


async def _stored_email_password(ctx: McpContext, account_id: str):
    """The stored mailbox password, or None.

    No route returns it (the response model has no such field), so
    the row the update route itself will read is the same-code
    source; ``restore_masked`` needs it to keep a masked echo from
    overwriting the credential.
    """
    account = await ctx.db.get(EmailAccount, account_id)
    if not account or not account.encrypted_password:
        return None
    return decrypt_password(account.encrypted_password)


@tool(
    'manage_email_account',
    'Manage email accounts (Settings → Email) and their store '
    'links. Actions: list, get, create, update, delete, test (IMAP '
    'login with raw credentials, BEFORE saving), test_smtp, send '
    '(SENDS A REAL EMAIL through the account — never use it '
    'without your user asking; refused if the user has turned '
    'sending off for that mailbox in Settings → Email — see each '
    "account's allow_send, which only the user can change), "
    'sync_now (pull new mail now), '
    'links (stores linked to an account), link / unlink (attach an '
    'account to a store), info_by_store (the per-account mail DB '
    'paths + query notes an agent uses to read mail). Passwords '
    'are write-only: accepted on create/update/test, never '
    'returned.',
    action_schema(
        {
            'account_id': {
                'type': ['string', 'null'],
                'description': (
                    'Account id (update/delete/test_smtp/send/links).'
                ),
            },
            'email': {
                'type': ['string', 'null'],
                'description': (
                    'Address; required for create and test. On update '
                    'it changes the address (route allows it).'
                ),
            },
            'password': {
                'type': ['string', 'null'],
                'description': (
                    'Mailbox password. Write-only. Required for '
                    'create and test; on update, omit or empty means '
                    '"leave unchanged".'
                ),
            },
            'imap_host': {
                'type': ['string', 'null'],
                'description': (
                    'IMAP host; omit on create to auto-discover from '
                    'the address (the route 400s when discovery '
                    'fails).'
                ),
            },
            'imap_port': {
                'type': ['integer', 'null'],
                'description': 'IMAP port (default 993).',
            },
            'use_ssl': {
                'type': ['boolean', 'null'],
                'description': 'IMAP over SSL (default true).',
            },
            'smtp_host': {
                'type': ['string', 'null'],
                'description': (
                    'SMTP host; omit on create to auto-discover (an '
                    'account without SMTP cannot send).'
                ),
            },
            'smtp_port': {
                'type': ['integer', 'null'],
                'description': 'SMTP port.',
            },
            'smtp_use_tls': {
                'type': ['boolean', 'null'],
                'description': 'SMTP STARTTLS (default true).',
            },
            'to': {
                'type': ['string', 'array', 'null'],
                'items': {'type': 'string'},
                'description': 'send: recipient address(es).',
            },
            'subject': {
                'type': ['string', 'null'],
                'description': 'send: subject line.',
            },
            'body': {
                'type': ['string', 'null'],
                'description': 'send: plain-text body.',
            },
            'body_html': {
                'type': ['string', 'null'],
                'description': 'send: optional HTML body.',
            },
            'account_email': {
                'type': ['string', 'null'],
                'description': (
                    'sync_now: the account address to pull mail for.'
                ),
            },
            'store_id': {
                'type': ['string', 'null'],
                'description': 'link / unlink / info_by_store: store.',
            },
            'email_account_id': {
                'type': ['string', 'null'],
                'description': 'link: the account to link to the store.',
            },
            'link_id': {
                'type': ['string', 'null'],
                'description': 'unlink: the link id (from links).',
            },
        },
        actions=_EMAIL_ACTIONS,
    ),
)
async def manage_email_account(ctx: McpContext, args: dict) -> dict:
    action = action_arg(args, _EMAIL_ACTIONS)

    if action == 'list':
        accounts = await call_route(
            email_route.list_email_accounts, db=ctx.db, _user=ctx.user
        )
        return redact({'accounts': [_email_row(a) for a in accounts]})

    if action == 'get':
        account_id = require(args, 'account_id')
        accounts = await call_route(
            email_route.list_email_accounts, db=ctx.db, _user=ctx.user
        )
        for account in accounts:
            if account.id == account_id:
                return redact({'account': _email_row(account)})
        raise ToolError(f'Email account not found: {account_id}')

    if action == 'create':
        data = EmailAccountCreate(
            email=require(args, 'email'),
            # A mask can only be an echo of a credential that does
            # not exist yet — restore_masked refuses it rather than
            # storing '****…' as the password.
            password=restore_masked(
                {'password': require(args, 'password')},
                {'password': None},
            )['password'],
            imap_host=opt_str(args, 'imap_host'),
            imap_port=opt_int(args, 'imap_port') or 993,
            use_ssl=flag(args, 'use_ssl', default=True),
            smtp_host=opt_str(args, 'smtp_host'),
            smtp_port=opt_int(args, 'smtp_port'),
            smtp_use_tls=flag(args, 'smtp_use_tls', default=True),
        )
        account = await call_route(
            email_route.create_email_account,
            data,
            db=ctx.db,
            user=ctx.user,
        )
        return redact({'created': _email_row(account)})

    if action == 'update':
        account_id = require(args, 'account_id')
        fields = {
            key: args[key]
            for key in _EMAIL_EDIT_FIELDS
            if args.get(key) is not None
        }
        if not fields:
            raise ToolError(
                'update needs at least one of: ' + ', '.join(_EMAIL_EDIT_FIELDS)
            )
        if 'password' in fields:
            # A masked password is an echo of the read side — keep
            # the stored credential instead of overwriting it with
            # the mask (restore_masked refuses when nothing is
            # stored: the agent believes it sent a secret).
            fields['password'] = restore_masked(
                {'password': fields['password']},
                {'password': await _stored_email_password(ctx, account_id)},
            )['password']
        account = await call_route(
            email_route.update_email_account,
            account_id,
            EmailAccountUpdate(**fields),
            db=ctx.db,
            _user=ctx.user,
        )
        return redact({'updated': _email_row(account)})

    if action == 'delete':
        account_id = require(args, 'account_id')
        result = await call_route(
            email_route.delete_email_account,
            account_id,
            db=ctx.db,
            _user=ctx.user,
        )
        return redact(result or {'ok': True})

    if action == 'test':
        data = _TestConnectionRequest(
            email=require(args, 'email'),
            imap_host=require(args, 'imap_host'),
            imap_port=opt_int(args, 'imap_port') or 993,
            password=restore_masked(
                {'password': require(args, 'password')},
                {'password': None},
            )['password'],
            use_ssl=flag(args, 'use_ssl', default=True),
        )
        result = await call_route(
            email_route.test_connection, data, _user=ctx.user
        )
        return redact(result)

    if action == 'test_smtp':
        account_id = require(args, 'account_id')
        result = await call_route(
            email_route.test_smtp_account,
            account_id,
            db=ctx.db,
            _user=ctx.user,
        )
        return redact(result)

    if action == 'send':
        account_id = require(args, 'account_id')
        to = args.get('to')
        if isinstance(to, str):
            to = [to]
        if not to or not all(isinstance(item, str) for item in to):
            raise ToolError('to is required (address or list of them)')
        data = _SendEmailRequest(
            to=to,
            subject=require(args, 'subject'),
            body=require(args, 'body'),
            body_html=opt_str(args, 'body_html'),
        )
        result = await call_route(
            email_route.send_email_endpoint,
            account_id,
            data,
            db=ctx.db,
            _user=ctx.user,
        )
        return redact(result)

    if action == 'sync_now':
        data = _SyncEmailNowRequest(
            account_email=require(args, 'account_email')
        )
        result = await call_route(
            email_route.sync_email_now, data, db=ctx.db, _user=ctx.user
        )
        return redact(result)

    if action == 'links':
        account_id = require(args, 'account_id')
        result = await call_route(
            email_route.get_email_account_links,
            account_id,
            db=ctx.db,
            _user=ctx.user,
        )
        return redact({'links': result})

    if action == 'link':
        store_id = require(args, 'store_id')
        result = await call_route(
            email_route.link_email_to_store,
            store_id,
            StoreEmailLinkCreate(
                email_account_id=require(args, 'email_account_id')
            ),
            db=ctx.db,
            _user=ctx.user,
        )
        return redact({'linked': result.model_dump()})

    if action == 'unlink':
        result = await call_route(
            email_route.unlink_email_from_store,
            require(args, 'store_id'),
            require(args, 'link_id'),
            db=ctx.db,
            _user=ctx.user,
        )
        return redact(result or {'ok': True})

    # info_by_store
    result = await call_route(
        email_route.email_info_by_store,
        require(args, 'store_id'),
        db=ctx.db,
        _user=ctx.user,
    )
    return redact(result)


_CHAT_ACTIONS = (
    'platforms',
    'list',
    'get',
    'create',
    'update',
    'delete',
    'test',
    'send',
    'send_file',
)

_CHAT_EDIT_FIELDS = ('name', 'webhook_url', 'secret', 'keyword')


def _send_file_staging_dir() -> Path:
    """A directory the send-file route's own guard accepts.

    The route resolves the caller's path and requires it under /tmp,
    /private/tmp or the downloads dir — its private constant IS the
    list, so the staging dir is taken from it rather than
    re-declared here (the two cannot drift apart). Falls back to
    /tmp, which every POSIX install has.
    """
    for root in chat_bots_route._ALLOWED_FILE_ROOTS:  # noqa: SLF001
        if root.name == 'tmp' and root.is_dir():
            return root
    return Path('/tmp')


def _decode_send_file(args: dict) -> tuple[bytes, str]:
    """Decode send_file's content_base64 + filename, with the rule-4
    cap. Returns (payload, safe filename) or raises ToolError."""
    raw = args.get('content_base64')
    if not isinstance(raw, str) or not raw:
        raise ToolError('content_base64 is required for send_file')
    try:
        payload = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ToolError(f'content_base64 is not valid base64: {exc}') from exc
    if not payload:
        raise ToolError('content_base64 decoded to an empty file')
    if len(payload) > _MAX_SEND_FILE_BYTES:
        raise ToolError(
            f'File too large: {len(payload)} bytes (max {_MAX_SEND_FILE_BYTES})'
        )
    filename = Path(require(args, 'filename')).name or 'file.bin'
    return payload, filename


@tool(
    'manage_chat_bot',
    'Manage chat-bot group webhooks — WeCom, DingTalk, Feishu, '
    'Slack (Settings → Integrations → Chat bots). Actions: '
    'platforms (registry: labels, capabilities, limits), list, '
    'get, create, update, delete, test (sends the standard test '
    'message — a REAL post to the group), send (posts a REAL text '
    'or markdown message), send_file (uploads a small file, '
    'base64, WeCom only). The platform is derived from the webhook '
    'URL, never chosen. Webhook URLs and signing secrets are '
    'write-only: list/get return a masked URL and has_secret, '
    'never the raw values.',
    action_schema(
        {
            'bot_id': {
                'type': ['string', 'null'],
                'description': (
                    'Bot id (get/update/delete/test/send/send_file).'
                ),
            },
            'name': {
                'type': ['string', 'null'],
                'description': 'Bot label (required for create).',
            },
            'webhook_url': {
                'type': ['string', 'null'],
                'description': (
                    'The platform webhook URL (required for create; '
                    'write-only — returned masked). The host decides '
                    'the platform.'
                ),
            },
            'secret': {
                'type': ['string', 'null'],
                'description': (
                    'Signing secret (DingTalk/Feishu). Write-only; '
                    'empty string on update clears it.'
                ),
            },
            'keyword': {
                'type': ['string', 'null'],
                'description': (
                    'Security keyword (DingTalk/Feishu) — not a '
                    'secret; shown on the row like in the UI.'
                ),
            },
            'content': {
                'type': ['string', 'null'],
                'description': (
                    'test (optional, defaults to the standard test '
                    'message) and send: the message text.'
                ),
            },
            'msgtype': {
                'type': ['string', 'null'],
                'enum': ['text', 'markdown', None],
                'description': "send: 'text' (default) or 'markdown'.",
            },
            'content_base64': {
                'type': ['string', 'null'],
                'description': (
                    'send_file: the file, base64-encoded (max 10 MB).'
                ),
            },
            'filename': {
                'type': ['string', 'null'],
                'description': 'send_file: the name to give the file.',
            },
        },
        actions=_CHAT_ACTIONS,
    ),
)
async def manage_chat_bot(ctx: McpContext, args: dict) -> dict:
    action = action_arg(args, _CHAT_ACTIONS)

    if action == 'platforms':
        result = await call_route(
            chat_bots_route.list_chat_bot_platforms, _user=ctx.user
        )
        return redact({'platforms': result})

    if action == 'list':
        result = await call_route(
            chat_bots_route.list_chat_bots, db=ctx.db, _user=ctx.user
        )
        return redact({'bots': [bot.model_dump() for bot in result]})

    if action == 'get':
        bot_id = require(args, 'bot_id')
        bot = await call_route(
            chat_bots_route.get_chat_bot, bot_id, db=ctx.db, _user=ctx.user
        )
        # The REST route returns the raw webhook URL and secret for
        # the edit form; over MCP they are write-only (redact).
        return redact({'bot': bot.model_dump()})

    if action == 'create':
        # A masked webhook/secret can only be an echo of a bot that
        # does not exist yet — refused rather than stored as '****'.
        restored = restore_masked(
            {
                'webhook_url': require(args, 'webhook_url'),
                'secret': opt_str(args, 'secret'),
            },
            {},
        )
        data = ChatBotCreate(
            name=require(args, 'name'),
            webhook_url=restored['webhook_url'],
            secret=restored['secret'],
            keyword=opt_str(args, 'keyword'),
        )
        bot = await call_route(
            chat_bots_route.create_chat_bot,
            data,
            db=ctx.db,
            user=ctx.user,
        )
        return redact({'created': bot.model_dump()})

    if action == 'update':
        bot_id = require(args, 'bot_id')
        fields = {}
        for key in _CHAT_EDIT_FIELDS:
            if args.get(key) is not None:
                fields[key] = args[key]
        if not fields:
            raise ToolError(
                'update needs at least one of: ' + ', '.join(_CHAT_EDIT_FIELDS)
            )
        # The get route holds the raw URL/secret (the edit form's
        # source); a masked echo means "keep it", so it is restored
        # from there before the update persists anything.
        stored = (
            await call_route(
                chat_bots_route.get_chat_bot,
                bot_id,
                db=ctx.db,
                _user=ctx.user,
            )
        ).model_dump()
        fields = restore_masked(fields, stored)
        bot = await call_route(
            chat_bots_route.update_chat_bot,
            bot_id,
            ChatBotUpdate(**fields),
            db=ctx.db,
            _user=ctx.user,
        )
        return redact({'updated': bot.model_dump()})

    if action == 'delete':
        bot_id = require(args, 'bot_id')
        result = await call_route(
            chat_bots_route.delete_chat_bot,
            bot_id,
            db=ctx.db,
            _user=ctx.user,
        )
        return redact(result or {'ok': True})

    if action == 'test':
        bot_id = require(args, 'bot_id')
        content = opt_str(args, 'content')
        result = await call_route(
            chat_bots_route.test_chat_bot,
            bot_id,
            ChatBotTestRequest(content=content) if content else None,
            db=ctx.db,
            _user=ctx.user,
        )
        return redact(result)

    if action == 'send':
        bot_id = require(args, 'bot_id')
        data = ChatBotSendRequest(
            content=require(args, 'content'),
            msgtype=opt_str(args, 'msgtype') or 'text',
        )
        result = await call_route(
            chat_bots_route.send_chat_message,
            bot_id,
            data,
            db=ctx.db,
            _user=ctx.user,
        )
        return redact(result)

    # send_file — decode locally, hand the route a server-local
    # path inside its allowed roots, and clean up.
    payload, filename = _decode_send_file(args)
    bot_id = require(args, 'bot_id')
    # The staging dir is the ROUTE'S own allowed tmp root, not
    # tempfile.gettempdir(): on macOS the system temp is
    # /var/folders/…, which the route's _resolve_safe_file rejects
    # (it allows /tmp, /private/tmp and the downloads dir) — the
    # tool would hand the route a path its own guard refuses. The
    # prefix keeps these identifiable if a send crashes.
    file = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=str(_send_file_staging_dir()),
            prefix='mcp-bot-send-',
            suffix=f'-{filename}',
            delete=False,
        ) as file:
            file.write(payload)
            path = file.name
        result = await call_route(
            chat_bots_route.send_chat_file,
            bot_id,
            ChatBotSendFileRequest(path=path),
            db=ctx.db,
            _user=ctx.user,
        )
        return redact(result)
    finally:
        if file is not None:
            Path(file.name).unlink(missing_ok=True)
