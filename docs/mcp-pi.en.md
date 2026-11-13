---
description: 'Connect Vibe Seller to Pi: run the pi mcp add command from the dialog, check it works, change or remove it.'
---

# Connect Vibe Seller to Pi

Once it's connected, you can ask Pi to create Vibe Seller tasks, follow their
progress and answer their questions.

Two things to get ready first:

1. An API key — see [First: create an API key](mcp.md#first-create-an-api-key).
   Right after you create it, the dialog shows your server address and a
   ready-made Pi command;
2. Pi itself, installed by following the instructions on the
   [Pi website](https://pi.dev).

## Connect

Run the command from the dialog's **Command line / config file** tab in a
terminal — copy it as one line:

```bash
# Connects to this machine; if Vibe Seller runs elsewhere, use its address instead of localhost
pi mcp add vibe-seller --url http://localhost:7777/api/mcp --header "Authorization=Bearer vst_…"
```

It writes `~/.pi/agent/mcp.json`. In a Pi session that is already open, run
`/reload` once.

## Check it works

Start a new chat and ask “how many stores are in vibe-seller right now?”. If it
names your stores, you're connected. From a terminal, `pi mcp list` should show
`vibe-seller: connected, 32 tools`.

## Change or remove

To change the address or the key, edit the `vibe-seller` entry in
`~/.pi/agent/mcp.json`; deleting that entry removes the server. Run `/reload`
once in any session you have open.

## Next

See [What you can ask](mcp.md#what-you-can-ask) for what to say once you're
connected, and [Examples](examples.md) for full walkthroughs.
