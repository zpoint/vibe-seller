"""Tests for app.netinfo LAN address helpers."""

from unittest.mock import patch

import pytest

import app.netinfo as net

pytestmark = pytest.mark.unit


def test_lan_url_format():
    with patch.object(net, 'lan_ip', return_value='192.168.1.42'):
        assert net.lan_url(7777) == 'http://192.168.1.42:7777'
        assert net.lan_url(8080) == 'http://192.168.1.42:8080'


def test_lan_hostname_url_strips_domain():
    with patch('socket.gethostname', return_value='mac.local'):
        assert net.lan_hostname_url(7777) == 'http://mac.local:7777'
    with patch('socket.gethostname', return_value='desktop'):
        assert net.lan_hostname_url() == 'http://desktop.local:7777'


def test_lan_ip_returns_ipv4_string():
    ip = net.lan_ip()
    assert isinstance(ip, str)
    parts = ip.split('.')
    assert len(parts) == 4 and all(p.isdigit() for p in parts)
