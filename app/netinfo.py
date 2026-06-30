"""LAN address helpers.

The server binds ``0.0.0.0``, so other devices on the same network can
reach it. These compute the URLs to share (used by the tray's
"Copy LAN address" item and anywhere else that needs them).
"""

import socket


def lan_ip() -> str:
    """Best-effort primary LAN IPv4 of this host.

    Opens a UDP socket "to" a public address — no packet is actually
    sent; it just makes the OS pick the outbound interface, whose local
    address is this host's LAN IP. Falls back to loopback if offline.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except OSError:
        return '127.0.0.1'
    finally:
        s.close()


def hostname() -> str:
    """Short host name, no domain suffix (e.g. ``mac`` from ``mac.local``)."""
    return socket.gethostname().split('.')[0]


def lan_url(port: int = 7777) -> str:
    """IP-based URL — reachable from any device on the same LAN."""
    return f'http://{lan_ip()}:{port}'


def lan_hostname_url(port: int = 7777) -> str:
    """mDNS ``.local`` URL — friendlier, works where mDNS resolves
    (macOS/Bonjour, Windows 10+/Linux+avahi)."""
    return f'http://{hostname()}.local:{port}'
