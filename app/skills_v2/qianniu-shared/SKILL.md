---
name: qianniu-shared
description: "Common Taobao/Tmall 千牛 (Qianniu) seller mechanics: chrome-backend login (QR scan, or slider captcha + SMS, both done by a human), the wrong-account check (the logged-in 店铺名 must match the bound store), closing promo/公告/安全提示 popups after each page load, the 千牛 / 生意参谋 (sycm) / 万相台无界 (one.alimama.com) page map and SSO, the per-store download dir, and the GB18030 CSV caveat. The other qianniu-* skills (qianniu-listing, qianniu-ads) build on it. Use before any browser-use action on a Taobao/Tmall (千牛) store."
---

# Qianniu (Taobao/Tmall) — Shared (auth, navigation, common patterns)

What every 千牛 seller task needs: the workbench login (chrome backend,
human-in-the-loop), the 生意参谋 / 万相台无界 page map + SSO, the per-store
download directory, and the CSV encoding caveat. Operation-specific skills
(`qianniu-listing`, `qianniu-ads`) load this first.

These stores run on the **chrome** browser backend, not Ziniao, so there
is no autofill or auto-captcha: the slider captcha and SMS are human steps
(§ 2).

Drive the browser with the store's `browser-use` wrapper and heredoc
helpers as described in the `browser-harness` skill. `cdp(...)` and
`click_at_xy(...)` are Python helpers in the heredoc scope; inside a
`js("…")` string they are undefined, because that code runs in the page.

If the store browser fails to start with *"Executable doesn't exist at
…/chromium-<v>/…Google Chrome for Testing"*, the host is missing
Playwright's Chromium; tell the user it needs a one-time
`python -m playwright install chromium` in the vibe-seller environment.

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
browser-use <<'PY'
import time
new_tab("https://myseller.taobao.com/")
time.sleep(6)
print("url:", js("return location.href"))          # loginmyseller… = logged out
# (popup-close loop from § 4 here)
PY
```

Then hand off with `AskUserQuestion` ("scan the QR / complete slider+SMS in
the visible window, then continue"), and poll `location.href` until the
workbench loads. **Don't drag the slider programmatically** — Taobao flags
automated drags and burns the account's attempts.

Password login (fallback) fills the `havanalogin.taobao.com` iframe
(`#fm-login-id`, `#fm-login-password`, `button[type=submit]`) via `js(...)`,
but still lands on the slider + SMS human step. Credentials, when used, come
from the store profile (`stores/<slug>/STORE.md`), format `店铺名:子账号名`.

The chrome **profile persists** at `~/.vibe-seller/browser_profiles/<slug>/`,
so login survives restarts — re-login is only needed when the session expires.

### 2.1 Confirm the logged-in account

The chrome profile persists, and a human may have left the window logged into
a different seller account. Data pulled from the wrong account is silently
wrong, and a write lands on someone else's store, so **before any read or
export, confirm the active 店铺名 matches the bound store.** The workbench header renders the sub-account as `店铺名:子账号名`.

```bash
browser-use <<'PY'
print(js(r"""
var t=(document.body.innerText||'').replace(/ /g,' ');
var m=t.match(/([一-龥A-Za-z0-9]{2,40})[:：]([一-龥A-Za-z0-9_]{1,24})/g)||[];
return JSON.stringify(m.slice(0,12));
"""))
PY
```

Compare the extracted `店铺名` (before the `:`/`：`) to the bound store's
店铺名. If it does not match, stop and ask the user with `AskUserQuestion`
which account to use. Keep the detected 店铺名 out of saved knowledge and
reports beyond what the task needs — it is store-identifying data.

## 3. Headed vs headless (so a human can log in / scan)

Window visibility belongs to the backend, not to you: don't pass
`--headed`/`--headless` or close and reopen the browser to change it. The
`chrome` backend is visible when the host has a display. If the human
can't see the window, ask them to turn off "Run browsers in headless mode"
in Settings and restart the store's browser, then retry the login.

## 4. Promo / 公告 / 安全提示 popups — close on sight

千牛, the login pages, 生意参谋, and 万相台 pop ad / 活动 / 公告 / 安全提示
modals on load that eat clicks (an element that won't respond usually has
an invisible overlay on top). After every page load or route change on a
Taobao/千牛 domain, run the detect-and-close loop before interacting, and
re-run it whenever a click doesn't register.

**Close via X / 关闭 / 知道了 / 我知道了 / 稍后 only — never a promo CTA**
(开通 / 立即开通 / 升级 / 立即参加 / 领取 / 报名 / 马上抢 / 一键…): those
subscribe the store to a paid service or join an activity, a write the user
didn't ask for.

```bash
browser-use <<'PY'
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
- **Verify by re-detecting in the DOM** — a background tab can show a stale
  frame in a screenshot. If a synthetic `.click()` won't dismiss it, read
  the X's box and `click_at_xy` its centre (a trusted click). If a
  blocking dialog exposes no close affordance, `js("location.reload()")` and
  re-run.

## 5. Per-store download dir & CSV encoding

Exports (Excel/CSV) the browser downloads land in the per-store dir
`~/.vibe-seller/downloads/<slug>/` (list newest first with `ls -lt`). Copy
what the task delivers into the task CWD; exports later runs will reuse go
to `store-data/<slug>/<area>/<YYYY-MM>/`. Keep captures out of `knowledge/`
and `stores/`, which hold curated facts every task reads.

> **CSV encoding:** Taobao/Tmall CSV exports are **GB18030**, not UTF-8.
> Decode with `encoding='gb18030'` (pandas/`open`) or headers/values render
> as mojibake. Excel (`.xlsx`) exports are fine as-is.

## See also

- `qianniu-listing` — CRUD listings via the Excel bulk round trip.
- `qianniu-ads` — 万相台 report export (analysis) + plan CRUD (review-first).
- `browser-harness` — the browser-use 0.13 heredoc CLI + helper reference.
