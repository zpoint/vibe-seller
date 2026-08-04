# Ziniao Browser: URL Block Detection

## Symptom

When you navigate to a URL and then inspect the page, it shows **"Empty
DOM tree"** even after waiting. The page URL and title may look correct,
but the actual content is a Ziniao block page.

## Root Cause

Ziniao (紫鸟) browser has a built-in security extension that intercepts URLs it
considers non-compliant. The extension redirects the page to an internal block
page (`chrome-extension://gpkcfclpmmkjajiipjgeefnpbjnmnnhi/stop.html`) which
shows a message like:

> 您所访问的网页可能涉及不符合相关法律法规和政策的内容，未予显示。

Translation: "The webpage you are accessing may involve content that does not
comply with relevant laws, regulations and policies, and is not displayed."

## Two different extension pages — `stop.html` vs `error.html`

The same extension id serves both, and they mean different things:

| page | meaning | who can fix it |
|---|---|---|
| `…/stop.html` | URL blocked by policy | whitelist the destination in the Ziniao console |
| `…/error.html?…&url=<target>` | the extension could not complete the navigation | **not the agent** — Ziniao-side or network |

`error.html` carries the destination in its own `url=` parameter, which is
the fastest way to see what was actually being fetched. Print that
parameter alone — the full extension URL carries several others, and the
one you want gets lost among them:

```bash
curl -sf --noproxy '*' "http://127.0.0.1:<mux_port>/json/list" \
  | python3 -c "import sys,json,urllib.parse as u; \
      [print(u.parse_qs(u.urlparse(t['url']).query).get('url',['?'])[0]) \
       for t in json.load(sys.stdin) \
       if 'error.html' in (t.get('url') or '')][:1]"
```

**Neither page is a timeout, and both are commonly misread as one.**
Observed live: a store retried the same noon URL twelve times, reporting
"the browser is consistently timing out on navigation", while every tab
held `error.html` for that exact URL. Retrying, resetting the daemon and
rotating the session all do nothing — the navigation is being intercepted,
not delayed.

So when navigation "times out" repeatedly, **list the browser's targets
before touching anything else**. Twelve identical extension pages is a
configuration answer, not a flaky one. If the profile is affected and a
sibling store reaches the same platform normally, it is that profile's
access that differs — report it rather than burning the run on retries.

## Detection: Always Check When DOM Is Empty

When inspecting the page returns "Empty DOM tree" after opening it,
**always run this diagnostic** before retrying or assuming a slow load:
evaluate JavaScript on the page (via browser-use) that reports
`document.title`, `window.location.href`, and the first ~3000 chars of
`document.body.innerText`. See the browser-use skill for the exact
command to run JavaScript on the page.

**Indicators of a Ziniao block:**
- `document.title` contains "紫鸟浏览器"
- `window.location.href` starts with `chrome-extension://`
- `bodyText` contains "不符合相关法律法规" or "申请加白" or "未予显示"

## What to Do When Blocked

### Option 1: Whitelist in Ziniao Console (Recommended)

The block page offers a self-whitelist button (申请加白). Steps:
1. Take a screenshot to see the full block page.
2. Click the "申请加白" button on the block page.
3. Wait for approval (usually instant for known e-commerce sites).
4. Retry navigating to the URL.

Workflow (see the browser-use skill for the exact commands):
1. Run the block-detection JavaScript (title / url / bodyText) on the
   page.
2. If blocked, take a screenshot to see the block page.
3. Inspect the page to find the "申请加白" button.
4. Click the whitelist button.
5. Wait a few seconds for approval, then re-navigate to the Seller
   Central URL (e.g. `https://sellercentral.amazon.<tld>/home`).

### Option 2: Report to User

If self-whitelist is not available for the URL, inform the user:

> Ziniao 浏览器拦截了 URL，需要在紫鸟后台申请加白。
> 被拦截的 URL: {url}
> 拦截原因: 可能不符合相关法律法规

## Do NOT Do These

- **Do NOT re-navigate to the same URL repeatedly** — the block page won't
  change without user action
- **Do NOT assume slow loading** — "Empty DOM tree" after 5+ seconds with a
  `chrome-extension://` URL means a block, not a slow page
- **Do NOT try alternative URLs** — if `sellercentral.amazon.<tld>` is blocked,
  trying `sellercentral.amazon.com.<tld>` or other wrong domains won't help

## Quick Diagnostic Flowchart

```
inspect page → "Empty DOM tree"?
  ├─ No → page loaded normally, proceed
  └─ Yes → run block-detection JS (title, url, bodyText)
       ├─ url starts with chrome-extension:// → ZINIAO BLOCK
       │    ├─ Try 申请加白 button
       │    └─ Ask user to whitelist in Ziniao console
       └─ url is https://... → genuinely slow load, wait and retry
```
