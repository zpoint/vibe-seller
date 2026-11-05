---
type: regex
pattern: mcp__plugin_vibe-seller_vibe-seller__
target: mock_calls
match: not_contains
---

The request has nothing to do with a store, so the skill must not fire:
no vibe-seller MCP tool may be called at all.
