"""A stand-in SMTP server — what an e2e mail send lands in.

The email sender (:mod:`app.email.sender`) always upgrades the
connection (STARTTLS) and logs in before it sends, so a stand-in that
only accepts plain SMTP is refused by the client, not by the network.
This server speaks exactly the slice of ESMTP the sender uses:

* ``EHLO`` → advertises ``STARTTLS`` and ``AUTH PLAIN``;
* ``STARTTLS`` → wraps the socket in TLS (a throwaway self-signed
  certificate — stdlib ``smtplib`` does not verify it);
* ``AUTH PLAIN`` → accepts any credentials and records them;
* ``MAIL FROM`` / ``RCPT TO`` / ``DATA`` → records each message.

Every accepted message is appended to the log file (``--log``) as one
JSON line — envelope plus body — so a test asserts "the agent's send
landed" by reading the file, never a real mailbox.

Standalone (the smoke runners and a manual check both use this form):

    python -m tests.e2e.fake_smtp_server --port 17825 \
        --log /tmp/fake-smtp.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging
import ssl
import time

logger = logging.getLogger('fake_smtp')

_GREETING = b'220 fake-smtp e2e stand-in\r\n'


def _make_ssl_context() -> ssl.SSLContext:
    """A throwaway self-signed server context.

    ``cryptography`` is already a project dependency (the same
    generator powers the e2e TLS fixtures elsewhere); generating at
    startup keeps any committed certificate out of the repo.
    """
    from pathlib import Path
    import tempfile

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName('localhost')]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    tmp = Path(tempfile.mkdtemp(prefix='fake-smtp-cert-'))
    cert_path = tmp / 'cert.pem'
    key_path = tmp / 'key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    return ctx


class _Session:
    """One SMTP conversation, exactly the sender's slice of it."""

    def __init__(self, log_path: str, ssl_ctx: ssl.SSLContext):
        self._log_path = log_path
        self._ssl = ssl_ctx
        self._tls = False
        self._mail_from = ''
        self._rcpts: list[str] = []
        self._auth_user = ''

    async def handle(self, reader: asyncio.StreamReader, writer) -> None:
        try:
            writer.write(_GREETING)
            await writer.drain()
            while True:
                line = await reader.readline()
                if not line:
                    return
                verb, arg = _split(line)
                if verb == b'EHLO' or verb == b'HELO':
                    if self._tls:
                        # after STARTTLS the client re-greets; STARTTLS
                        # is not offered a second time
                        writer.write(
                            b'250-fake-smtp\r\n250-AUTH PLAIN\r\n250 OK\r\n'
                        )
                    else:
                        writer.write(
                            b'250-fake-smtp\r\n250-STARTTLS\r\n'
                            b'250-AUTH PLAIN\r\n250 OK\r\n'
                        )
                elif verb == b'STARTTLS':
                    writer.write(b'220 go\r\n')
                    await writer.drain()
                    # upgrades THIS writer's transport in place (the
                    # 3.11 stdlib returns None — do not reassign)
                    await writer.start_tls(self._ssl)
                    self._tls = True
                    continue
                elif verb == b'AUTH':
                    self._auth_user = _auth_user(arg)
                    writer.write(b'235 ok\r\n')
                elif verb == b'MAIL':
                    self._mail_from = _between(arg, b':', b'>')
                    writer.write(b'250 ok\r\n')
                elif verb == b'RCPT':
                    self._rcpts.append(_between(arg, b':', b'>'))
                    writer.write(b'250 ok\r\n')
                elif verb == b'DATA':
                    writer.write(b'354 go\r\n')
                    await writer.drain()
                    body = await reader.readuntil(b'\r\n.\r\n')
                    await asyncio.to_thread(self._record, body[:-5])
                    writer.write(b'250 queued\r\n')
                elif verb == b'QUIT':
                    writer.write(b'221 bye\r\n')
                    await writer.drain()
                    return
                elif verb == b'RSET' or verb == b'NOOP':
                    writer.write(b'250 ok\r\n')
                else:
                    writer.write(b'250 ok\r\n')
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError, ssl.SSLError):
            pass  # the client hung up mid-conversation; keep the log
        finally:
            writer.close()

    def _record(self, body: bytes) -> None:
        entry = {
            'at': datetime.now(UTC).isoformat(),
            'auth_user': self._auth_user,
            'mail_from': self._mail_from,
            'rcpts': self._rcpts,
            'body': body.decode('utf-8', errors='replace'),
        }
        with open(self._log_path, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(entry) + '\n')
        logger.info('recorded message to %s', self._rcpts)


def _split(line: bytes) -> tuple[bytes, bytes]:
    stripped = line.rstrip(b'\r\n')
    if b' ' in stripped:
        verb, _, arg = stripped.partition(b' ')
        return verb.upper(), arg
    return stripped.upper(), b''


def _between(arg: bytes, start: bytes, end: bytes) -> str:
    try:
        i = arg.index(start) + 1
        out = arg[i : arg.index(end, i)]
    except ValueError:
        out = arg
    return out.strip(b'<>').decode('utf-8', 'replace')


def _auth_user(arg: bytes) -> str:
    """The username out of ``AUTH PLAIN <b64>`` (anything goes)."""
    import base64

    payload = arg.strip()
    if payload.upper().startswith(b'PLAIN '):
        payload = payload[6:]
    try:
        # AUTH PLAIN decodes to authzid\0authcid\0passwd
        parts = base64.b64decode(payload).split(b'\0')
        return parts[1].decode('utf-8', 'replace') if len(parts) > 1 else ''
    except Exception:  # noqa: BLE001 — a malformed AUTH is recorded raw
        return payload.decode('utf-8', 'replace')


def read_messages(log_path: str) -> list[dict]:
    """The recorded messages — the assertion helper for tests."""
    from pathlib import Path

    p = Path(log_path)
    if not p.is_file():
        return []
    return [
        json.loads(line)
        for line in p.read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]


async def serve(port: int, log_path: str) -> asyncio.AbstractServer:
    """Start the server on *port*; returns the asyncio server object.

    The log file is TRUNCATED by the sync callers (``serve_sync`` /
    ``main``) before this runs; the one TLS context is built off-loop,
    in a thread, before anything is served.
    """
    ssl_ctx = await asyncio.to_thread(_make_ssl_context)
    return await asyncio.start_server(
        lambda r, w: _Session(log_path, ssl_ctx).handle(r, w),
        '127.0.0.1',
        port,
    )


def serve_sync(port: int, log_path: str) -> int:
    """Start the server on a background loop; returns the bound port.

    The sync entry point for drivers that are not themselves async
    (``integration_smoke``): the loop runs on a daemon thread.
    """
    from pathlib import Path
    import threading

    Path(log_path).write_text('', encoding='utf-8')  # fresh log per run
    box: dict = {}

    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        server = loop.run_until_complete(serve(port, log_path))
        box['port'] = server.sockets[0].getsockname()[1]
        box['ready'] = True
        loop.run_forever()

    threading.Thread(target=_run, daemon=True).start()
    while 'ready' not in box:
        time.sleep(0.05)
    return int(box['port'])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--log', required=True)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    from pathlib import Path

    Path(args.log).write_text('', encoding='utf-8')  # fresh log per run

    async def run() -> None:
        server = await serve(args.port, args.log)
        logger.info(
            'fake smtp on 127.0.0.1:%s logging to %s', args.port, args.log
        )
        async with server:
            await server.serve_forever()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
