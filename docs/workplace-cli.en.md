---
description: WeCom / Feishu / DingTalk official CLIs — click Connect in Vibe Seller to download the official CLI, sign in by QR code or link, and your agents can use the official skills for messages, docs, sheets, calendars, to-dos and more.
---

# WeCom / Feishu / DingTalk (official CLIs)

All three platforms publish an official command-line tool with matching
AI skills:

| Platform | Official CLI | What agents can do (excerpt) |
|---|---|---|
| WeCom (企业微信) | `wecom-cli` ([WecomTeam/wecom-cli](https://github.com/WecomTeam/wecom-cli)) | Messages to chats the bot is in, mail, docs, sheets, smart sheets, calendar, meetings, to-dos, drive, contacts |
| Feishu / Lark (飞书) | `lark-cli` ([larksuite/cli](https://github.com/larksuite/cli)) | Messages and chats, docs, sheets, Base, drive, wiki, calendar, meetings and minutes, tasks, mail, approvals, contacts |
| DingTalk (钉钉) | `dws` ([DingTalk-Real-AI/dingtalk-workspace-cli](https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli)) | Chats and robot messages, AI tables, docs, drive, wiki, calendar, to-dos, mail, minutes, contacts, DING |

Once a platform is connected, agents call its official CLI and follow
its official skills. **You do not install Node.js or npm, or open a
terminal**: Vibe Seller downloads the official build for your OS,
verifies the checksum the vendor publishes, and runs the sign-in from
the Settings page. Windows, macOS and Linux are supported.

Compared with [chat bots](chat-bots.md): a chat bot only pushes
messages into a group; the official CLI reads and writes across the
whole suite — docs, sheets, calendars, to-dos. You can use both.

## Identity: one account per machine, shared by the team

- Each platform is signed in with **one** account on this Vibe Seller
  machine. Every teammate's tasks act as that account.
- The sign-in belongs to Vibe Seller: it is stored in Vibe Seller's own
  data directory, and never reads or changes a CLI you installed for
  yourself on the same computer.
- Only admins can connect, sign in, update or disable, and only admins
  see the QR code and sign-in links — whoever scans signs this Vibe
  Seller in as themselves.
- Prefer an account or bot dedicated to automation over a personal
  account.

## Connect

Open **Settings → Integrations → WeCom / Feishu / DingTalk (official
CLIs)** and click **Connect** on the platform's row:

1. Vibe Seller downloads the platform's latest official CLI (about
   5–25 MB) and installs it only after the checksum matches and it runs
   on the computer running Vibe Seller. Downloads use whichever path works — your proxy or
   a direct connection — and fall back to mirrors in China (npmmirror,
   Gitee) when the official host is unreachable;
2. The sign-in steps appear on the page (see below);
3. Once signed in, the platform is enabled automatically: the row shows
   "Signed in as …" and "Enabled for agents".

You can close the page during sign-in — Vibe Seller enables the
platform when the sign-in completes.

### How each platform signs in

**WeCom** — the page shows a QR code and a link to the scan page. Scan
with the WeCom app and follow the prompts to create and bind a **smart
bot**. The code is valid for 5 minutes; click **Sign in again** if it
expires. Agents then act as that bot; messages can only go to chats the
bot has talked in recently.

**Feishu** — two steps:

1. Step 1/2: open the link to create the Feishu app the CLI uses (one
   time);
2. Step 2/2: open the link and authorize with your Feishu account (a
   code is shown).

Agents can then act as the app (bot) or as you; your own calendar, mail
and documents need the user identity.

**DingTalk** — the page shows a link and a code. Open the link, pick the
organization and authorize.

!!! warning "DingTalk needs your organization's primary admin to enable CLI access"
    The DingTalk CLI is in its co-creation phase. Until the organization
    enables it, authorizing fails with "no CLI data access", and the
    Settings page lists the organization's primary admins and where to
    act. To enable it
    ([official guide](https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli#getting-started)):

    1. The organization's **primary admin** signs in to the
       [DingTalk developer console](https://open-dev.dingtalk.com);
    2. Opens "CLI Access Management" and turns it on;
    3. Back in Vibe Seller Settings, click **Sign in again** on the
       DingTalk row and authorize once more.

## Enable only what you need

The three platforms are independent — connect one, two or all three:

- Agents see **only enabled** platforms: each adds one entry to the
  agent's skill list (the official skills sit under it and are opened
  as needed) and puts that platform's CLI on the agent's command path;
- A platform that is not connected, or disabled, has neither its skills
  nor its command available to agents.

## Update, sign in again, disable

- **Update** downloads the platform's latest version and refreshes the
  skills agents read, so the docs always match the program. Tasks
  already running keep the previous version; new tasks use the new one.
- **Sign in again** switches accounts, or re-authorizes after the login
  expired.
- **Disable** takes the platform away from agents immediately. The
  program and the login are kept, so **Connect** enables it again
  straight away.

## Rules agents follow

Vibe Seller puts a short set of rules in front of each platform's
skills, and they win over the official text:

- Agents never sign in, sign out or update the CLI, nor install other
  skills; when the login is missing, expired or lacks a permission, they
  stop and ask you to sign in again in Settings;
- Agents send messages or mail, invite people, or change or delete
  documents only when the task asks for it.

## FAQ

<details markdown="1"><summary><b>The download fails?</b></summary>

The error names why each address failed. Vibe Seller tries the official
host and the China mirrors in turn, through whichever proxy or direct
path works. If all fail, check that the computer running Vibe Seller can reach at least one
of `registry.npmjs.org`, `registry.npmmirror.com`, `github.com` or
`gitee.com`, then retry.
</details>

<details markdown="1"><summary><b>The WeCom CLI does not run on Windows?</b></summary>

The WeCom CLI needs the Microsoft VC++ runtime. If connecting says
`wecom-cli does not run here`, install the
[Microsoft Visual C++ Redistributable (x64)](https://aka.ms/vs/17/release/vc_redist.x64.exe)
and retry.
</details>

<details markdown="1"><summary><b>I already installed and signed in to the official CLI myself — sign in again?</b></summary>

Yes. Vibe Seller uses its own copy and keeps its own sign-in, separate
from your personal CLI, so switching your personal account to another
organization never changes what the team's tasks act as.
</details>

<details markdown="1"><summary><b>Do these CLIs send usage data?</b></summary>

- The DingTalk CLI sends usage statistics by default; Vibe Seller turns
  that off with the switch the vendor provides;
- Every WeCom CLI request carries caller information (such as the
  calling process chain; see its
  [data collection notes](https://github.com/WecomTeam/wecom-cli/blob/main/docs/data-collection.md)),
  and the vendor offers no switch to turn it off;
- Feishu CLI requests carry the CLI version and similar details.
</details>

<details markdown="1"><summary><b>Feishu says some permissions were withheld by the organization admin?</b></summary>

The sign-in worked and the platform is enabled; only the permissions
listed were restricted by your Feishu organization admin, so the features
that need them won't work yet — everything else does. To use them, ask
the admin to grant them, then click **Sign in again**.
</details>

<details markdown="1"><summary><b>Does disabling delete data on the platform?</b></summary>

No. Disabling only takes the skills and the command away from agents;
your data on the platform, the CLI program and its login are untouched.
</details>
