# Documentation Index

This directory is the source of the docs site
(<https://docs.vibe-sellers.com/>, built with MkDocs Material —
config: [`mkdocs.yml`](../mkdocs.yml)). The GitHub READMEs are generated
from the two homepage files by [`scripts/gen_readme.py`](../scripts/gen_readme.py);
never edit `README.md` / `README_en.md` by hand.

**User-facing pages are bilingual**: `foo.md` is the Chinese version (the
site default) and `foo.en.md` its English sibling — same structure, same
anchors. Developer and operations pages are English-only; on the Chinese
site they are marked as such.

## Getting started

| File | Topics |
|------|--------|
| [install.md](install.md) / [install.en.md](install.en.md) | Requirements, Windows installer, macOS / Linux one-liner, upgrade, uninstall, WSL2, source install, service management, LAN access |
| [quickstart.md](quickstart.md) / [quickstart.en.md](quickstart.en.md) | First run: open the web UI, add an LLM key, bind a store, run your first task |
| [llm-keys.md](llm-keys.md) / [llm-keys.en.md](llm-keys.en.md) | Getting an API key from each provider, pay-as-you-go vs plans |
| [concepts.md](concepts.md) / [concepts.en.md](concepts.en.md) | Store, task (two scopes), schedule, knowledge & skills, integrations, AI profiles |

## User guide

| File | Topics |
|------|--------|
| [stores.md](stores.md) / [stores.en.md](stores.en.md) | Stores and browsers: Ziniao, Chrome, per-store isolation |
| [using-tasks.md](using-tasks.md) / [using-tasks.en.md](using-tasks.en.md) | Creating and running tasks, Auto vs Plan mode, results and logs |
| [schedules.md](schedules.md) / [schedules.en.md](schedules.en.md) | Scheduled runs, plan-first schedules, cross-run state |
| [knowledge.md](knowledge.md) / [knowledge.en.md](knowledge.md) | Per-store memory, the knowledge base, skills |

## FAQ section

| File | Topics |
|------|--------|
| [ziniao.md](ziniao.md) / [ziniao.en.md](ziniao.en.md) | Ziniao: developer-mode prerequisites, binding fields, common errors, WSL launcher, credential storage |
| [faq-cost.md](faq-cost.md) / [faq-cost.en.md](faq-cost.en.md) | Cost, which LLM to pick |
| [faq-login.md](faq-login.md) / [faq-login.en.md](faq-login.en.md) | Login password setup / recovery, no `vibe-seller` command on Windows |
| [faq-data.md](faq-data.md) / [faq-data.en.md](faq-data.md) | Cloud servers, data privacy, where the agent's memory lives, English-only buttons |
| [faq.md](faq.md) / [faq.en.md](faq.en.md) | Section overview: index of every question |

## Integrations

| File | Topics |
|------|--------|
| [integrations.md](integrations.md) / [integrations.en.md](integrations.md) | Overview: what can be connected and from where |
| [mcp.md](mcp.md) / [mcp.en.md](mcp.en.md) | Driving Vibe Seller from an MCP-capable AI tool |
| [ads-api.md](ads-api.md) / [ads-api.en.md](ads-api.en.md) | Amazon Ads API binding (and the measured speed-up) |
| [email.md](email.md) / [email.en.md](email.en.md) | Store mailboxes over IMAP / SMTP |
| [chat-bots.md](chat-bots.md) / [chat-bots.en.md](chat-bots.en.md) | WeCom / DingTalk / Feishu / Slack group bots |
| [ticktick.md](ticktick.md) / [ticktick.en.md](ticktick.en.md) | TickTick / Dida365 todos |
| [google-workspace.md](google-workspace.md) / [google-workspace.en.md](google-workspace.md) | Gmail, Drive, Docs, Sheets, Slides, Calendar |

## Developer and operations (English)

| File | Topics |
|------|--------|
| [dev-guide.md](dev-guide.md) | Feature surface, project structure, API endpoints, testing flows |
| [tasks.md](tasks.md) | Task lifecycle internals, data persistence, agent context |
| [workspace.md](workspace.md) | Workspace assistant, knowledge system, skills |
| [subsystems.md](subsystems.md) | Plan-state machine, email sync, browser profiles, concurrency, report gate |
| [events.md](events.md) | SSE event bus, event types, business events |
| [browser.md](browser.md) | Browser backends, wrapper scripts, CDP proxy, store-less web browser |
| [ziniao-concurrency.md](ziniao-concurrency.md) | Ziniao WebDriver mechanism and multi-store concurrency |
| [browser-use-0.13-migration.md](browser-use-0.13-migration.md) | The browser-use 0.13 interface change |
| [backend.md](backend.md) | FastAPI modules, models, schemas, config |
| [frontend.md](frontend.md) | React components, views, i18n, SSE |
| [api.md](api.md) | All API routes |
| [agents.md](agents.md) | External-agent MCP API: tokens, tool reference |
| [testing.md](testing.md) | Test tiers, fixtures, running tests |
| [telemetry.md](telemetry.md) | What is reported, how to opt out |
| [vision.md](vision.md) | AI product-image generation internals |
| [windows-setup.md](windows-setup.md) | Windows + WSL2 service deployment runbook |
| [macos-setup.md](macos-setup.md) | macOS auto-start (`launchd`) runbook |
