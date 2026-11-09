---
description: 获取 LLM API Key：DeepSeek、Kimi、MiniMax、智谱 GLM、通义千问、小米 MiMo、Claude 各家去哪注册、在哪创建 Key、按量付费还是买套餐，以及在 Vibe Seller 里该选哪个预设。
---

# 获取 API Key

Vibe Seller 本身免费，干活的 AI 模型要用你自己的 **API Key**——可以理解成
模型供应商发给你的「密码 + 账单号」：Vibe Seller 拿着它去调用模型，费用记在
你的供应商账户上。

这一页按供应商讲清楚三件事：**去哪注册、在哪点出 Key、在 Vibe Seller 里选哪
个预设**。第一次用、不知道怎么选的话，先看下面的「先选哪家」。

> 各家控制台改版很勤快。本文按 **2026 年 11 月** 各家官方文档整理，
> 每一节都附了官方链接；界面和文中对不上时，以官方页面为准。

## 先选哪家

| 你的情况 | 建议 |
|---|---|
| 第一次用，想先试试 | **DeepSeek**：注册、充 15–20 块就能跑，按用量扣钱，没有月费 |
| 这台电脑上的 Claude Code 已经登录了 Claude 订阅 | **不用拿 Key**，直接复用，见 [Claude](#claude) |
| 天天在跑、用量大，想省钱 | 买一家的**套餐**（Token Plan / Coding Plan / M Plan），见下面各家 |
| 公司或账号在海外 | GLM、通义、MiniMax、MiMo 都有国际版，选对应的「International」预设或改 Base URL |

### 按量付费 vs 套餐，有什么区别？

- **按量付费（Pay-as-you-go）**：先充值，用多少扣多少（按 token 计费，token
  就是模型读写的文字量）。适合刚开始、用量不稳定的时候。
- **套餐（Token Plan / Coding Plan / M Plan 等）**：按月付固定的钱，换一份额度，
  用完要等下个周期或升级。用量大时单价更低。
- **套餐的 Key 和按量的 Key 是两种不同的 Key**，大多数家不能混用。有几家
  （通义、MiMo）连接地址也不一样，所以 Vibe Seller 给它们分了不同的预设：
  **套餐 Key 一定要选带「Plan」的预设**，否则测试连接会失败。
- 套餐一般都有使用条款，比如只能在官方支持的编程工具里用、不允许无人值守的
  自动化调用。Vibe Seller 底层跑的是 Claude Code，但定时任务属于无人值守调用，
  **买套餐前请先读清楚该家的条款**；拿不准就先用按量付费。

## 通用流程

每一家都是这几步，下面各节只讲每家不一样的地方：

1. 在供应商官网**注册**账号（国内平台通常要手机号，充值前可能要求实名认证）。
2. **充值**（按量付费）或**购买套餐**。
3. 在控制台**创建 API Key**，**马上复制**——很多家只完整显示一次。
4. 打开 Vibe Seller：`设置 → AI`，在「AI 配置文件」右上角点 **创建配置文件**。
5. 在「环境预设」里点对应的供应商。**GLM、Alibaba Cloud、MiMo** 点了以后会再
   展开一排子选项（国内 / 国际、按量 / 套餐），选和你的 Key 对应的那个。
6. 把 Key 粘到 **API 密钥** 一栏。模型、Base URL 等预设已经填好，一般不用动。
7. 保持勾选 **创建后设为我的默认 AI**，点 **创建**。

点「创建」时 Vibe Seller 会先**测试连接**（按钮显示「正在测试连接…」），用你的
Key 真的向供应商发一条很短的请求。通过了才会保存；不通过会显示「连接测试失败」
和供应商返回的原因，对照下面的[常见问题](#常见问题)改。

<!-- SHOT: vibeseller_create_profile_zh.png — 设置 → AI → 「创建配置文件」弹窗：环境预设按钮一排、API 密钥输入框、「创建」按钮（Key 打码） -->

<!-- SHOT: vibeseller_preset_variants_zh.png — 点开「Alibaba Cloud ›」后展开的子选项：Pay-as-you-go (China) / Pay-as-you-go (International) / Coding Plan / Token Plan -->

预设按钮和 Key 的对应关系一览：

| 供应商 | 按量付费的 Key → 选 | 套餐的 Key → 选 |
|---|---|---|
| DeepSeek | `DeepSeek` | （无套餐） |
| Kimi | （本预设不支持按量） | `Kimi`（Kimi 会员的 Kimi Code Key） |
| MiniMax | `MiniMax` | `MiniMax`（同一个预设，Key 决定计费） |
| 智谱 GLM（国内） | `GLM › China` | `GLM › China`（同一个预设，Key 决定计费） |
| Z.AI（GLM 国际） | `GLM › International` | `GLM › International` |
| 通义千问（国内） | `Alibaba Cloud › Pay-as-you-go (China)` | `Coding Plan` 或 `Token Plan`，**按你买的那种选** |
| 通义千问（国际） | `Alibaba Cloud › Pay-as-you-go (International)` | 见[通义千问](#通义千问阿里云百炼) |
| 小米 MiMo | `MiMo › Pay-as-you-go` | `MiMo › Token Plan (China cluster)` |
| Claude | 内置的 `Claude` 配置 | 内置的 `Claude` 配置 |

## DeepSeek

**适合**：第一次用、想先花十几块钱试试。只有按量付费，没有月费。

1. 打开 [DeepSeek 开放平台](https://platform.deepseek.com/)，注册并登录。
   页面如果要求实名认证，按提示完成。
2. 到 [充值页](https://platform.deepseek.com/top_up) 充值。
3. 到 [API Keys 页](https://platform.deepseek.com/api_keys) 创建一个 Key，
   马上复制保存。
4. Vibe Seller 里选预设 **DeepSeek**，粘贴 Key，创建。

<!-- SHOT: deepseek_apikeys_zh.png — DeepSeek 开放平台 API Keys 页，「创建 API key」按钮位置（已有 Key 打码） -->

<!-- SHOT: deepseek_topup_zh.png — DeepSeek 开放平台充值页 -->

**常见坑**：余额用完会报 `402 余额不足`，去充值页补上就行。
官方说明：[接入 Claude Code](https://api-docs.deepseek.com/zh-cn/quick_start/agent_integrations/claude_code)、
[错误码](https://api-docs.deepseek.com/zh-cn/quick_start/error_codes)。

## Kimi

**适合**：已经是 Kimi 会员的用户。Vibe Seller 的 Kimi 预设走的是 **Kimi Code**
接口，需要会员里包含 Kimi Code 权益。

1. 在 [Kimi 会员页](https://www.kimi.com/membership/pricing) 开通会员。
   **不是每个档位都含 Kimi Code 权益**，购买前看清页面说明。
2. 打开 [Kimi Code 控制台](https://www.kimi.com/code/console)，点
   **新建 API Key**，输入一个名字，确认。
3. **立刻复制**——关掉弹窗后就看不到完整的 Key 了。
4. Vibe Seller 里选预设 **Kimi**，粘贴 Key，创建。

<!-- SHOT: kimi_code_console_zh.png — Kimi Code 控制台，「新建 API Key」按钮位置 -->

**常见坑**：官方文档只说明了用 **Kimi Code 控制台** 生成的 Key；Kimi 开放平台
（按量付费那个）的 Key 能不能用在这里，官方没有说明，请用 Kimi Code 控制台的 Key。
官方说明：[在 Claude Code 中使用](https://www.kimi.com/code/docs/third-party-tools/claude-code.html)。

## MiniMax

**适合**：按量和套餐都想要的人。MiniMax 的套餐叫 **M Plan**（老用户可能还记得
它以前叫 Token Plan）。两种 Key 用的是**同一个 Vibe Seller 预设**，用哪种 Key
就扣哪边的钱。

国内账号在 [MiniMax 开放平台](https://platform.minimax.cn/) 注册登录，然后二选一：

- **按量付费**：在 [账户管理 > 余额](https://platform.minimax.cn/user-center/payment/balance)
  充值，再到 [接口密钥 > 创建新的 API Key](https://platform.minimax.cn/user-center/basic-information/interface-key)
  拿 Key。
- **M Plan 套餐**：在 [套餐详情](https://platform.minimax.cn/console/plan) 选档位
  付款，付完在同一页查看你的 **订阅 Key**。

然后在 Vibe Seller 里选预设 **MiniMax**，粘贴 Key，创建。

<!-- SHOT: minimax_interface_key_zh.png — MiniMax 开放平台「接口密钥」页，「创建新的 API Key」按钮位置 -->

<!-- SHOT: minimax_plan_key_zh.png — MiniMax「套餐详情」页里订阅 Key 的位置（Key 打码） -->

**国际账号**：在 [platform.minimax.io](https://platform.minimax.io/) 注册，菜单是
`API Keys > Create new secret key`（按量）和 `Billing > M Plan`（套餐）。国际账号的
Key 只能连国际地址：选完 MiniMax 预设后，展开 **高级环境变量**，把
`ANTHROPIC_BASE_URL` 改成 `https://api.minimax.io/anthropic` 再创建。

**常见坑**：

- 订阅 Key 只用套餐额度，按量 Key 只扣余额，两者不能混用。
- 官方说明 M Plan 面向个人开发者的交互式使用，**生产环境建议用按量付费**。

官方说明：[前置准备](https://platform.minimax.cn/docs/guides/quickstart-preparation)、
[开通 M Plan](https://platform.minimaxi.com/docs/m-plan/quickstart)、
[M Plan 常见问题](https://platform.minimaxi.com/docs/m-plan/faq)、
[Claude Code 接入](https://platform.minimax.cn/docs/token-plan/claude-code)。

## 智谱 GLM

**适合**：国内用户选 **智谱开放平台**（预设 `GLM › China`），海外用户选
**Z.AI**（预设 `GLM › International`）。两边都有按量付费和 GLM Coding Plan 套餐。

> **2026 年 10 月 16 日起**，智谱 / Z.AI 把「标准 API Key」（按量 / 资源包）和
> 「Coding Plan 套餐 Key」分开管理了：**新建的 Key 是哪种，就扣哪边的钱**。
> 已订阅套餐却拿标准 Key 来用，不会用套餐额度，而是扣账户余额。
> 之前建的老 Key 不用换，系统已自动归类。

### 国内：智谱开放平台

1. 打开 [智谱开放平台](https://open.bigmodel.cn)，右上角「注册/登录」。调 API
   不强制实名，但官方建议做 [实名认证](https://open.bigmodel.cn/usercenter/auth)。
2. 二选一：
    - **按量付费**：充值后，在 [标准 API Key 页面](https://bigmodel.cn/usercenter/proj-mgmt/apikeys)
      创建 Key。
    - **GLM Coding Plan**：订阅套餐后，在 **个人编程套餐 → 套餐概览** 新建 Key
      （团队版成员在 **团队编程套餐 → 我的套餐**）。
3. Vibe Seller 里点 **GLM ›**，再选 **China**，粘贴 Key，创建。

<!-- SHOT: glm_bigmodel_apikeys_zh.png — 智谱开放平台「标准 API Key」页面，创建按钮位置 -->

<!-- SHOT: glm_bigmodel_codingplan_key_zh.png — 智谱「个人编程套餐 → 套餐概览」页，新建 API Key 的位置 -->

### 海外：Z.AI

1. 打开 [Z.AI 开放平台](https://z.ai/model-api) 注册登录。
2. 二选一：
    - **按量付费**：在 [API Keys](https://z.ai/manage-apikey/apikey-list) 页的
      **Standard API Key** 下创建。
    - **GLM Coding Plan**：在 [订阅页](https://z.ai/subscribe) 买套餐后，在
      [API Keys](https://z.ai/manage-apikey/apikey-list) 页的 **Personal Coding Plan**
      下创建（团队版在 [Team Coding Plan → My Plan](https://z.ai/manage-apikey/coding-plan/team/my-plan)）。
3. Vibe Seller 里点 **GLM ›**，再选 **International**，粘贴 Key，创建。

<!-- SHOT: glm_zai_apikeys_zh.png — Z.AI API Keys 页，Standard API Key 与 Personal Coding Plan 两个入口 -->

**常见坑**：官方注明 GLM Coding Plan **仅限在官方支持的工具中使用**（Claude Code
在支持列表里），购买前请读条款。
官方说明：[API Key 分类管理说明](https://docs.bigmodel.cn/cn/coding-plan/notice/api-key-management)、
[Coding Plan 快速开始](https://docs.bigmodel.cn/cn/coding-plan/quick-start)、
[Z.AI API Key Management Guide](https://docs.z.ai/devpack/notice/api-key-management)、
[Z.AI Quick Start](https://docs.z.ai/devpack/quick-start)。

## 通义千问（阿里云百炼）

**适合**：已经有阿里云账号的用户。百炼有**四种**接入方式，**Key 和连接地址必须
配套**，所以 Vibe Seller 里点 **Alibaba Cloud ›** 后有四个子选项：

| 你买的是 | Key 的样子 | 在哪拿 | Vibe Seller 选 |
|---|---|---|---|
| 按量付费（国内 / 北京） | `sk-` 开头 | [百炼控制台 → API Key](https://bailian.console.aliyun.com/cn-beijing/model/settings/api-key) | `Pay-as-you-go (China)` |
| 按量付费（国际 / 新加坡） | `sk-` 开头 | 国际站 [Model Studio](https://modelstudio.console.alibabacloud.com/) 的 API Key 页 | `Pay-as-you-go (International)` |
| Coding Plan | `sk-sp-` 开头 | [Coding Plan 页](https://bailian.console.aliyun.com/cn-beijing/subscription/coding-plan) | `Coding Plan` |
| Token Plan（个人版 / 团队版） | 见控制台 | [Token Plan 个人版页](https://bailian.console.aliyun.com/cn-beijing/subscription/token-plan/personal) 点「创建或查看 API Key」；团队版由管理员在 [成员管理](https://bailian.console.aliyun.com/cn-beijing/subscription/uac-admin/organization/members/list)「分配坐席并获取 API Key」 | `Token Plan` |

**按量付费 Key 的创建步骤**（国内）：

1. 打开百炼控制台的 API Key 页，**先在右上角选对地域**（Key 不能跨地域用）。
2. 点 **创建 API Key**，「归属账号」「归属业务空间」保持默认，「权限」选
   **全部**，点 **确定**。
3. **立刻复制或下载**——新版 Key 只在创建成功时完整显示一次。
4. Vibe Seller 里点 **Alibaba Cloud ›**，选 **Pay-as-you-go (China)**，粘贴，创建。

<!-- SHOT: qwen_bailian_apikey_zh.png — 百炼控制台 API Key 页：右上角地域选择 +「创建 API Key」按钮 -->

<!-- SHOT: qwen_codingplan_key_zh.png — 百炼 Coding Plan 页里专属 Key（sk-sp-）的位置（Key 打码） -->

<!-- SHOT: qwen_tokenplan_key_zh.png — 百炼 Token Plan 个人版页「创建或查看 API Key」按钮 -->

**常见坑**：

- **报 `401 invalid_api_key` 基本都是 Key 和预设没配对**，比如拿 Coding Plan 的
  `sk-sp-` Key 选了按量的预设。换成对应的子选项重新创建即可。
- **国际按量**预设的 Base URL 里有个占位符 `{WorkspaceId}`，不替换成你自己的
  Workspace ID 就没法保存（界面会标红提示）。Workspace ID 在哪看请按
  [官方地域说明](https://www.alibabacloud.com/help/en/model-studio/regions) 查找。
- 预设里 Coding Plan / Token Plan 用的是**国内**地址。买的是国际站套餐的话，
  展开 **高级环境变量**，把 `ANTHROPIC_BASE_URL` 改成官方文档给的国际地址
  （Coding Plan：`https://coding-intl.dashscope.aliyuncs.com/apps/anthropic`；
  Token Plan：`https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic`）。
- 官方条款写明 **Coding Plan 仅限在编程工具中交互使用**，禁止自动化脚本、
  非交互式批量调用，违规可能被封 Key。Vibe Seller 的定时任务属于无人值守调用，
  长期用建议选按量付费，或先读清 Token Plan 的条款。

官方说明：[获取 API Key](https://help.aliyun.com/zh/model-studio/get-api-key)、
[Claude Code 接入（含四种方式对照）](https://help.aliyun.com/zh/model-studio/claude-code)、
[Coding Plan](https://help.aliyun.com/zh/model-studio/coding-plan)、
[国际站 Claude Code 接入](https://www.alibabacloud.com/help/en/model-studio/claude-code)。

## 小米 MiMo

**适合**：有小米账号的用户。按量付费和 Token Plan 两种都有，**Key 不通用**。

1. 用小米账号登录 [MiMo 开放平台](https://platform.xiaomimimo.com/)（没有账号可以
   在登录页注册）。**国内用户充值或买套餐前必须完成实名认证**（个人或企业都行）。
2. 二选一：
    - **按量付费**：在 [账户余额](https://platform.xiaomimimo.com/#/console/balance)
      充值（支持微信、支付宝、小米支付），再到
      [API Keys](https://platform.xiaomimimo.com/#/console/api-keys) 创建 Key，
      格式是 `sk-` 开头。Vibe Seller 选 **MiMo › Pay-as-you-go**。
    - **Token Plan**：在 [Token Plan 页](https://platform.xiaomimimo.com/#/token-plan)
      选套餐订阅，然后到 [Token Plan 管理页](https://platform.xiaomimimo.com/#/console/plan-manage)
      拿专属 Key（个人版 `tp-` 开头，团队版 `ttp-` 开头；团队版要先分配席位）。
      Vibe Seller 选 **MiMo › Token Plan (China cluster)**。
3. 粘贴 Key，创建。

<!-- SHOT: mimo_apikeys_zh.png — MiMo 开放平台 API Keys 页，创建按钮位置 -->

<!-- SHOT: mimo_tokenplan_manage_zh.png — MiMo Token Plan 管理页里专属 Key 和 Base URL 的位置（Key 打码） -->

**常见坑**：

- `sk-` 的按量 Key 和 `tp-`/`ttp-` 的套餐 Key 不能混用，预设要选对。
- Token Plan 有国内、新加坡、欧洲三个集群。预设默认是国内集群；如果 Token Plan
  页面显示的 Base URL 不是 `token-plan-cn` 开头，展开 **高级环境变量**，把
  `ANTHROPIC_BASE_URL` 改成页面上显示的那个。海外账号的按量 Key 也一样，以控制台
  显示的地址为准。
- Token Plan 余额不能抵扣，需要单独购买；北京时间 0–8 点调用按 0.8 倍扣额度。

官方说明：[首次调用 API](https://mimo.mi.com/docs/quick-start/summary/first-api-call)、
[Token Plan 快速接入](https://mimo.mi.com/docs/tokenplan/Token%20Plan/quick-access)、
[Claude Code 配置](https://mimo.mi.com/docs/zh-CN/tokenplan/integration/claudecode)。

## Claude

Claude 能力上限最高，有两种用法，都**不需要在 Vibe Seller 里粘 Key**。

### 复用这台电脑上已登录的 Claude Code（推荐）

Vibe Seller 底层就是 Claude Code。只要这台电脑上的 Claude Code 已经登录，
Vibe Seller 内置的 **Claude** 配置（「系统默认环境」）就直接用这个登录。

1. 按 [官方安装文档](https://code.claude.com/docs/en/setup) 装好 Claude Code。
2. 在终端运行 `claude`，浏览器会弹出登录页。可以用：
    - **Claude Pro / Max 订阅**：用 claude.ai 账号登录（[订阅页](https://claude.com/pricing)）；
    - **Claude Console 账号**（按 API 用量付费）：登录时选 Console 账号。
3. 终端显示 `Login successful` 就好了。
4. 回到 Vibe Seller：`设置 → AI`，「Claude」那一行如果没有 **★ 默认** 标记，
   点 **设为默认**。

<!-- SHOT: vibeseller_claude_default_zh.png — 设置 → AI 里内置「Claude」配置行带「★ 默认」标记的样子 -->

### Anthropic Console 的 API Key

在 [Claude Console](https://platform.claude.com/) 注册，到
[Billing](https://platform.claude.com/settings/billing) 充值，在
[API keys](https://platform.claude.com/settings/keys) 页创建 Key。最省事的用法仍是
上面第 2 步用 Console 账号登录 Claude Code。

**常见坑**：

- Claude 只在[部分国家和地区](https://platform.claude.com/docs/en/api/supported-regions)
  提供服务，注册前先确认。
- 电脑上装了 cc-switch 之类的工具，或者环境变量里设了 `ANTHROPIC_API_KEY`，
  Claude Code 会优先用它们而不是你的登录。可以在 Claude Code 里输入 `/status`
  看当前用的是哪种登录。

官方说明：[Claude Code 登录方式](https://code.claude.com/docs/en/authentication)、
[Claude API 概览](https://platform.claude.com/docs/en/api/overview)。

## 常见问题

<details markdown="1">
<summary><b>Key 只显示一次，我没复制下来怎么办？</b></summary>

找不回来，也不需要找回。到供应商控制台把那个 Key **删掉**，再**新建一个**，
这次马上复制。

</details>

<details markdown="1">
<summary><b>Key 不小心发给别人 / 贴到群里了怎么办？</b></summary>

立刻到供应商控制台**删除（或禁用）这个 Key**，再新建一个。然后在 Vibe Seller
`设置 → AI` 里，找到用这个 Key 的配置，点 **编辑**，把新 Key 粘进
**API 密钥**，点 **保存**。Key 泄露了别人就能花你的钱，不要犹豫。

</details>

<details markdown="1">
<summary><b>测试连接报 401 / 认证失败</b></summary>

按顺序检查：

1. Key 是否完整复制（前后没有多余空格，没有少一截）。
2. Key 是不是已经被删除或过期。
3. **Key 和预设是否配对**——最常见：套餐 Key 选了按量预设，或者国际站的 Key
   选了国内预设。

</details>

<details markdown="1">
<summary><b>报余额不足（402）/ 配额用完（429）</b></summary>

- **余额不足**：按量付费账户没钱了，去充值页充值。
- **429**：请求太快，或者**套餐额度用完了**。套餐额度要等周期重置，或者升级
  套餐；急用的话可以在 Vibe Seller 里新建一个其他家的配置先顶上。

</details>

<details markdown="1">
<summary><b>套餐 Key 选错了预设，怎么改？</b></summary>

已经创建好的配置，编辑时不能再换预设。直接 **创建配置文件**，重新选**对的**
预设、粘 Key、创建，再把错的那个删掉。

</details>

<details markdown="1">
<summary><b>Key 存在哪里？安全吗？</b></summary>

Key 只存在你自己电脑上的 Vibe Seller 里，加密保存，不会上传到任何地方；
调用模型时由你的电脑直接发给对应的供应商。

</details>
