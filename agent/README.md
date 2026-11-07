# vibe-seller (Claude Code plugin)

The Claude Code plugin for [Vibe Seller](https://github.com/zpoint/vibe-seller):
it connects this agent to a Vibe Seller server over the hosted MCP endpoint
(`/api/mcp`) and ships a skill that explains how to drive tasks with it.

## Install

```bash
# add the marketplace (from a clone of the repo)
claude plugin marketplace add /path/to/vibe-seller

# install the plugin
claude plugin install vibe-seller@vibe-seller
```

Claude Code then asks for two values:

| Option | What to enter |
| --- | --- |
| **Vibe Seller server URL** | The server's base URL, e.g. `http://127.0.0.1:7777` or `https://vibe.example.com`. No trailing slash, no `/api/mcp` suffix. |
| **API key** | A personal access token minted in the server's **Settings → Integrations → API keys**. |

The token is stored in the platform's credential store (it is marked
`sensitive`), not in `settings.json`.

## What the plugin contains

- `.mcp.json` — the MCP server: `http` transport, `${user_config.url}/api/mcp`,
  `Authorization: Bearer ${user_config.token}`.
- `skills/vibe-seller/SKILL.md` — how to use the tools: when to use plan
  mode, how to follow a task, how to answer questions, how to review plans,
  and why you must never fabricate a result. Details live in
  `skills/vibe-seller/references/`.
- `evals/` — a Claude Code eval suite for the skill, with the MCP server
  mocked so it runs without a server.
- `test/eval_set.json` — a plain trigger-eval list, for review by hand.

## Run the evals

```bash
claude plugin eval ./agent
```

The MCP tools are mocked under `agent/evals/mocks/vibe-seller/`, so no
Vibe Seller server and no real credentials are needed. Eval runs call the
model, so they cost tokens.

## Not in this plugin

The plugin does **not** give the agent a browser. Store work goes through
tasks, so the server's own gates (plan approval, ad-scope declarations,
completeness review) still apply. See
[docs/agents.md](../../docs/agents.md) for the full picture, including the
Codex and WorkBuddy configurations.
