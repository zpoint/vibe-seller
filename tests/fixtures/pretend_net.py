"""Local stand-ins for the internet a download crosses.

An HTTPS origin (any handler, served over TLS) and a CONNECT proxy in
front of it that counts what it carried — so a test reads which path a
download took off the proxy itself, never off our own env. Plus the
shapes a proxy user meets when it goes wrong: a proxy that refuses the
target, and one that is gone.
"""

from __future__ import annotations

import contextlib
import http.server
import select
import socket
import socketserver
import ssl
import threading


class TLSServer(http.server.ThreadingHTTPServer):
    """``ThreadingHTTPServer`` over TLS; set ``context`` before serving."""

    daemon_threads = True
    context: ssl.SSLContext

    def get_request(self):
        sock, addr = self.socket.accept()
        # Handshake in the handler's thread, not in the accept loop.
        return (
            self.context.wrap_socket(
                sock, server_side=True, do_handshake_on_connect=False
            ),
            addr,
        )

    def handle_error(self, request, client_address):
        pass  # a probe that hangs up mid-handshake is not a test failure


def https_server(handler, cert, key) -> TLSServer:
    """*handler* served over TLS on a free loopback port, running."""
    server = TLSServer(('127.0.0.1', 0), handler)
    server.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.context.load_cert_chain(cert, key)
    serve(server)
    return server


class Proxy(socketserver.ThreadingTCPServer):
    """A CONNECT proxy that tunnels everything to the one local origin."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, origin_port: int):
        super().__init__(('127.0.0.1', 0), _Tunnel)
        self.origin_port = origin_port
        self.refuse = False
        self.asked = 0  # CONNECTs received
        self.tunnels = 0  # CONNECTs that got through
        # Local ports of the connections it opened to the origin: an
        # origin that logs its peers' ports can tell which requests
        # this proxy carried.
        self.upstream_ports: set[int] = set()

    @property
    def url(self) -> str:
        return f'http://127.0.0.1:{self.server_address[1]}'


class _Tunnel(socketserver.StreamRequestHandler):
    def handle(self):
        request = self.rfile.readline().split()
        while self.rfile.readline().strip():
            pass
        if len(request) < 2 or request[0] != b'CONNECT':
            self.wfile.write(b'HTTP/1.1 405 Method Not Allowed\r\n\r\n')
            return
        proxy: Proxy = self.server  # type: ignore[assignment]
        proxy.asked += 1
        if proxy.refuse:
            self.wfile.write(b'HTTP/1.1 502 Bad Gateway\r\n\r\n')
            return
        upstream = socket.create_connection(('127.0.0.1', proxy.origin_port))
        proxy.upstream_ports.add(upstream.getsockname()[1])
        proxy.tunnels += 1
        self.wfile.write(b'HTTP/1.1 200 Connection established\r\n\r\n')
        self.wfile.flush()
        with contextlib.closing(upstream):
            sockets = [self.connection, upstream]
            while True:
                ready, _, _ = select.select(sockets, [], [], 30)
                if not ready:
                    return
                for sock in ready:
                    data = sock.recv(65536)
                    if not data:
                        return
                    (
                        upstream if sock is self.connection else self.connection
                    ).sendall(data)


def serve(server) -> None:
    threading.Thread(target=server.serve_forever, daemon=True).start()


def stop(server) -> None:
    server.shutdown()
    server.server_close()


def dead_port() -> str:
    """A proxy URL nothing listens on (a proxy that stopped)."""
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return f'http://127.0.0.1:{sock.getsockname()[1]}'
