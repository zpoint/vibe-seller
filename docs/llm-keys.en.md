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
>
> The provider console screenshots below show the **Chinese** UI (the China
> sites); button names are given in Chinese with an English gloss so you can
> match them. International sites have the same steps in English.

**Contents**

- [Which provider?](#which-provider)
- [The general flow](#the-general-flow)
- Getting a key, provider by provider:
    - [DeepSeek](#deepseek)
    - [Kimi](#kimi)
    - [MiniMax](#minimax)
    - [Zhipu GLM](#zhipu-glm)
    - [Qwen (Alibaba Cloud Model Studio)](#qwen-alibaba-cloud-model-studio)
    - [Xiaomi MiMo](#xiaomi-mimo)
    - [Claude](#claude)
- [FAQ](#faq)

## Which provider?

| Your situation | Suggestion |
|---|---|
| First time, just want to try it | **DeepSeek**: sign up, top up $2–3, pay per use, no monthly fee |
| Claude Code on this machine is already signed in to a Claude subscription | **No key needed** — reuse it, see [Claude](#claude) |
| Running every day, heavy usage, want it cheaper | Buy a **plan** (Token Plan / Coding Plan / M Plan) from one provider |
| Your account is outside mainland China | GLM, Qwen, MiniMax and MiMo all have international versions — pick the "International" preset or edit the endpoint |

### Pay-as-you-go vs. a plan

- **Pay-as-you-go**: top up, then pay for the tokens (the text the model reads
  and writes) you actually use.
- **Plan** (Token Plan / Coding Plan / M Plan, etc.): a fixed monthly price for
  a quota, usually capped by both a rolling 5-hour window and a weekly limit;
  cheaper per token if you use a lot.
- **A plan key and a pay-as-you-go key are different keys**, from different
  pages in the console, and they are not interchangeable. Where Vibe Seller has
  a preset with "Plan" in its name, a plan key needs that preset.

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
6. Paste the key into **API Key**. Model, `ANTHROPIC_BASE_URL` (the endpoint)
   and the rest are already filled in by the preset; usually leave them alone.
7. Keep **Use as my default after creating** on, and click **Create**.

When you click Create, Vibe Seller first **tests the connection** (the button
says "Testing connection…") by sending one tiny real request with your key. It
saves only if that works; otherwise it shows "Connection test failed" plus the
provider's reason — see the [FAQ](#faq).

![The Create Profile dialog with the DeepSeek preset picked and a key pasted](images/vibeseller_create_profile_en.png)

GLM, Alibaba Cloud and MiMo open a second row when clicked — Alibaba Cloud,
for example:

![The four options under Alibaba Cloud](images/vibeseller_preset_variants_en.png)

Which preset goes with which key:

| Provider | Pay-as-you-go key → pick | Plan key → pick |
|---|---|---|
| DeepSeek | `DeepSeek` | (no plan) |
| Kimi | `Kimi`, then change endpoint and model — see [Kimi](#kimi) | `Kimi` (a Kimi Code key from a Kimi membership) |
| MiniMax | `MiniMax` | `MiniMax` — same preset; paste the key that matches how you pay |
| Zhipu GLM (China) | `GLM › China` | `GLM › China` — same preset; paste the matching key |
| Z.AI (GLM international) | `GLM › International` | `GLM › International` — same |
| Qwen (China) | `Alibaba Cloud › Pay-as-you-go (China)` | `Alibaba Cloud › Coding Plan` or `Token Plan` — **whichever you bought** |
| Qwen (international) | `Alibaba Cloud › Pay-as-you-go (International)` | same, plus an endpoint change — see [Qwen](#qwen-alibaba-cloud-model-studio) |
| Xiaomi MiMo | `MiMo › Pay-as-you-go` | `MiMo › Token Plan (China cluster)` |
| Claude | the built-in `Claude` profile | the built-in `Claude` profile |

## DeepSeek

**Pay-as-you-go only — DeepSeek has no plan.** Good for a first try for a few
dollars.

**Pay-as-you-go:**

1. Open the [DeepSeek Platform](https://platform.deepseek.com/), sign up and log
   in. If it asks for identity verification, follow the prompts.
2. In the left menu click **充值** (Top up — [top-up page](https://platform.deepseek.com/top_up)),
   pick an amount and a payment method, then click 去支付 (Pay).

    ![DeepSeek top-up page](images/deepseek_topup_zh.png)

3. In the left menu click **API keys** ([API Keys page](https://platform.deepseek.com/api_keys)),
   then **创建 API key** (Create API key) in the top right.

    ![DeepSeek API keys page with "Create API key" in the top right](images/deepseek_apikeys_zh.png)

4. Give the key a name (e.g. `vibe-seller`), click **创建** (Create), and
   **copy it right away** — DeepSeek only shows the key when it is created.

    ![The Create API key dialog](images/deepseek_create_zh.png)

5. In Vibe Seller pick the **DeepSeek** preset, paste the key, create.

**Pitfall**: an empty balance returns `402 Insufficient Balance` — top up again.
Official docs: [Claude Code integration](https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code),
[error codes](https://api-docs.deepseek.com/quick_start/error_codes).

## Kimi

Kimi has both, but **the two keys come from two different platforms** and use
different endpoints.

**Plan (Kimi Code, part of a Kimi membership):**

1. Get a membership on the [Kimi membership page](https://www.kimi.com/membership/pricing).
   **Not every tier includes Kimi Code** — check the page before buying.
2. Open the [Kimi Code Console](https://www.kimi.com/code/console), click
   **Create API Key**, enter a name, confirm, and **copy it immediately** — once
   the dialog closes, the full key can't be shown again.
3. In Vibe Seller pick the **Kimi** preset, paste the key, create. The Kimi
   preset points at Kimi Code out of the box.

**Pay-as-you-go (Kimi API Platform):**

1. Sign up on the [Kimi API Platform](https://platform.kimi.ai/), top up, and
   create and copy a key on the [API Keys](https://platform.kimi.ai/console/api-keys)
   page (China accounts: [platform.kimi.com](https://platform.kimi.com/console/api-keys)).
2. In Vibe Seller pick the **Kimi** preset, paste the key, then change two
   fields:
    - set `ANTHROPIC_BASE_URL` to `https://api.moonshot.ai/anthropic`
      (China accounts: `https://api.moonshot.cn/anthropic`);
    - set **Model** to `kimi-k3[1m]` (the platform's model name, which differs
      from Kimi Code's): select the old value and paste over it.
3. Expand **Advanced environment variables** and check that
   `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`,
   `ANTHROPIC_DEFAULT_HAIKU_MODEL`, `ANTHROPIC_DEFAULT_FABLE_MODEL` and
   `CLAUDE_CODE_SUBAGENT_MODEL` also read `kimi-k3[1m]`. If you cleared the
   Model field and then typed, these rows end up empty — fill them in by hand.
4. Click **Create**; once the connection test passes you're done.

**Pitfall**: a platform key won't work on the Kimi Code endpoint, and a Kimi
Code key won't work on the platform endpoint — on a 401, first check that key
and endpoint come from the same platform.
Official docs: [Kimi Code in Claude Code](https://www.kimi.com/code/docs/en/third-party-tools/claude-code.html),
[Kimi API Platform in Claude Code](https://platform.kimi.ai/docs/guide/claude-code-kimi).

## MiniMax

MiniMax has both; its plan is called **M Plan** (formerly Token Plan). **The two
keys live on different console pages and are not interchangeable.** In Vibe
Seller both go into the same **MiniMax** preset — the key you paste decides how
you're billed. China accounts sign up on the [MiniMax Open Platform](https://platform.minimax.cn/).

**Pay-as-you-go:**

1. Top up under **费用管理 → 账户钱包** (Billing → Wallet —
   [balance page](https://platform.minimax.cn/user-center/payment/balance)).
2. Go to **API 管理 → API Key** ([API Key page](https://platform.minimax.cn/user-center/basic-information/interface-key)),
   click **创建 API Key** (Create API Key) in the top right, and copy it.

    ![MiniMax API Key page with "Create API Key" in the top right](images/minimax_interface_key_zh.png)

3. In Vibe Seller pick the **MiniMax** preset, paste, create.

**Plan (M Plan):**

1. Choose a tier and pay on the [plan details page](https://platform.minimax.cn/console/plan).
2. Open **个人订阅 → 套餐管理** (Subscription → Plan management). The
   **订阅 Key** (Subscription Key) at the bottom is your plan key: one per
   account, shown masked; use the copy icon to copy it, and **重置 Key** (Reset
   Key) if it ever leaks. The page itself says a Subscription Key and a
   pay-as-you-go API key are not interchangeable.

    ![MiniMax plan management page showing the Subscription Key](images/minimax_plan_key_zh.png)

3. In Vibe Seller pick the same **MiniMax** preset, paste the Subscription Key,
   create.

**International accounts**: sign up at [platform.minimax.io](https://platform.minimax.io/);
the menus are `API Keys > Create new secret key` (pay-as-you-go) and
`Billing > M Plan` (plan). International keys only work on the international
endpoint: after picking the MiniMax preset, change the `ANTHROPIC_BASE_URL`
field to `https://api.minimax.io/anthropic` before you click Create.

Official docs: [Prerequisites](https://platform.minimax.io/docs/guides/quickstart-preparation),
[M Plan FAQ](https://platform.minimax.io/docs/m-plan/faq),
[Claude Code setup (China)](https://platform.minimax.cn/docs/token-plan/claude-code).

## Zhipu GLM

China users use the **Zhipu BigModel platform** (preset `GLM › China`); users
outside China use **Z.AI** (preset `GLM › International`). Both have
pay-as-you-go and a GLM Coding Plan. **The two keys come from different pages
and aren't interchangeable**; in Vibe Seller both go into the same preset, and
the key you paste decides what it bills against.

> Since October 16, 2026, Zhipu / Z.AI manage "standard API keys"
> (pay-as-you-go / usage bundles) and "Coding Plan keys" separately. Older keys
> keep working and were classified automatically.

### China: Zhipu BigModel

Click "Register/Login" in the top right of [Zhipu BigModel](https://open.bigmodel.cn).
Real-name verification isn't required to call the API, but Zhipu recommends
[doing it](https://open.bigmodel.cn/usercenter/auth).

**Pay-as-you-go:** top up, then on the [standard API key page](https://bigmodel.cn/usercenter/proj-mgmt/apikeys)
click **新建API Key** (New API Key) in the top right. The notice at the top of
that page says keys created there are for the standard (pay-as-you-go) API only.

![BigModel standard API key page with "New API Key" in the top right](images/glm_bigmodel_apikeys_zh.png)

**Plan (GLM Coding Plan):** buy it with **立即订阅** (Subscribe now) under
**Coding Plan → 套餐概览** (Plan Overview); after subscribing, create a key under
**Personal Coding Plan → Plan Overview** (team members: **Team Coding Plan → My
Plan**).

![BigModel Coding Plan overview with the "Subscribe now" button](images/glm_bigmodel_codingplan_zh.png)

Either key: in Vibe Seller click **GLM ›**, then **China**, paste, create.

### International: Z.AI

Register or log in on the [Z.AI Open Platform](https://z.ai/model-api).

**Pay-as-you-go:** create a key under **Standard API Key** on the
[API Keys](https://z.ai/manage-apikey/apikey-list) page.

**Plan (GLM Coding Plan):** subscribe on the [plan page](https://z.ai/subscribe),
then create a key under **Personal Coding Plan** on the
[API Keys](https://z.ai/manage-apikey/apikey-list) page (team plan:
[Team Coding Plan → My Plan](https://z.ai/manage-apikey/coding-plan/team/my-plan)).

Either key: in Vibe Seller click **GLM ›**, then **International**, paste, create.

Official docs: [Z.AI API Key Management Guide](https://docs.z.ai/devpack/notice/api-key-management),
[Z.AI Quick Start](https://docs.z.ai/devpack/quick-start),
[BigModel key classification (zh)](https://docs.bigmodel.cn/cn/coding-plan/notice/api-key-management).

## Qwen (Alibaba Cloud Model Studio)

Model Studio (Bailian) has pay-as-you-go and two plans (Coding Plan, Token
Plan). **Each kind of key has its own page and its own endpoint, and they must
match**, so clicking **Alibaba Cloud ›** in Vibe Seller shows four options.

**Pay-as-you-go:**

1. Open [Bailian console → API Key](https://bailian.console.aliyun.com/cn-beijing/model/settings/api-key)
   and **pick the right region in the top bar first** (e.g. 华北2（北京）,
   North China 2 (Beijing) — keys don't work across regions).
2. In the 按量付费 (pay-as-you-go) section click **创建API-KEY** (Create
   API key), leave owner account and workspace at their defaults, set
   permissions to **All**, click **OK**.

    ![Bailian API Key page: region picker at the top, "Create API key" in the pay-as-you-go section](images/qwen_bailian_apikey_zh.png)

3. **Copy or download it now** — new keys are shown in full only once. The key
   starts with `sk-`.
4. In Vibe Seller click **Alibaba Cloud ›**, pick **Pay-as-you-go (China)**,
   paste, create. International (Singapore) accounts create the key on the API
   Key page of the international [Model Studio console](https://modelstudio.console.alibabacloud.com/),
   pick **Pay-as-you-go (International)**, and replace `{WorkspaceId}` in the
   endpoint with their own Workspace ID (you can't save until you do; to find
   it, follow the [official regions page](https://www.alibabacloud.com/help/en/model-studio/regions)).

**Plan (Coding Plan):** buy it on the [Coding Plan page](https://bailian.console.aliyun.com/cn-beijing/subscription/coding-plan);
the dedicated key (starting with `sk-sp-`) is on the same page. In Vibe Seller
pick **Alibaba Cloud › Coding Plan**.

**Plan (Token Plan):** buy it from **TokenPlan** in Bailian's top navigation
(personal or team edition). After buying, use "Create or view API Key" on the
[Token Plan personal page](https://bailian.console.aliyun.com/cn-beijing/subscription/token-plan/personal);
for teams an admin assigns a seat in [member management](https://bailian.console.aliyun.com/cn-beijing/subscription/uac-admin/organization/members/list).
In Vibe Seller pick **Alibaba Cloud › Token Plan**.

![Bailian Token Plan purchase page (personal / team edition)](images/qwen_tokenplan_zh.png)

**Pitfalls**:

- **`401 invalid_api_key` almost always means the key and preset don't match**
  — e.g. a Coding Plan `sk-sp-` key on a pay-as-you-go preset. Create the
  profile again with the matching option.
- The Coding Plan and Token Plan presets use the **China** endpoints. If you
  bought the plan on the international site, set the `ANTHROPIC_BASE_URL`
  field to the international endpoint from the official docs (Coding Plan:
  `https://coding-intl.dashscope.aliyuncs.com/apps/anthropic`; Token Plan:
  `https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic`).

Official docs: [Get an API key (zh)](https://help.aliyun.com/zh/model-studio/get-api-key),
[Claude Code (international)](https://www.alibabacloud.com/help/en/model-studio/claude-code),
[Claude Code (China, zh)](https://help.aliyun.com/zh/model-studio/claude-code),
[Coding Plan (zh)](https://help.aliyun.com/zh/model-studio/coding-plan).

## Xiaomi MiMo

MiMo has both. **The two keys aren't interchangeable and use different
endpoints**, so Vibe Seller has two presets. Log in to the
[MiMo API Platform](https://platform.xiaomimimo.com/) with a Xiaomi account (you
can register from the login page). **Users in mainland China must complete
real-name verification** (personal or business) before topping up or buying a
plan.

**Pay-as-you-go:**

1. Top up under **账户余额** (Account Balance — [balance page](https://platform.xiaomimimo.com/#/console/balance);
   overseas: Apple Pay, Google Pay, cards).
2. Under **API Keys** ([API Keys page](https://platform.xiaomimimo.com/#/console/api-keys))
   click **新建 API Key** (New API Key) in the top right — the key starts with
   `sk-`.

    ![MiMo console API Keys page with "New API Key" in the top right](images/mimo_apikeys_zh.png)

3. In Vibe Seller pick **MiMo › Pay-as-you-go**, paste, create.

**Plan (Token Plan):**

1. Under **Token Plan** in the left menu, choose the personal or team edition
   and a tier, and click 立即订阅 (Subscribe now). Account balance can't pay for
   a Token Plan; it's bought separately.

    ![MiMo console Token Plan subscription page](images/mimo_tokenplan_zh.png)

2. Get your dedicated key on the [Token Plan management page](https://platform.xiaomimimo.com/#/console/plan-manage)
   (`tp-` for individual, `ttp-` for team; team plans need a seat assigned
   first).
3. In Vibe Seller pick **MiMo › Token Plan (China cluster)**, paste, create.

**Pitfalls**:

- `sk-` pay-as-you-go keys and `tp-`/`ttp-` plan keys can't be swapped — pick
  the matching preset.
- The Token Plan has China, Singapore and Europe clusters. The preset defaults
  to China; if the Base URL on your Token Plan page doesn't start with
  `token-plan-cn`, set the `ANTHROPIC_BASE_URL` field to the one shown there.
  Overseas accounts get their own endpoints for pay-as-you-go too — use what the
  console shows.
- Token Plan calls between 00:00 and 08:00 Beijing time use quota at 0.8×.

Official docs: [First API call](https://mimo.mi.com/docs/quick-start/summary/first-api-call),
[Token Plan quick access](https://mimo.mi.com/docs/tokenplan/Token%20Plan/quick-access),
[Claude Code configuration](https://mimo.mi.com/docs/tokenplan/integration/claudecode).

## Claude

Claude has the highest ceiling. Either way, you **sign in to Claude Code on this
machine** and Vibe Seller's built-in **Claude** profile ("System default
environment") uses that sign-in — **no key is pasted into Vibe Seller**.

**Pay-as-you-go (Claude Console):** sign up on the [Claude Console](https://platform.claude.com/)
and add credit under [Billing](https://platform.claude.com/settings/billing)
(keys live on the [API keys](https://platform.claude.com/settings/keys) page).
The simplest route is to choose the Console account at step 2 below; Claude Code
handles the key for you.

**Plan (Claude Pro / Max subscription):** subscribe on the [pricing page](https://claude.com/pricing),
then sign in with your claude.ai account at step 2 below.

Signing in:

1. Install Claude Code following the [official setup guide](https://code.claude.com/docs/en/setup).
2. Run `claude` in a terminal; a browser login page opens. Choose the claude.ai
   account or the Console account, depending on how you pay.
3. When the terminal shows `Login successful`, you're done.
4. Back in Vibe Seller, `Settings → AI`: if the "Claude" row has no
   **★ Default** mark, click **Set as Default**.

![Settings → AI: the built-in Claude profile marked "★ Default"](images/vibeseller_claude_default_en.png)

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

Immediately **delete (or disable / reset) that key** in the provider's console
and create a new one. Then in Vibe Seller `Settings → AI`, find the profile
using it, click **Edit**, paste the new key into **API Key**, and click **Save**.
A leaked key lets anyone spend your money — don't wait.

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
- **429**: too many requests, or **your plan quota is used up** (the 5-hour or
  weekly cap). Wait for it to come back, or upgrade; if it's urgent, create a
  profile for another provider in Vibe Seller to cover in the meantime.

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
