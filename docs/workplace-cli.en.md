---
description: WeCom / Feishu / DingTalk official CLIs — click Connect in Vibe Seller to download the official CLI, sign in by QR code or link, and your agents can use the official skills for messages, docs, sheets, calendars, to-dos and more.
---

# WeCom / Feishu / DingTalk

All three platforms publish an official command-line tool with matching
AI skills:

| Platform | Official CLI | What agents can do (excerpt) |
|---|---|---|
| WeCom (企业微信) | `wecom-cli` ([WecomTeam/wecom-cli](https://github.com/WecomTeam/wecom-cli)) | Messages to the person who scanned, or to chats the bot has talked in recently; mail, docs, sheets, smart sheets, calendar, meetings, to-dos, drive, contacts |
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

- Each platform is connected **once** on this Vibe Seller. Every
  teammate's tasks use that connection: for Feishu and DingTalk, the
  account that authorized; for WeCom, the bot created when scanning,
  acting on the scanner's behalf (see "WeCom" below).
- The sign-in belongs to Vibe Seller: it is stored in Vibe Seller's own
  data directory, and never reads or changes a CLI you installed for
  yourself on the same computer.
- Only admins can connect, sign in or disable, and only admins
  see the QR code and sign-in links — whoever scans or authorizes is the
  account this Vibe Seller connects with.
- Prefer an account or bot dedicated to automation over a personal
  account.

## Connect

Open **Settings → Integrations → WeCom / Feishu / DingTalk (official
CLIs)** and click **Connect** on the platform's row:

1. Vibe Seller downloads the platform's latest official CLI (about
   4–25 MB) and installs it only after the checksum matches and it runs
   on the computer running Vibe Seller. Downloads use whichever path works — your proxy or
   a direct connection — and fall back to mirrors in China (npmmirror,
   Gitee) when the official host is unreachable;
2. The sign-in steps appear on the page (see below);
3. Once signed in, the platform is enabled automatically: the row shows
   "Signed in as …" and "The AI can use it".

You can close the page during sign-in — Vibe Seller enables the
platform when the sign-in completes.

### How each platform signs in

**WeCom** — the page shows a QR code and a link to the scan page. Scan
with the WeCom app and follow the prompts to create and bind a **smart
bot**. The code is valid for 5 minutes; if it expires, click **Connect**
again (**Sign in again** if the platform was signed in before).

From then on every action is carried out by that bot, on behalf of the
person who scanned — not as that person. Messages arrive as sent by the
bot. As the WeCom CLI itself states:

- **Messages** can go to two kinds of recipient only: the person who
  scanned, and single or group chats the bot has talked in recently. For
  the AI to post in a group, the bot first needs a conversation there —
  for example, add the bot to the group and @-mention it once.
- **Reading**: data the scanner created or owns can be read, searched
  and downloaded by the bot. Every teammate's tasks can use that access,
  so scan with a dedicated work account.
- **Changing**: the scanner's online docs, sheets, smart sheets and smart
  pages can be edited; calendars, meetings, to-dos, drive files, mail and
  everything else can only be changed where the bot created them.

**Feishu** — two steps:

1. Step 1/2: open the link to create the Feishu app the CLI uses (one
   time);
2. Step 2/2: open the link and authorize with your Feishu account (a
   code is shown).

Agents can then act as the app (bot) or as you; your own calendar, mail
and documents need the user identity.

**DingTalk** — the page shows a link and a code. Open the link, pick the
organization and authorize.

!!! warning "DingTalk needs an admin to turn on CLI access first"
    The DingTalk CLI works only after an enterprise admin authorizes it.
    Until then, authorizing fails with "no CLI data access" (「您暂无 CLI
    数据访问权限」), and the Settings page lists the organization's
    primary admins and where to act. Either way works
    ([official guide](https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli#getting-started)):

    - The organization's **primary admin** signs in to the
      [DingTalk developer platform](https://open-dev.dingtalk.com), opens
      「CLI 访问管理」 (CLI access management) and turns it on;
    - Or, while authorizing, choose the organization on the DingTalk page
      and click 「立即申请」 (Apply now); an admin gets a request card and
      approves it.

    Once it is on or approved, click **Connect** on the DingTalk row in
    Vibe Seller Settings again and authorize once more.

## Enable only what you need

The three platforms are independent — connect one, two or all three:

- Agents see **only enabled** platforms: each adds one entry to the
  agent's skill list (the official skills sit under it and are opened
  as needed) and puts that platform's CLI on the agent's command path;
- A platform that is not connected, or disabled, has neither its skills
  nor its command available to agents.

## Automatic updates, sign in again, disable

- **Automatic updates**: a connected platform needs no manual update.
  Vibe Seller checks for the official latest release once a day; a new
  one is downloaded, verified, and the skills agents read are refreshed
  with it, so the docs always match the program. Tasks already running
  keep the previous version; new tasks use the new one. If a new
  release fails to download or verify, the current version keeps
  running and the next day's check tries again. A platform nobody
  connected is never downloaded.
- **Sign in again** switches accounts, or re-authorizes after the login
  expired.
- **Disable** takes the platform away from agents immediately. The
  program and the login are kept, so **Connect** enables it again
  straight away.

## Rules agents follow

Vibe Seller puts a short set of rules in front of each platform's
skills, and they win over the official text:

- The AI never signs in, signs out or updates the CLI, nor installs
  other skills;
- When the sign-in is missing or expired, the AI stops and asks for an
  admin to click **Sign in again** in Settings;
- When a permission is missing or restricted (signing in again does not
  fix that), the AI stops and tells you which permission it is, and that
  the platform's admin has to grant it;
- Agents send messages or mail, invite people, or change or delete
  documents only when the task asks for it.

## FAQ

<details markdown="1"><summary><b>The download fails?</b></summary>

The Settings page says what to do next; open **Details** to see why each
address failed. Vibe Seller tries the official host first, then the China
mirror, through whichever proxy or direct path works. The addresses each
platform uses:

| Platform | Version and program | Official skills |
|---|---|---|
| WeCom | `registry.npmjs.org`, mirror `registry.npmmirror.com` | GitHub only (`codeload.github.com`), no mirror |
| Feishu | Version: `registry.npmjs.org`, mirror `registry.npmmirror.com`; program: `github.com`, mirror `registry.npmmirror.com` | GitHub (`codeload.github.com`), falling back to Feishu's own `open.feishu.cn` |
| DingTalk | Version: `registry.npmjs.org`, then `api.github.com`, `gitee.com`; program: `github.com`, mirror `gitee.com` | `github.com`, mirror `gitee.com` |

Check that the computer can reach at least one address in every cell,
then retry.
</details>

<details markdown="1"><summary><b>The WeCom CLI does not run on Windows?</b></summary>

The WeCom CLI needs the Microsoft VC++ runtime. If connecting says
"This computer can't run WeCom's official program", install the
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

<details markdown="1"><summary><b>Feishu says your admin has restricted some permissions?</b></summary>

The sign-in worked and the platform is enabled; only the permissions
listed were restricted by your Feishu organization admin, so the features
that need them won't work yet — nothing else is affected. To use them,
ask your Feishu admin to allow them, then click **Sign in again**.
</details>

<details markdown="1"><summary><b>Feishu says this sign-in can't renew itself?</b></summary>

The sign-in worked, but Feishu issued no credential to renew it: what
the AI does as your Feishu account stops working at the time shown
(about 2 hours after signing in), and you will have to sign in again;
what it does as the Feishu app is not affected. The cause: the Feishu app
has not turned on "Refresh user_access_token" in its security settings
in the developer console, or has not published a version since.

Fix: the admin of the Feishu app opens the link in the notice, turns on
"Refresh user_access_token", creates and publishes a new version; then
click **Sign in again** in Settings.
</details>

<details markdown="1"><summary><b>Does disabling delete data on the platform?</b></summary>

No. Disabling only takes the skills and the command away from agents;
your data on the platform, the CLI program and its login are untouched.
</details>
