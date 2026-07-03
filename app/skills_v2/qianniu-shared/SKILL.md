---
name: qianniu-shared
description: "Common Taobao/Tmall 千牛 (Qianniu) seller mechanics — chrome-backend login (QR scan or slider-captcha + SMS is a HUMAN step; NO auto-captcha), wrong-account guard (verify the logged-in 店铺名 matches the bound store, else ask), promo/公告/安全提示 popup detect-and-close on every page load, the 千牛 / 生意参谋 (sycm) / 万相台无界 (one.alimama.com) page map + SSO, per-store download dir, and GB18030 CSV caveat. Prerequisite: every other qianniu-* skill (qianniu-listing, qianniu-ads, qianniu-reports) expects this loaded for auth + navigation. Load before any browser-use action on a Taobao/Tmall (千牛) store."
---

# Qianniu (Taobao/Tmall) — Shared (auth, navigation, common patterns)

What every 千牛 seller task needs: the workbench login (chrome backend,
human-in-the-loop), the 生意参谋 / 万相台无界 page map + SSO, the per-store
download directory, and the CSV encoding caveat. Operation-specific skills
(`qianniu-listing`, `qianniu-ads`, `qianniu-reports`) load this first.

> These stores run on the **chrome** browser backend (not Ziniao) — check
> `stores.browser_backend`. There is **no auto-captcha** here: the slider
> captcha and SMS are HUMAN steps (§ 2).

> **browser-use 0.13 CLI (heredoc).** Drive the wrapper by piping a Python
> snippet: `~/.vibe-seller/bin/<slug>/browser-use <<'PY' … PY`. Pre-imported
> helpers: `new_tab(url)` (**first navigation**, not `goto`), `page_info()`,
> `js("<javascript>")` (runs JS in the page, returns the value; pierce open
> shadow roots via `.shadowRoot`), `click_at_xy(x, y)`, `capture_screenshot(path)`,
> `wait_for_load()`, `cdp("Domain.method", ...)` (raw CDP). See the
> `browser-harness` skill. **Gotcha:** `cdp(...)` / `click_at_xy(...)` are
> **Python** helpers in the heredoc scope — do NOT call them inside a
> `js("…")` string (that runs in the page, where they're undefined).

> **First-run on a fresh box:** the chrome backend needs Playwright's headed
> Chromium ("Chrome for Testing"). If `browser/start` fails with
> *"Executable doesn't exist at …/chromium-<v>/…Google Chrome for Testing"*,
> install it once: `.venv/bin/python3 -m playwright install chromium`.

## 1. Surfaces & URLs

| Surface | URL | Notes |
|---|---|---|
| 千牛工作台 (workbench home) | `https://myseller.taobao.com/` | redirects to `loginmyseller.taobao.com` when logged out |
| 商品管理 (listings) | `https://myseller.taobao.com/home.htm/SellManage/on_sale` | tabs: 全部 / 出售中 / 仓库中 / 预售 / 已售空 / 违规 |
| 生意参谋 (Business Advisor) | `https://sycm.taobao.com/` | retail big-data; product/traffic/transaction reports |
| 万相台无界 (Alimama, ad platform) | `https://one.alimama.com/index.html` | hash-route SPA `#!/...`; ad plans + 报表 live here |

`生意参谋` and `万相台无界` are **separate apps** but share the 千牛 login
session (**SSO**). Once 千牛 is logged in, opening `sycm.taobao.com` or
`one.alimama.com` does **not** need a second login. If the session is dead
they redirect to the Taobao login — that is a **human step** (§ 2).

## 2. Login (chrome backend — human-in-the-loop)

There is **no auto-captcha**. Two human paths; QR is simplest:

**QR scan (preferred).** Open `https://myseller.taobao.com/`, run the
popup-close loop (§ 4), switch the login card to **扫码登录** (click the QR
toggle if it opens on 密码登录), then ask the human to scan with the
手机淘宝/千牛 app and wait until the workbench home renders.

```bash
~/.vibe-seller/bin/<slug>/browser-use <<'PY'
import time
new_tab("https://myseller.taobao.com/")
time.sleep(6)
print("url:", js("return location.href"))          # loginmyseller… = logged out
# (popup-close loop from § 4 here)
capture_screenshot("/tmp/qn_login.png")             # show the operator the QR
PY
```

Then hand off with `AskUserQuestion` ("scan the QR / complete slider+SMS in
the visible window, then continue"). **Never drag the slider
programmatically** — Taobao flags automated drags and burns attempts.

Password login (fallback) fills the `havanalogin.taobao.com` iframe
(`#fm-login-id`, `#fm-login-password`, `button[type=submit]`) via `js(...)`,
but still lands on the slider + SMS human step. Credentials, when used, come
from the store profile (`stores/<slug>/STORE.md`), format `店铺名:子账号名`.

The chrome **profile persists** at `~/.vibe-seller/browser_profiles/<slug>/`,
so login survives restarts — re-login is only needed when the session expires.

### 2.1 Wrong-account guard ⚠️ HARD GATE

The chrome profile persists, and a human may have left the window logged into
the **wrong seller account**. Data pulled from the wrong account is silently
wrong. **Before any read/export, confirm the active 店铺名 matches the bound
store.** The workbench header renders the sub-account as `店铺名:子账号名`.

```bash
~/.vibe-seller/bin/<slug>/browser-use <<'PY'
print(js(r"""
var t=(document.body.innerText||'').replace(/ /g,' ');
var m=t.match(/([一-龥A-Za-z0-9]{2,40})[:：]([一-龥A-Za-z0-9_]{1,24})/g)||[];
return JSON.stringify(m.slice(0,12));
"""))
PY
```

Compare the extracted `店铺名` (before the `:`/`：`) to the bound store's
店铺名. **If it does not match, do NOT proceed** — surface via
`AskUserQuestion` (human decision; never auto-continue). Never write the
detected 店铺名 into code, commits, or PRs — it is store-identifying data.

## 3. Headed vs headless (so a human can log in / scan)

**Wrapper-managed — NEVER pass `--headed`/`--headless`, never `close`+reopen
to "switch to headed".** Window visibility is decided by the backend:
`chrome` (mac/Linux Playwright) is visible when a display exists; if it's
invisible, an admin flips `browser_headless=false` and restarts the store
browser (see `debug-store`). Just open the login page and have the human scan.

## 4. Promo / 公告 / 安全提示 popups — close on sight ⚠️

千牛, the login pages, 生意参谋, and 万相台 pop **ad / 活动 / 公告 / 安全提示
modals** on load that **eat clicks** (an element that won't respond usually
has an invisible overlay on top). **Standing rule: after every page load or
route change on a Taobao/千牛 domain, run the detect-and-close loop before
any other interaction**, and re-run whenever a click doesn't register.

**Close via X / 关闭 / 知道了 / 我知道了 / 稍后 only — NEVER click a promo CTA**
(开通 / 立即开通 / 升级 / 立即参加 / 领取 / 报名 / 马上抢 / 一键…): those
subscribe the store to a paid service or join an activity (a **write** — out
of scope unless the user asked).

```bash
~/.vibe-seller/bin/<slug>/browser-use <<'PY'
print(js(r"""
var closed=0;
document.querySelectorAll('.baxia-dialog-close,.next-dialog-close,.next-overlay-wrapper .next-icon-close').forEach(function(e){if(e.offsetParent){e.click();closed++}});
var BAD=/开通|升级|参加|报名|抢|领取|去逛|立即|马上|一键|体验|试用/;
var OK=/^(知道了|我知道了|关闭|稍后再说|稍后|以后再说|不再提示|跳过|取消|×|✕|✖|x|X)$/;
document.querySelectorAll('[class*=dialog] *,[class*=modal] *,[class*=popup] *,[class*=Dialog] *,[class*=Modal] *').forEach(function(e){
  if(!e.offsetParent||e.children.length)return;
  var t=(e.innerText||e.getAttribute('aria-label')||'').trim();
  if(OK.test(t)&&!BAD.test(t)){e.click();closed++}
});
return 'closed '+closed;
"""))
PY
```

- **Loop until clean** (re-run until it returns `closed 0`, cap ~4 rounds) —
  closing one modal can reveal another.
- **Verify by re-detecting, not by screenshot** (a background tab can show a
  stale frame). If a synthetic `.click()` won't dismiss it, dispatch a trusted
  CDP click at the X's center (`cdp('Input.dispatchMouseEvent', ...)`). If a
  blocking dialog exposes no close affordance, `js("location.reload()")` and
  re-run.

## 5. Per-store download dir & CSV encoding

Exports (Excel/CSV) the browser downloads land in the vibe-seller-monitored
per-store dir `~/.vibe-seller/downloads/<slug>/` (list newest first with
`ls -lt`). Save any live capture (exports, screenshots, scraped rows) under
`/tmp/<task>/` — **never** under `~/.vibe-seller/knowledge/` (curated facts
only) or `stores/` (per the capture rule).

> **CSV encoding:** Taobao/Tmall CSV exports are **GB18030**, not UTF-8.
> Decode with `encoding='gb18030'` (pandas/`open`) or headers/values render
> as mojibake. Excel (`.xlsx`) exports are fine as-is.

## 6. Common "always load X first" pattern

Every qianniu-* operation skill lists `requires: [qianniu-shared]` and opens
by loading this for login + the page map + the popup-close rule. Load the
operation skill for the actual task (listing round trip, ads audit, reports).

## See also

- `qianniu-listing` — CRUD listings via the Excel bulk round trip.
- `qianniu-ads` — 万相台 report export (analysis) + plan CRUD (review-first).
- `browser-harness` — the browser-use 0.13 heredoc CLI + helper reference.
