---
description: Getting an LLM API key — where to sign up, where to click to create a key, pay-as-you-go vs. plan, and which Vibe Seller preset to pick for DeepSeek, Kimi, MiniMax, GLM, Qwen, Xiaomi MiMo and Claude.
---

# Get an API Key

Vibe Seller itself is free; the AI model doing the work runs on **your own API
key**. Think of it as a password plus billing account the model provider gives
you: Vibe Seller uses it to call the model, and the cost lands on your account
with that provider.

This page covers three things for each provider: **where to sign up, where to
click to create the key, and which preset to pick in Vibe Seller**. New to all
this? Start with "Which provider?" below.

> Provider consoles change often. This page follows each provider's official
> docs **as of November 2026**, and every section links them. If the screen
> doesn't match the text, trust the official page.

## Which provider?

| Your situation | Suggestion |
|---|---|
| First time, just want to try it | **DeepSeek**: sign up, top up $2–3, pay per use, no monthly fee |
| Claude Code on this machine is already signed in to a Claude subscription | **No key needed** — reuse it, see [Claude](#claude) |
| Running every day, heavy usage, want it cheaper | Buy a **plan** (Token Plan / Coding Plan / M Plan) from one provider |
| Your account is outside mainland China | GLM, Qwen, MiniMax and MiMo all have international versions — pick the "International" preset or edit the Base URL |

### Pay-as-you-go vs. a plan

- **Pay-as-you-go**: top up first, then each call is deducted by usage (billed
  per token — a token is a small chunk of the text the model reads or writes).
  Good while you are starting out or usage is uneven.
- **Plan** (Token Plan / Coding Plan / M Plan, etc.): a fixed monthly fee for a
  usage quota; when it runs out you wait for the next cycle or upgrade. Cheaper
  per unit at high volume.
- **A plan key and a pay-as-you-go key are different keys**, and most providers
  don't let you swap one for the other. Some (Qwen, MiMo) also use a different
  endpoint, so Vibe Seller has separate presets for them: **a plan key needs the
  preset with "Plan" in its name**, or the connection test fails.
- Plans come with usage terms — for example, only inside officially supported
  coding tools, or no unattended automation. Vibe Seller runs on Claude Code, but
  scheduled tasks are unattended calls, so **read the provider's terms before
  buying a plan**; if unsure, start with pay-as-you-go.

## The general flow

Every provider follows these steps; the sections below only cover what differs:

1. **Sign up** on the provider's site (mainland-China platforms usually want a
   phone number and may require real-name verification before you pay).
2. **Top up** (pay-as-you-go) or **buy a plan**.
3. **Create an API key** in the console and **copy it right away** — many
   providers show it in full only once.
4. In Vibe Seller: `Settings → AI`, then click **Create Profile** in the top
   right of "AI Profiles".
5. Under "Environment Presets", click the provider. **GLM, Alibaba Cloud and
   MiMo** open a second row of options (China / International, pay-as-you-go /
   plan) — pick the one that matches your key.
6. Paste the key into **API Key**. Model, Base URL and the rest are already
   filled in by the preset; usually leave them alone.
7. Keep **Use as my default after creating** on, and click **Create**.

When you click Create, Vibe Seller first **tests the connection** (the button
says "Testing connection…") by sending one tiny real request with your key. It
saves only if that works; otherwise it shows "Connection test failed" plus the
provider's reason — see the [FAQ](#faq).

<!-- SHOT: vibeseller_create_profile_en.png — Settings → AI → "Create Profile" dialog: preset buttons row, API Key field, Create button (key masked) -->

<!-- SHOT: vibeseller_preset_variants_en.png — "Alibaba Cloud ›" expanded: Pay-as-you-go (China) / Pay-as-you-go (International) / Coding Plan / Token Plan -->

Which preset goes with which key:

| Provider | Pay-as-you-go key → pick | Plan key → pick |
|---|---|---|
| DeepSeek | `DeepSeek` | (no plan) |
| Kimi | (not supported by this preset) | `Kimi` (a Kimi Code key from a Kimi membership) |
| MiniMax | `MiniMax` | `MiniMax` (same preset; the key decides billing) |
| Zhipu GLM (China) | `GLM › China` | `GLM › China` (same preset; the key decides billing) |
| Z.AI (GLM international) | `GLM › International` | `GLM › International` |
| Qwen (China) | `Alibaba Cloud › Pay-as-you-go (China)` | `Coding Plan` or `Token Plan` — **whichever you bought** |
| Qwen (international) | `Alibaba Cloud › Pay-as-you-go (International)` | see [Qwen](#qwen-alibaba-cloud-model-studio) |
| Xiaomi MiMo | `MiMo › Pay-as-you-go` | `MiMo › Token Plan (China cluster)` |
| Claude | the built-in `Claude` profile | the built-in `Claude` profile |

## DeepSeek

**Good for**: a first try for a few dollars. Pay-as-you-go only, no monthly fee.

1. Open the [DeepSeek Platform](https://platform.deepseek.com/), sign up and log
   in. If it asks for identity verification, follow the prompts.
2. Top up on the [top-up page](https://platform.deepseek.com/top_up).
3. Create a key on the [API Keys page](https://platform.deepseek.com/api_keys)
   and copy it right away.
4. In Vibe Seller pick the **DeepSeek** preset, paste the key, create.

<!-- SHOT: deepseek_apikeys_en.png — DeepSeek Platform API Keys page, where the "Create API key" button is (existing keys masked) -->

<!-- SHOT: deepseek_topup_en.png — DeepSeek Platform top-up page -->

**Pitfall**: an empty balance returns `402 Insufficient Balance` — top up again.
Official docs: [Claude Code integration](https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code),
[error codes](https://api-docs.deepseek.com/quick_start/error_codes).

## Kimi

**Good for**: people who already have a Kimi membership. Vibe Seller's Kimi
preset uses the **Kimi Code** endpoint, so your membership must include Kimi Code.

1. Get a membership on the [Kimi membership page](https://www.kimi.com/membership/pricing).
   **Not every tier includes Kimi Code** — check the page before buying.
2. Open the [Kimi Code Console](https://www.kimi.com/code/console), click
   **Create API Key**, enter a name, confirm.
3. **Copy it immediately** — once the dialog closes, the full key can't be shown
   again.
4. In Vibe Seller pick the **Kimi** preset, paste the key, create.

<!-- SHOT: kimi_code_console_en.png — Kimi Code Console, where the "Create API Key" button is -->

**Pitfall**: the official docs only cover keys from the **Kimi Code Console**.
They don't say whether a pay-as-you-go key from the Kimi open platform works here,
so use a Kimi Code Console key.
Official docs: [Use in Claude Code](https://www.kimi.com/code/docs/en/third-party-tools/claude-code.html).

## MiniMax

**Good for**: people who want either pay-as-you-go or a plan. MiniMax's plan is
called **M Plan** (formerly Token Plan). Both kinds of key use the **same Vibe
Seller preset**; the key you paste decides which balance is charged.

China accounts sign up on the [MiniMax Open Platform](https://platform.minimax.cn/),
then pick one:

- **Pay-as-you-go**: top up under [Account > Balance](https://platform.minimax.cn/user-center/payment/balance),
  then get a key at [API Keys > Create new API Key](https://platform.minimax.cn/user-center/basic-information/interface-key).
- **M Plan**: choose a tier and pay on the [plan details page](https://platform.minimax.cn/console/plan);
  your **Subscription Key** is shown on the same page.

Then pick the **MiniMax** preset in Vibe Seller, paste, create.

<!-- SHOT: minimax_interface_key_en.png — MiniMax platform API key page, where "Create new API Key" is -->

<!-- SHOT: minimax_plan_key_en.png — MiniMax plan details page, where the Subscription Key is (key masked) -->

**International accounts**: sign up at [platform.minimax.io](https://platform.minimax.io/);
the menus are `API Keys > Create new secret key` (pay-as-you-go) and
`Billing > M Plan` (plan). International keys only work on the international
endpoint: after picking the MiniMax preset, expand **Advanced environment
variables** and change `ANTHROPIC_BASE_URL` to `https://api.minimax.io/anthropic`
before you click Create.

**Pitfalls**:

- A Subscription Key only draws on plan quota and a pay-as-you-go key only on
  your balance; they're not interchangeable.
- MiniMax says M Plan is meant for individual developers' interactive use and
  **recommends pay-as-you-go for production**.

Official docs: [Prerequisites](https://platform.minimax.io/docs/guides/quickstart-preparation),
[M Plan FAQ](https://platform.minimax.io/docs/m-plan/faq),
[Claude Code setup (China)](https://platform.minimax.cn/docs/token-plan/claude-code).

## Zhipu GLM

**Good for**: China users pick the **Zhipu BigModel platform** (preset
`GLM › China`); users outside China pick **Z.AI** (preset `GLM › International`).
Both have pay-as-you-go and a GLM Coding Plan.

> **Since October 16, 2026**, Zhipu / Z.AI manage "standard API keys"
> (pay-as-you-go / usage bundles) and "Coding Plan keys" separately: **the type
> of a new key decides what it bills against**. Using a standard key while you
> have a plan does not use plan quota — it charges your balance. Older keys keep
> working and were classified automatically.

### China: Zhipu BigModel

1. Open [Zhipu BigModel](https://open.bigmodel.cn) and click "Register/Login" in
   the top right. Real-name verification isn't required to call the API, but
   Zhipu recommends [doing it](https://open.bigmodel.cn/usercenter/auth).
2. Pick one:
    - **Pay-as-you-go**: top up, then create a key on the
      [standard API key page](https://bigmodel.cn/usercenter/proj-mgmt/apikeys).
    - **GLM Coding Plan**: after subscribing, create a key under
      **Personal Coding Plan → Plan Overview** (team members: **Team Coding
      Plan → My Plan**).
3. In Vibe Seller click **GLM ›**, then **China**, paste, create.

<!-- SHOT: glm_bigmodel_apikeys_en.png — BigModel standard API key page, where the create button is -->

<!-- SHOT: glm_bigmodel_codingplan_key_en.png — BigModel Personal Coding Plan overview, where to create the plan key -->

### International: Z.AI

1. Open the [Z.AI Open Platform](https://z.ai/model-api), register or log in.
2. Pick one:
    - **Pay-as-you-go**: create a key under **Standard API Key** on the
      [API Keys](https://z.ai/manage-apikey/apikey-list) page.
    - **GLM Coding Plan**: subscribe on the [plan page](https://z.ai/subscribe),
      then create a key under **Personal Coding Plan** on the
      [API Keys](https://z.ai/manage-apikey/apikey-list) page (team plan:
      [Team Coding Plan → My Plan](https://z.ai/manage-apikey/coding-plan/team/my-plan)).
3. In Vibe Seller click **GLM ›**, then **International**, paste, create.

<!-- SHOT: glm_zai_apikeys_en.png — Z.AI API Keys page showing the Standard API Key and Personal Coding Plan entries -->

**Pitfall**: GLM Coding Plan is officially **limited to supported tools**
(Claude Code is on the list) — read the terms before buying.
Official docs: [Z.AI API Key Management Guide](https://docs.z.ai/devpack/notice/api-key-management),
[Z.AI Quick Start](https://docs.z.ai/devpack/quick-start),
[BigModel key classification (zh)](https://docs.bigmodel.cn/cn/coding-plan/notice/api-key-management).

## Qwen (Alibaba Cloud Model Studio)

**Good for**: people who already have an Alibaba Cloud account. Model Studio
(Bailian) has **four** ways in, and **each key only works with its own
endpoint**, so clicking **Alibaba Cloud ›** in Vibe Seller shows four options:

| What you bought | Key looks like | Where to get it | Pick in Vibe Seller |
|---|---|---|---|
| Pay-as-you-go (China / Beijing) | starts with `sk-` | [Bailian console → API Key](https://bailian.console.aliyun.com/cn-beijing/model/settings/api-key) | `Pay-as-you-go (China)` |
| Pay-as-you-go (International / Singapore) | starts with `sk-` | the API Key page of the international [Model Studio console](https://modelstudio.console.alibabacloud.com/) | `Pay-as-you-go (International)` |
| Coding Plan | starts with `sk-sp-` | [Coding Plan page](https://bailian.console.aliyun.com/cn-beijing/subscription/coding-plan) | `Coding Plan` |
| Token Plan (personal / team) | see console | "Create or view API Key" on the [Token Plan personal page](https://bailian.console.aliyun.com/cn-beijing/subscription/token-plan/personal); for teams an admin assigns a seat in [member management](https://bailian.console.aliyun.com/cn-beijing/subscription/uac-admin/organization/members/list) | `Token Plan` |

**Creating a pay-as-you-go key** (China):

1. Open the API Key page in the Bailian console and **pick the right region in
   the top right first** (keys don't work across regions).
2. Click **Create API Key**, leave owner account and workspace at their
   defaults, set permissions to **All**, click **OK**.
3. **Copy or download it now** — new keys are shown in full only once.
4. In Vibe Seller click **Alibaba Cloud ›**, pick **Pay-as-you-go (China)**,
   paste, create.

<!-- SHOT: qwen_bailian_apikey_en.png — Model Studio API Key page: region selector top right + "Create API Key" button -->

<!-- SHOT: qwen_codingplan_key_en.png — Model Studio Coding Plan page, where the dedicated sk-sp- key is (masked) -->

<!-- SHOT: qwen_tokenplan_key_en.png — Model Studio Token Plan personal page, "Create or view API Key" button -->

**Pitfalls**:

- **`401 invalid_api_key` almost always means the key and preset don't match**
  — e.g. a Coding Plan `sk-sp-` key on a pay-as-you-go preset. Create the
  profile again with the matching option.
- The **international pay-as-you-go** preset's Base URL contains a
  `{WorkspaceId}` placeholder; you can't save until you replace it with your own
  Workspace ID (the field is highlighted). To find it, follow the
  [official regions page](https://www.alibabacloud.com/help/en/model-studio/regions).
- The Coding Plan and Token Plan presets use the **China** endpoints. If you
  bought the plan on the international site, expand **Advanced environment
  variables** and set `ANTHROPIC_BASE_URL` to the international endpoint from the
  official docs (Coding Plan: `https://coding-intl.dashscope.aliyuncs.com/apps/anthropic`;
  Token Plan: `https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic`).
- The terms say **Coding Plan is only for interactive use inside coding tools**
  — automated scripts and non-interactive batch calls are prohibited and may get
  the key banned. Vibe Seller's scheduled tasks are unattended calls; for
  long-term use, prefer pay-as-you-go, or read the Token Plan terms first.

Official docs: [Get an API key (zh)](https://help.aliyun.com/zh/model-studio/get-api-key),
[Claude Code (international)](https://www.alibabacloud.com/help/en/model-studio/claude-code),
[Claude Code (China, zh)](https://help.aliyun.com/zh/model-studio/claude-code),
[Coding Plan (zh)](https://help.aliyun.com/zh/model-studio/coding-plan).

## Xiaomi MiMo

**Good for**: people with a Xiaomi account. Both pay-as-you-go and a Token Plan
are available; **the keys are not interchangeable**.

1. Log in to the [MiMo API Platform](https://platform.xiaomimimo.com/) with a
   Xiaomi account (you can register from the login page). **Users in mainland
   China must complete real-name verification** (personal or business) before
   topping up or buying a plan.
2. Pick one:
    - **Pay-as-you-go**: top up on [Account Balance](https://platform.xiaomimimo.com/#/console/balance)
      (overseas: Apple Pay, Google Pay, cards), then create a key on
      [API Keys](https://platform.xiaomimimo.com/#/console/api-keys) — it starts
      with `sk-`. In Vibe Seller pick **MiMo › Pay-as-you-go**.
    - **Token Plan**: subscribe on the [Token Plan page](https://platform.xiaomimimo.com/#/token-plan),
      then get your dedicated key on the [Token Plan management page](https://platform.xiaomimimo.com/#/console/plan-manage)
      (`tp-` for individual, `ttp-` for team; team plans need a seat assigned
      first). In Vibe Seller pick **MiMo › Token Plan (China cluster)**.
3. Paste, create.

<!-- SHOT: mimo_apikeys_en.png — MiMo platform API Keys page, where the create button is -->

<!-- SHOT: mimo_tokenplan_manage_en.png — MiMo Token Plan management page, where the dedicated key and Base URL are (key masked) -->

**Pitfalls**:

- `sk-` pay-as-you-go keys and `tp-`/`ttp-` plan keys can't be swapped — pick
  the matching preset.
- The Token Plan has China, Singapore and Europe clusters. The preset defaults
  to China; if the Base URL on your Token Plan page doesn't start with
  `token-plan-cn`, expand **Advanced environment variables** and set
  `ANTHROPIC_BASE_URL` to the one shown there. Overseas accounts get their own
  endpoints for pay-as-you-go too — use what the console shows.
- Account balance can't pay for a Token Plan; it's bought separately. Calls
  between 00:00 and 08:00 Beijing time use quota at 0.8×.

Official docs: [First API call](https://mimo.mi.com/docs/quick-start/summary/first-api-call),
[Token Plan quick access](https://mimo.mi.com/docs/tokenplan/Token%20Plan/quick-access),
[Claude Code configuration](https://mimo.mi.com/docs/tokenplan/integration/claudecode).

## Claude

Claude has the highest ceiling. Both ways to use it need **no key pasted into
Vibe Seller**.

### Reuse Claude Code already signed in on this machine (recommended)

Vibe Seller runs on Claude Code. Once Claude Code on this machine is signed in,
Vibe Seller's built-in **Claude** profile ("System default environment") uses
that sign-in.

1. Install Claude Code following the [official setup guide](https://code.claude.com/docs/en/setup).
2. Run `claude` in a terminal; a browser login page opens. You can sign in with:
    - a **Claude Pro / Max subscription** — your claude.ai account
      ([pricing](https://claude.com/pricing));
    - a **Claude Console account** (billed by API usage) — choose the Console
      account at login.
3. When the terminal shows `Login successful`, you're done.
4. Back in Vibe Seller, `Settings → AI`: if the "Claude" row has no
   **★ Default** mark, click **Set as Default**.

<!-- SHOT: vibeseller_claude_default_en.png — Settings → AI with the built-in "Claude" profile marked "★ Default" -->

### An Anthropic Console API key

Sign up on the [Claude Console](https://platform.claude.com/), add credit under
[Billing](https://platform.claude.com/settings/billing), and create keys on the
[API keys](https://platform.claude.com/settings/keys) page. The simplest way to
use Console billing is still step 2 above: sign in to Claude Code with the
Console account.

**Pitfalls**:

- Claude is available only in [supported countries and regions](https://platform.claude.com/docs/en/api/supported-regions);
  check before signing up.
- If you use cc-switch or a similar tool, or have `ANTHROPIC_API_KEY` set in
  your environment, Claude Code uses that instead of your sign-in. Type
  `/status` in Claude Code to see which credential is active.

Official docs: [Claude Code authentication](https://code.claude.com/docs/en/authentication),
[Claude API overview](https://platform.claude.com/docs/en/api/overview).

## FAQ

<details markdown="1">
<summary><b>The key was shown only once and I didn't copy it</b></summary>

You can't get it back, and you don't need to. **Delete** that key in the
provider's console, **create a new one**, and copy it this time.

</details>

<details markdown="1">
<summary><b>I accidentally shared my key / pasted it in a group chat</b></summary>

Immediately **delete (or disable) that key** in the provider's console and create
a new one. Then in Vibe Seller `Settings → AI`, find the profile using it, click
**Edit**, paste the new key into **API Key**, and click **Save**. A leaked key lets
anyone spend your money — don't wait.

</details>

<details markdown="1">
<summary><b>The connection test says 401 / authentication failed</b></summary>

Check in this order:

1. The key was copied completely (no extra spaces, nothing cut off).
2. The key hasn't been deleted or expired.
3. **The key matches the preset** — most common: a plan key on a pay-as-you-go
   preset, or an international key on a China preset.

</details>

<details markdown="1">
<summary><b>Insufficient balance (402) / quota exhausted (429)</b></summary>

- **Insufficient balance**: the pay-as-you-go account is empty — top up.
- **429**: too many requests, or **your plan quota is used up**. Plan quota
  comes back when the cycle resets, or upgrade; if it's urgent, create a profile
  for another provider in Vibe Seller to cover in the meantime.

</details>

<details markdown="1">
<summary><b>I picked the wrong preset for my plan key</b></summary>

An existing profile can't switch presets when you edit it. Click **Create
Profile**, pick the **right** preset, paste the key, create, then delete the
wrong one.

</details>

<details markdown="1">
<summary><b>Where is the key stored? Is it safe?</b></summary>

The key stays inside Vibe Seller on your own machine, encrypted, and is never
uploaded anywhere; your machine sends it straight to the provider when calling
the model.

</details>
