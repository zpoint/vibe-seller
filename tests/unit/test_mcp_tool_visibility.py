"""Unit tests for capability-gated MCP tool visibility.

`vibe_seller_generate_image` is hidden from `tools/list` until a vision
API key is configured — industry practice is to conditionally register
rather than advertise-then-error. When hidden, a base-prompt breadcrumb
(covered in test_prompt_assembly.py) guides the user to enable it.
"""

import pytest

from app import mcp_server

pytestmark = pytest.mark.unit

_IMAGE_TOOL = 'vibe_seller_generate_image'


def _names(tools):
    return {t.get('name') for t in tools}


def test_image_tool_hidden_when_no_key(monkeypatch):
    monkeypatch.setattr('app.vision.get_kie_api_key', lambda: None)
    monkeypatch.setattr('app.vision.is_fake', lambda: False)
    assert _IMAGE_TOOL not in _names(mcp_server._visible_tools())


def test_image_tool_shown_when_key_configured(monkeypatch):
    monkeypatch.setattr('app.vision.get_kie_api_key', lambda: 'sk-x')
    monkeypatch.setattr('app.vision.is_fake', lambda: False)
    assert _IMAGE_TOOL in _names(mcp_server._visible_tools())


def test_image_tool_shown_in_fake_mode(monkeypatch):
    # Fake/CI mode skips the network but must not hide the tool, so e2e
    # runs still exercise the image flow.
    monkeypatch.setattr('app.vision.get_kie_api_key', lambda: None)
    monkeypatch.setattr('app.vision.is_fake', lambda: True)
    assert _IMAGE_TOOL in _names(mcp_server._visible_tools())


def test_hiding_only_drops_the_gated_tool(monkeypatch):
    """Hiding the image tool must not disturb any other tool."""
    monkeypatch.setattr('app.vision.get_kie_api_key', lambda: None)
    monkeypatch.setattr('app.vision.is_fake', lambda: False)
    hidden = _names(mcp_server._visible_tools())
    monkeypatch.setattr('app.vision.get_kie_api_key', lambda: 'sk-x')
    full = _names(mcp_server._visible_tools())
    assert full - hidden == {_IMAGE_TOOL}
