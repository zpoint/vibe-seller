---
description: Deployment and data — running on a cloud server, what happens to your store data, where the agent's memory lives, and why some buttons are in English.
---

# Deployment & Data

## Can I run it on a cloud server?

Yes — you just have to solve the browser/GUI part yourself. Vibe Seller runs fine on a headless server; Ziniao (and the Chrome backend, if you want to watch it work) needs somewhere to render into: a virtual display (Xvfb), a cloud desktop VM, or VNC into a real GUI session all work.

The simpler common setup is a small always-on machine in the office — a Mac mini, a mini-PC running WSL, a spare laptop — that the team reaches over the LAN. Auto-start on boot and crash-restart are set up in [Installation](install.md#keep-it-running-around-the-clock).

## What happens to my store data?

The database, screenshots, logs and store memory all stay on your own disk under `~/.vibe-seller/` (on Windows: `C:\Users\<your-name>\.vibe-seller`). There's no cloud storage and no account to register.

Only two things ever leave your machine:

1. **The prompts you send to your LLM provider** — these contain whatever page content the agent read. That's the unavoidable cost of having an AI do the work, and it's governed by that provider's privacy terms;
2. **Anonymous usage telemetry** — install and task counts only, **no store content whatsoever**. It's on by default; turn it off under **Settings → General → "Anonymous usage telemetry"**, or set `VIBE_SELLER_TELEMETRY=0`.

Nothing else goes out. Where Ziniao credentials are stored and who can see them in the UI: [Where the credentials are stored](ziniao.en.md#where-the-credentials-are-stored).

## Where does the agent's memory live?

Per-store knowledge accumulates as plain markdown under `~/.vibe-seller/stores/<store-slug>/` — you can read it, edit it, copy it between machines. Cross-store knowledge lives in `~/.vibe-seller/knowledge/`, and Skills are markdown too, under `~/.vibe-seller/.claude/skills/`. Nothing is locked inside a binary blob.

You don't have to go digging for it: the **Knowledge** page in the web UI browses and edits those files, and its **AI Chat** mode will file what you tell it into the right place. Layout and conventions: [Knowledge & Skills](knowledge.md).

## Why are some buttons in English?

Because those few spots aren't translated yet — the Chinese and English UIs show the same English text: **Create & Run** (the submit button when you create a task), **Chrome (Playwright)** (a browser-backend option), **Add account** (the plus next to Ziniao accounts), **Claude Code CLI** (the backend name in Settings). The docs quote them exactly as the app shows them, so seeing English in the Chinese UI doesn't mean you're in the wrong place.
