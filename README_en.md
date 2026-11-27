<!-- Generated from docs/index.en.md by scripts/gen_readme.py — edit those, not this file. -->

<h1 align="center">Vibe Seller</h1>

<p align="center">
  <b>AI automation framework for cross-border sellers (supports Amazon, Noon, Mercado Libre, Qianniu and more; Ziniao, Yuehulian, Chrome and other browsers). Freely connect any LLM.</b><br/>
  Runs on macOS, Linux, or Windows (native installer). Local-first — code on your machine, data on your disk. Works with Claude, DeepSeek, GLM, Qwen, Kimi, MiniMax — any Anthropic-compatible provider.
</p>

<p align="center">
  <a href="https://docs.vibe-sellers.com/en/">English</a> ·
  <a href="https://docs.vibe-sellers.com/">中文</a>
</p>

<p align="center">
  <a href="https://join.slack.com/t/vibeseller/shared_invite/zt-4fjzkwkqs-L0pM6VS7~_2WsPCxTeLbew"><img alt="Slack" src="https://img.shields.io/badge/Slack-Join-4A154B?logo=slack&logoColor=white" /></a>
  <a href="#community"><img alt="WeChat group" src="https://img.shields.io/badge/WeChat-Scan%20to%20join-07C160?logo=wechat&logoColor=white" /></a>
</p>


<img alt="Vibe Seller demo" src="docs/images/demo_en.gif" width="100%" />

<img width="3200" height="1800" alt="1_combined" src="docs/images/1_combined_en.png" />

<img width="1991" height="1552" alt="2_combined" src="docs/images/2_combined.png" />

🔥 *News* 🔥

- [Nov 2026] **Let the AI use WeCom / Feishu / DingTalk**: click Connect, scan or authorize, and the AI writes docs and sheets, sends messages and schedules events: [**details**](https://docs.vibe-sellers.com/en/workplace-cli/)
- [Nov 2026] **MCP setup**: drive Vibe Seller from the AI tool you already use — Claude, Codex, WorkBuddy, Qoder: [**details**](https://docs.vibe-sellers.com/en/mcp/)
- [Nov 2026] **Amazon ads over the Ads API**: the same ad-optimization task went from 147 minutes to 11, with less need to step in: [**details**](https://docs.vibe-sellers.com/en/ads-api/)

---

## What is Vibe Seller?

A local-first **browser-automation framework**. AI agents drive your
store's real browser — an anti-detect browser such as Ziniao or
Yuehulian, or plain Chrome —
through CDP (Chrome's low-level remote-control protocol), the same
way you'd click through pages yourself. Ad tuning, listing launches, inventory checks, invoicing,
returns, warehouse setup, logistics lookup — whatever you can do in
a browser, the agent can do too.

Each store gets its own agent — **think of it as hiring a different
ops person for each store**. The Store A agent only sees Store A's
browser profile, has its own memory and workspace, can't touch the other
stores' agents.

```mermaid
flowchart LR
    U1[You] -.LAN.-> S
    U2[Teammates] -.LAN.-> S
    subgraph OfficeHost["Office host — Mac or Windows (always on)"]
        S[Vibe Seller server<br/>web UI on :7777]
        S --> A1[Agent for Store A]
        S --> A2[Agent for Store B]
        S --> A3[Agent for Store C]
        A1 -->|CDP| Z1[Ziniao profile A]
        A2 -->|CDP| Z2[BitBrowser store B]
        A3 -->|CDP| Z3[Yuehulian store C]
    end
```

Either deployment works:

- **One always-on machine** (Mac, Linux, or Windows) hosts the
  server
- **Or just use your own computer**

You and your team open the web UI from any device on the same LAN.

## Why Vibe Seller?

Compared to existing automation tools, Vibe Seller is built around
what sellers actually need and what today's AI models
can actually do well: AI-driven by design, with built-in Skills,
per-store isolation, and fully local deployment. Browser control
runs on [browser-use](https://github.com/browser-use/browser-use)
instead of blind screenshot-clicking — faster, and far cheaper in
tokens, than an agent that has to feel out every page from
scratch.

- **Batteries-included, no hand-holding.** Amazon (every marketplace),
  Noon, and more ship with built-in Skills for the jobs sellers
  actually do — report / statement export, listing creation & updates,
  ad tuning. Create a task and the system opens that store's browser
  and the agent operates it directly — usually right on the
  first try, no step-by-step guidance needed.
- **Native anti-detect browser control.** CDP straight into the
  logged-in store profile in Ziniao, Yuehulian and the like — not
  Playwright on vanilla Chrome, not UI-coordinate clicks. Lower risk-control risk, more stable.
- **Multi-browser by design.** Ziniao, Yuehulian and plain Chrome are
  all first-class backends — pick per store. Downloads, setup and how to
  choose: [Browsers](https://docs.vibe-sellers.com/en/browsers/).
- **Multi-LLM by design.** Built on top of the Claude Code CLI, so
  any Anthropic-compatible provider works — Claude, DeepSeek, Kimi,
  MiniMax, GLM, Qwen. Switch in Settings, no code changes. Your key,
  your bill.
- **No SaaS lock-in.** Code on your machine, data on your disk, no
  account required.
- **Auditable.** Every step, every prompt, every screenshot is logged
  and replayable.
- **Multi-store from day one.** Each store keeps its own platform,
  data, SOPs, accumulated knowledge. Store A on Amazon US with its
  own listing strategy, Store B on Noon EG with totally different
  SOPs, Store C on Shopify DTC — they coexist without cross-talk.

## Install

**🪟 Windows**: download [VibeSeller-Setup.exe](https://github.com/zpoint/vibe-seller/releases/latest),
double-click it, and when it's done your default browser opens
<http://localhost:7777>. Later, click Vibe Seller in the Start menu, or go to
that address in your browser yourself.

**🍎 macOS / 🐧 Linux**: open a terminal, run these two lines, then open
<http://localhost:7777> in your browser:

```bash
curl -sSL https://raw.githubusercontent.com/zpoint/vibe-seller/main/install.sh | bash
vibe-seller start
```

Step-by-step pictures, upgrading and uninstalling, WSL2 and installing
from source: [Installation](https://docs.vibe-sellers.com/en/install/).

## Quickstart

Once it's installed, three steps get your first task running; the
illustrated walkthrough is the [Quickstart](https://docs.vibe-sellers.com/en/quickstart/):

1. **Set up AI**: in **Settings → AI**, click **Add Model**, pick a
   provider, paste its API key and click **Create**. No key yet? DeepSeek
   is the easiest first choice; how to get one from each provider:
   [Configure AI Models](https://docs.vibe-sellers.com/en/llm-keys/);
2. **Add a store**: click **+ New Store**. If your store uses Ziniao or
   Yuehulian, pick that browser; otherwise pick Chrome. Using Ziniao? Its
   main account has to grant access first; see
   [Ziniao: Prerequisites](https://docs.vibe-sellers.com/en/ziniao/#prerequisites-developer-mode-and-webdriver-login);
3. **Create a task**: select the store, click **+ New Task**, say what you
   want in one sentence, e.g. “Export the last 7 days' sales report”, and
   click **Create & Run**.

## What you can do with it

Vibe Seller is an **AI agent framework** — the browser is one of
its tools, not the only one. The agent can browse pages, read your
email, send group chat messages (WeCom / DingTalk / Feishu / Slack), write to TickTick / Dida365, update
Google Docs. Anything that fits *observe → decide → act* — whether
a browser is involved or not.

A few things sellers commonly use it for:

| Task | What happens | Example |
|---|---|---|
| **Ad tuning** | Reads Campaign Manager, finds under- and over-spenders, proposes bid / budget changes, applies them with your approval. | [Analyse ad ACOS and apply fixes](https://docs.vibe-sellers.com/en/example-ads-acos/) |
| **Listing audit** | Walks every store's catalog, flags suppressed / out-of-stock / inactive items. | [Weekly listing check](https://docs.vibe-sellers.com/en/example-listing-check/) |
| **Invoices & reports** | Crawls tax docs, business reports, advertising reports; downloads are archived to Google Drive. | [Monthly sales report](https://docs.vibe-sellers.com/en/example-daily-report/) |
| **Email handling** | Sorts buyer messages, forwards important ones to the group chat, drafts replies. | [Daily email digest to WeCom](https://docs.vibe-sellers.com/en/example-email-wecom/) |
| **From other AI tools** | Say it in Codex, Claude and the like — Vibe Seller fetches the data from Seller Central, that tool's AI analyses it. | [Export a Ziniao store's finances with Codex](https://docs.vibe-sellers.com/en/example-mcp/) |
| **Listing publication** | Creates new listings from a template — title, bullets, photos, A+ content, variations. | |
| **Inventory check** | Pulls stock levels; low-stock SKUs get pushed to the group chat automatically. | |
| **Warehouse / inbound setup** | Creates inbound shipments, allocates SKUs to FBA / FBN, prints box labels. | |
| **Logistics lookup** | Tracks in-transit shipments, freight delivery, customs clearance; flagged shipments get filed as TickTick todos. | |
| **Anything you add** | Write a Skill (a markdown file with steps) or teach the agent once — it'll remember next time. | |

A single ad-tuning run is ≈ 3–5 minutes and costs about 0.15 USD in LLM tokens
(DeepSeek, 10 or fewer campaigns; more campaigns cost more). Every example has a
description to copy, the settings screenshots and the finished result — see
[All Examples](https://docs.vibe-sellers.com/en/examples/).

## Docs

| | |
|---|---|
| **Getting started** | [Installation](https://docs.vibe-sellers.com/en/install/) · [Quickstart](https://docs.vibe-sellers.com/en/quickstart/) · [Configure AI Models](https://docs.vibe-sellers.com/en/llm-keys/) · [Core Concepts](https://docs.vibe-sellers.com/en/concepts/) |
| **Browsers** | [Browsers Overview](https://docs.vibe-sellers.com/en/browsers/) · [Store Browser & Auxiliary Browser](https://docs.vibe-sellers.com/en/store-and-aux-browser/) · [Ziniao: Setup & Common Errors](https://docs.vibe-sellers.com/en/ziniao/) · [Yuehulian: Setup Guide](https://docs.vibe-sellers.com/en/yuehulian/) |
| **Integrations** | [Overview](https://docs.vibe-sellers.com/en/integrations/) · [MCP setup](https://docs.vibe-sellers.com/en/mcp/) · [Amazon Ads API](https://docs.vibe-sellers.com/en/ads-api/) · [Email](https://docs.vibe-sellers.com/en/email/) · [Chat bots](https://docs.vibe-sellers.com/en/chat-bots/) · [WeCom / Feishu / DingTalk](https://docs.vibe-sellers.com/en/workplace-cli/) · [TickTick / Dida365](https://docs.vibe-sellers.com/en/ticktick/) · [Google Workspace](https://docs.vibe-sellers.com/en/google-workspace/) |
| **Examples** | [All Examples](https://docs.vibe-sellers.com/en/examples/) · [Daily email digest to WeCom](https://docs.vibe-sellers.com/en/example-email-wecom/) · [Analyse ad ACOS and apply fixes](https://docs.vibe-sellers.com/en/example-ads-acos/) · [Weekly listing check](https://docs.vibe-sellers.com/en/example-listing-check/) · [Monthly sales report](https://docs.vibe-sellers.com/en/example-daily-report/) · [Export a Ziniao store's finances with Codex](https://docs.vibe-sellers.com/en/example-mcp/) |
| **User guide** | [Stores & Browsers](https://docs.vibe-sellers.com/en/stores/) · [Tasks](https://docs.vibe-sellers.com/en/using-tasks/) · [Schedules](https://docs.vibe-sellers.com/en/schedules/) · [Knowledge & Skills](https://docs.vibe-sellers.com/en/knowledge/) |
| **FAQ** | [Cost & Model Choice](https://docs.vibe-sellers.com/en/faq-cost/) · [Login & Accounts](https://docs.vibe-sellers.com/en/faq-login/) · [Deployment & Data](https://docs.vibe-sellers.com/en/faq-data/) · [All Questions](https://docs.vibe-sellers.com/en/faq/) |
| **Community** | [Community & Feedback](https://docs.vibe-sellers.com/en/community/): the WeChat group, Slack, and how to report a problem or suggest a feature |
| **Developer (English)** | [Developer Guide](https://docs.vibe-sellers.com/en/dev-guide/) · [Task Internals](https://docs.vibe-sellers.com/en/tasks/) · [Browser Internals](https://docs.vibe-sellers.com/en/browser/) · [Subsystems](https://docs.vibe-sellers.com/en/subsystems/) · [External Agents (MCP API)](https://docs.vibe-sellers.com/en/agents/) · [Windows + WSL2 service](https://docs.vibe-sellers.com/en/windows-setup/) · [macOS service](https://docs.vibe-sellers.com/en/macos-setup/) |

Architecture lives in [`DESIGN.md`](https://github.com/zpoint/vibe-seller/blob/main/DESIGN.md);
deeper subsystem notes are in the [`docs/`
tree](https://github.com/zpoint/vibe-seller/tree/main/docs).

## Integrations

Beyond the browser, the agent can call these external services
directly — everything is hooked up under **Settings → Integrations**,
no code:

- **MCP** — drive Vibe Seller from the AI tool you already use — Claude, Codex, WorkBuddy, Qoder, Hermes Agent, Pi: create tasks, follow them, answer questions, review plans. [Setup guide](https://docs.vibe-sellers.com/en/mcp/)
    - The other way round: connect an outside MCP service such as Xiyou Insight to Vibe Seller, and the AI in your tasks uses its tools directly. [Add a custom MCP server](https://docs.vibe-sellers.com/en/mcp-custom/)
- **Amazon Ads API** — Amazon ad tasks call the API instead of clicking through the seller console; in our test the same ad task went from 147 minutes to 11. [Setup and measured comparison](https://docs.vibe-sellers.com/en/ads-api/)
- **Email** (IMAP/SMTP) — read your linked store mailboxes, sort and summarize, draft and send replies (sending can be turned off per mailbox). Works with 163 / QQ / Gmail and more. [Setup guide](https://docs.vibe-sellers.com/en/email/)
- **Group chat bots** (WeCom / DingTalk / Feishu / Slack) — have the AI post results, reports and reminders straight into a group; WeCom bots can send files too. [Setup guide](https://docs.vibe-sellers.com/en/chat-bots/)
- **WeCom / Feishu / DingTalk** — with each vendor's official program, the AI writes docs and sheets, sends messages, schedules events and creates to-dos; connect by scanning or authorizing. [Setup guide](https://docs.vibe-sellers.com/en/workplace-cli/)
- **TickTick / Dida365** — auto-create todos (follow-ups, order tracking, periodic checks). [Setup guide](https://docs.vibe-sellers.com/en/ticktick/)
- **Google Workspace** — with Google's official program, the AI reads and sends Gmail, edits Sheets and Docs, uploads to Drive and schedules Calendar events; click Connect and sign in with Google. [Setup guide](https://docs.vibe-sellers.com/en/google-workspace/)

A single task can mix browser + service calls. e.g. *“Find all
slow-moving SKUs across stores”* → browser pulls inventory →
generates a Google Sheet → posts a chat summary → drops a "price
review next week" todo into TickTick. Start at the
[Integrations overview](https://docs.vibe-sellers.com/en/integrations/).

## Platforms

Any marketplace you can work in a browser — Amazon, Noon, Mercado Libre, Qianniu (Taobao /
Tmall), AliExpress, Shopee, TikTok Shop… **If it runs in a browser, Vibe Seller can work it.**
At its core it is **an AI agent + the store's browser (an anti-detect browser such as Ziniao or Yuehulian, or plain Chrome) + built-in tools**: the
agent opens the store's back office and storefront the way you would — clicking, reading,
filling in forms, downloading.

Amazon and Noon get the most built-in tools:

- **Amazon** (every marketplace): built-in Skills for ads, reports, listings, FBA inbound,
  invoices and product images, plus the [Amazon Ads API](https://docs.vibe-sellers.com/en/ads-api/) connection so ad work
  goes through the API — faster and steadier;
- **Noon**: built-in Skills for ads, report exports, listings and FBN;
- **Qianniu**: built-in Skills for ads and listings.

Other platforms have no dedicated Skill, and the agent still does back-office and storefront
work through the store's browser. The first runs may take a few extra tries; the paths
that work go into that store's knowledge, so the next run follows them and it settles down
after a few rounds. You can also write your own steps as a Skill and import it (see
[Import an existing Skill](https://docs.vibe-sellers.com/en/knowledge/#import-an-existing-skill)), or polish it and
contribute it to the project (see *Contributing* below).

## Contributing

PRs welcome — whether you write the code yourself or let Claude
Code / Cursor / your coding agent do it. Python side is Google
style (ruff-enforced); frontend is TypeScript strict + ESLint.
[`CLAUDE.md`](https://github.com/zpoint/vibe-seller/blob/main/CLAUDE.md) is the project conventions doc — it's
written for AI agents and reads cleanly as a contributor guide.

## License

[Apache License 2.0](https://github.com/zpoint/vibe-seller/blob/main/LICENSE).

## Community

- **Slack** — [join the VibeSeller workspace](https://join.slack.com/t/vibeseller/shared_invite/zt-4fjzkwkqs-L0pM6VS7~_2WsPCxTeLbew)
- **WeChat** — scan to join the user group:

<img alt="Vibe Seller WeChat user group" src="docs/images/wecom_group_qr.png" width="200" />

Found a problem or have an idea? See [Community & Feedback](https://docs.vibe-sellers.com/en/community/).
