---
name: browser-harness
description: "MUST load BEFORE running any browser-use command. This is the only bridge between the agent and the browser — browser-use 0.13 has NO subcommands (open/click/state are gone); you drive the browser by piping Python helper code via a heredoc. Contains the helper API, wrapper rules, session management, and store-task restrictions. Without this skill, browser commands will fail."
allowed-tools: Bash(browser-use:*)
---

<!-- VIBE-SELLER CUSTOMIZATIONS: adapted from the upstream 0.13 skill
     (browser_use/skills/browser-use/SKILL.md in the wheel). If re-syncing
     from upstream, re-apply: (1) the Store/No-store task banners, (2) the
     wrapper env-injection contract (BU_NAME/BU_CDP_WS auto-injected, agent
     overrides blocked), (3) removal of cloud/remote-daemon and local-profile
     sections we don't use. See docs/browser-use-0.13-migration.md. -->

> **browser-use 0.13 changed everything.** There are **no subcommands**. You
> no longer run `browser-use open <url>`. Instead you pipe Python helper code
> to `browser-use` via a heredoc; helpers are pre-imported and a background
> daemon is attached automatically.

> **Store Tasks:** `browser-use` is a per-store wrapper script that
> auto-injects `BU_NAME` (the store session) and `BU_CDP_WS` (the store's CDP
> proxy). You **cannot** set `BU_NAME`, `BU_CDP_URL`, `BU_CDP_WS`, or `--mcp`
> yourself — the wrapper blocks them. Use the default session for the store's
> seller center, or `--session <slug>-aux` for non-seller-center sites (the
> wrapper maps this to the aux session; no other `--session` value is
> allowed).
>
> **No-store (orchestrator) Tasks:** `browser-use` is the store-less `web`
> wrapper (`bin/_web`). Use it only for neutral public web work (search,
> tracking/logistics, research) — NEVER for a store's seller center or to log
> into store/platform accounts (create a store sub-task for those).

# Browser Automation with browser-use (0.13, heredoc interface)

Drive the browser by piping Python to `browser-use`. Helpers are
pre-imported; the harness calls `ensure_daemon()` before running your code, so
the browser attaches automatically to the store's CDP endpoint.

```bash
browser-use <<'PY'
new_tab("https://example.com")   # first navigation is new_tab(), NOT goto
wait_for_load()
print(page_info())
PY
```

The wrapper takes the heredoc form **only** — there is no `-c` flag
(passing one just prints usage). Put every statement inside the heredoc.

## Prerequisites

```bash
browser-use --doctor    # verify installation / CDP connectivity
```

## Core Workflow

1. **Navigate**: `new_tab(url)` — for the first page **and every later
   navigation**. There is **no `page` object** in the heredoc scope, so
   `page.goto(url)` raises `NameError`; use `new_tab(url)` (or click a link)
   to move around.
2. **Understand state — via the DOM (PREFERRED, no vision needed):**
   `page_info()` for page-level facts (url/title/size), and **`js(...)` to
   read the DOM** — text content AND every element's on-screen coordinates
   via `getBoundingClientRect` (see "Locate & click without vision"). This
   is the primary way to drive the browser; a non-vision model completes
   the whole flow this way.
3. **See the layout (OPTIONAL — vision models only):**
   `capture_screenshot()` returns a PNG path (`~/.vibe-seller/bh-tmp/shot.png`,
   overwritten each call); `print()` it and **Read that PNG** to view it.
   Use it only to disambiguate a crowded layout — never *depend* on it. If
   your model can't view images, skip screenshots entirely and use step 2.
4. **Interact**: get an element's centre coords from step 2, then
   `click_at_xy(x, y)`; set input values with `js(...)`. Re-read with
   `page_info()` / `js(...)` after to confirm.
5. **After navigation**: `wait_for_load()`; if the tab is stale/internal,
   `ensure_real_tab()`.

## Helper API

Helpers are pre-imported into the heredoc namespace:

```python
new_tab(url)  # open a new tab and navigate (use for EVERY navigation)
page_info()  # structured summary of the current page
capture_screenshot()  # → PNG path (~/.vibe-seller/bh-tmp/shot.png); Read it to VIEW
click_at_xy(x, y)  # click at pixel coordinates
wait_for_load()  # wait for navigation/network to settle
ensure_real_tab()  # switch off a stale/internal (chrome://) tab
js('<javascript>')  # run JS; returns the SERIALIZABLE result only
cdp('Domain.method', **params)  # raw CDP — params are KEYWORDS, not a dict
                                # e.g. cdp('Page.navigate', url='...')
```

- **`js()` returns serializable values only.** `js("document.title")` and
  `js("return 1+1")` work; but `js("document.querySelector(...)")` returns
  a useless `{}` (a DOM node can't serialize). Return **numbers, strings,
  or plain objects/arrays** — e.g. an element's coordinates (below), not
  the element itself. For an element *reference* (to set a file input) use
  `cdp('Runtime.evaluate', expression=..., returnByValue=False)` → `objectId`
  (note: `cdp()` params are **keyword args**, never a positional dict —
  see "Uploading a file").

## Locate & click an element WITHOUT vision (the preferred path)

You do not need to see the page. Read the DOM and compute click
coordinates from `getBoundingClientRect`, then `click_at_xy`. This drives
any page — buttons, links, shadow-DOM `kat-*` components — with no
screenshot:

```bash
browser-use <<'PY'
# one element by selector → its centre coords + text (None if not found):
box = js("""
  var el = document.querySelector('button.submit');   // any CSS selector
  if(!el) return null;
  var r = el.getBoundingClientRect();
  return {text:(el.innerText||el.value||'').slice(0,40),
          x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
""")
print("target:", box)
if box: click_at_xy(box["x"], box["y"])

# OR enumerate all clickables to find the right one by its text:
els = js("""
  return [].slice.call(document.querySelectorAll('a,button,input,[role=button],kat-button'))
    .map(function(el){var r=el.getBoundingClientRect();
      return {text:(el.innerText||el.value||'').slice(0,40),
              x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};})
    .filter(function(e){return e.x>0 && e.y>0;});
""")
print(els)          # pick the one whose text matches, then click_at_xy(it.x, it.y)
PY
```

### A control BELOW the fold that won't scroll into view

Some pages (e.g. Amazon's "Generate Spreadsheet" popover) put the button
you need **below the viewport**, inside a `kat-popover`/panel that
`scrollTo`/`scrollIntoView` can't bring up — the window is short (e.g.
839px tall) and `click_at_xy` can't hit an off-screen y. Don't fight the
scroll: **grow the layout viewport with CDP so the whole panel fits**,
then click the (now on-screen) coordinate, then restore:

```bash
browser-use <<'PY'
import time
cdp("Emulation.setDeviceMetricsOverride",
    width=1920, height=2400, deviceScaleFactor=1, mobile=False)  # tall viewport
time.sleep(1)
box = js("""
  var els=document.querySelectorAll('kat-button,button,[role=button]');
  for(var i=0;i<els.length;i++){var t=(els[i].innerText||els[i].getAttribute('label')||'').trim();
    if(/generate spreadsheet/i.test(t)){var r=els[i].getBoundingClientRect();
      return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};}}
  return null;
""")
if box: click_at_xy(box["x"], box["y"])   # trusted click, now on-screen
time.sleep(3)
cdp("Emulation.clearDeviceMetricsOverride")   # restore the real viewport
PY
```

(Verified: the override raises `window.innerHeight` to the set value, so a
below-fold button becomes reachable; `clearDeviceMetricsOverride` undoes
it. A plain JS `.click()` still no-ops on `kat-*` — you need the trusted
`click_at_xy`.)

For an element inside an **open shadow root** (Amazon `kat-*`), pierce it
in the selector: `document.querySelector('kat-file-upload').shadowRoot.querySelector('input')`.
Set an input's value with `js("document.querySelector('#q').value='socks'")`
(then click its search icon — some inputs need the click to fire events).

Only the helpers above (plus Python builtins) are in scope — the heredoc
runs as a plain Python script. **`time`, `json`, `re`, etc. are NOT
pre-imported; `import` them yourself.** Bare `sleep 3` is a `SyntaxError`
and `time.sleep(3)` without `import time` is a `NameError`. **Prefer
`wait_for_load()` over sleeping** — reach for `import time; time.sleep(n)`
only when you must wait on something `wait_for_load()` can't observe (e.g.
an async in-page render after a click).

Multiple statements run in one heredoc (this replaces `&&` chaining):

```bash
browser-use <<'PY'
new_tab("https://example.com/login")
wait_for_load()
js("document.querySelector('#email').value = 'user@example.com'")
js("document.querySelector('#password').value = 'secret'")
click_at_xy(640, 480)
wait_for_load()
print(page_info())
PY
```

## Uploading a file to a web `<input type=file>`

> **The file must be in a path the BROWSER PROCESS can read, passed in
> that process's path form — NOT `/tmp`.** `setFileInputFiles` reads the
> file in the browser, not the agent. Put it in the store's downloads dir
> (`~/.vibe-seller/downloads/<slug>/`) — the one location guaranteed
> readable on every backend. Otherwise it **silently no-ops**:
> `files.length` stays 0, Submit never enables, no error. Per backend:
> macOS Ziniao's Chrome is sandboxed and can't read `/tmp` (so a `/tmp`
> file fails — this was the whole "the widget won't accept my file"
> bug, not the widget); native-Windows Chrome has no such sandbox but the
> winchrome case (WSL agent → native Windows Chrome) must pass the
> **Windows-form** path (`C:\…\downloads\<slug>\file`, via `wslpath -w`) —
> a `/mnt/c` or WSL path is unreadable by native Chrome.

There is **no native upload helper**. Never coordinate-click the visible
"Browse"/"Upload file" button expecting to then drive the OS file picker
(you can't). Attach the file via CDP. **`cdp()` takes keyword args, NOT a
params dict** — the signature is `cdp(method, session_id=None, **params)`,
so a dict in the 2nd slot binds to `session_id` and the proxy rejects it
with `-32600 "Message may have string 'sessionId' property"`. Always use
keywords: `cdp('Runtime.evaluate', expression=..., returnByValue=False)`.

There are **two kinds of file input** — pick the matching method:

### Method 1 — a plain `<input type=file>` (set the node directly)

Works for a normal light-DOM input, or a simple open-shadow-DOM input
whose node the component actually reads from. Get an objectId, set files:

```bash
browser-use <<'PY'
sel = "document.querySelector('input[type=file]')"
# shadow-DOM variant: "document.querySelector('host').shadowRoot.querySelector('input[type=file]')"
obj = cdp('Runtime.evaluate', expression=sel, returnByValue=False)  # kwargs!
oid = obj['result']['objectId']
cdp('DOM.setFileInputFiles', objectId=oid, files=['/abs/path/to/file.txt'])  # ABSOLUTE path
print('files.length:', js(sel + ".files.length"))   # MUST verify → 1, not 0
PY
```

**Always verify `files.length` right after.** If it stays **0** while the
call returned success, the node you set is an **inert placeholder** the
component doesn't read from (common with design-system upload widgets like
Amazon's `kat-file-upload`, whose `input#kat-file-attachment` is a decoy).
`setFileInputFiles` silently no-ops on it — objectId, `backendNodeId`, and
`getDocument(pierce=True)` all give 0. Switch to Method 2.

### Method 2 — a component-managed widget (file-chooser interception)

For widgets that open the file picker via a button and create the *real*
input on demand (Amazon `kat-file-upload`, most React uploaders). Suppress
the OS dialog, do a **trusted** click on the widget's button, and take the
`backendNodeId` Chrome hands you in `Page.fileChooserOpened`:

```bash
browser-use <<'PY'
import time
cdp('Page.enable')
cdp('Page.setInterceptFileChooserDialog', enabled=True)   # OS dialog suppressed
drain_events()                                            # clear the buffer
# TRUSTED click on the visible upload button. click_at_xy is a CDP Input
# event (trusted) — a JS .click() is UNtrusted and the browser will NOT
# open a chooser for it (this is why setting the placeholder input fails).
box = js("""var b=document.querySelector('kat-file-upload').shadowRoot.querySelector('#select-file');
            var r=b.getBoundingClientRect();
            return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};""")
click_at_xy(box['x'], box['y'])
bnid = None
for _ in range(8):                     # poll for the event (~4s)
    time.sleep(0.5)
    for e in drain_events():
        if 'fileChooserOpened' in str(e.get('method', '')):
            bnid = e['params']['backendNodeId']
    if bnid: break
cdp('DOM.setFileInputFiles', backendNodeId=bnid, files=['/abs/path/to/file.txt'])
cdp('Page.setInterceptFileChooserDialog', enabled=False)
print('attached to backendNodeId', bnid)
PY
```

The widget then reads and stages the file itself — the visible field shows
the filename and (for Amazon) a green "File Type … (Automatically
detected)". **Don't trust the shadow-root `textContent`** to confirm — it
carries hidden error-state strings ("File upload was unsuccessful") even on
success; confirm with a `capture_screenshot()` + Read, or by the Submit
button becoming enabled. Then `click_at_xy` the page's own **Submit** and
`wait_for_load()`.

## Sessions

- **Default (seller center):** just run `browser-use <<'PY' … PY`. The wrapper
  injects `BU_NAME=<slug>` and the CDP proxy for the store.
- **Aux (non-seller-center browsing on the same store):**
  ```bash
  browser-use --session <slug>-aux <<'PY'
  new_tab("https://tracking.example.com")
  print(page_info())
  PY
  ```
  The wrapper accepts `--session <slug>-aux` **only** and maps it to the aux
  session; any other `--session` value is rejected.

## Blocked in store / web tasks

The wrapper rejects these — do not use them:

| Blocked | Why |
|---------|-----|
| `BU_NAME=…` (your own) | session is auto-injected per store/task |
| `BU_CDP_URL` / `BU_CDP_WS` | CDP endpoint is managed by the store proxy |
| `--mcp` | not allowed inside tasks |
| local-profile / cloud daemon helpers (`start_remote_daemon`, profile sync) | tasks use the store's managed browser only |

## Tips

1. **DOM first, not screenshots.** Locate targets with `js(...)` +
   `getBoundingClientRect` (see "Locate & click without vision") and act on
   the coords — this works with or without vision. A screenshot is an
   optional cross-check for vision models, never a prerequisite.
2. **`new_tab(url)` is how you navigate — every time**, not `goto` (there
   is no `page` object in the heredoc scope).
3. Prefer `page_info()` / `js(...)` over screenshots for text extraction.
4. **CLI aliases**: `bu` and `browser` also invoke the wrapper.
5. **Raw-string your `js()` when it contains backslashes** (regex like
   `/foo\/bar/`, `\d`, `\.`): `js(r"""…""")`. A plain triple-quoted string
   makes Python emit `SyntaxWarning: invalid escape sequence` and can
   corrupt the JS before it reaches the page.

## Troubleshooting

- **Daemon can't connect?** `browser-use --doctor`.
- **Element not where expected?** re-`capture_screenshot()` after
  `wait_for_load()`; scroll with `js("window.scrollBy(0, 600)")`.
- **Stale/internal tab?** `ensure_real_tab()`.
