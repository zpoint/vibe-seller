"""Unit tests for the centralized env options module."""

import logging

import pytest

from app.env_options import Options

pytestmark = pytest.mark.unit


class TestOptionsGet:
    def test_get_returns_default(self, monkeypatch):
        monkeypatch.delenv('ADMIN_EMAIL', raising=False)
        assert Options.ADMIN_EMAIL.get() == 'admin@vibe-seller.local'

    def test_get_returns_env_override(self, monkeypatch):
        monkeypatch.setenv('ADMIN_EMAIL', 'custom@example.com')
        assert Options.ADMIN_EMAIL.get() == 'custom@example.com'


class TestOptionsGetBool:
    @pytest.mark.parametrize('val', ['true', 'True', '1'])
    def test_get_bool_true_variants(self, monkeypatch, val):
        monkeypatch.setenv('VIBE_AUTH_REQUIRED', val)
        assert Options.AUTH_REQUIRED.get_bool() is True

    @pytest.mark.parametrize('val', ['false', '0', '', 'no'])
    def test_get_bool_false_variants(self, monkeypatch, val):
        monkeypatch.setenv('VIBE_AUTH_REQUIRED', val)
        assert Options.AUTH_REQUIRED.get_bool() is False

    def test_get_bool_default(self, monkeypatch):
        monkeypatch.delenv('VIBE_AUTH_REQUIRED', raising=False)
        assert Options.AUTH_REQUIRED.get_bool() is False


class TestOptionsGetInt:
    def test_get_int_valid(self, monkeypatch):
        monkeypatch.setenv('MAX_AGENT_CONCURRENCY', '5')
        assert Options.MAX_AGENT_CONCURRENCY.get_int() == 5

    def test_get_int_invalid_uses_default(self, monkeypatch):
        monkeypatch.setenv('MAX_AGENT_CONCURRENCY', 'abc')
        assert Options.MAX_AGENT_CONCURRENCY.get_int() == 2

    def test_get_int_default(self, monkeypatch):
        monkeypatch.delenv('MAX_AGENT_CONCURRENCY', raising=False)
        assert Options.MAX_AGENT_CONCURRENCY.get_int() == 2


class TestLogLevel:
    def test_log_level_default_is_info(self, monkeypatch):
        monkeypatch.delenv('LOG_LEVEL', raising=False)
        assert Options.LOG_LEVEL.get() == 'INFO'

    def test_log_level_env_override(self, monkeypatch):
        monkeypatch.setenv('LOG_LEVEL', 'DEBUG')
        assert Options.LOG_LEVEL.get() == 'DEBUG'

    def test_log_level_resolves_to_logging_constant(self, monkeypatch):
        monkeypatch.setenv('LOG_LEVEL', 'DEBUG')
        level = getattr(logging, Options.LOG_LEVEL.get().upper(), logging.INFO)
        assert level == logging.DEBUG

    def test_log_level_invalid_falls_back_to_info(self, monkeypatch):
        monkeypatch.setenv('LOG_LEVEL', 'BOGUS')
        level = getattr(logging, Options.LOG_LEVEL.get().upper(), logging.INFO)
        assert level == logging.INFO


class TestOptionsRepr:
    def test_repr_returns_env_var_name(self):
        assert repr(Options.ADMIN_EMAIL) == 'ADMIN_EMAIL'
        assert repr(Options.BACKEND_PORT) == 'BACKEND_PORT'
