---
name: browser-harness
description: "Drives the browser through browser-use 0.13, which has no subcommands (no open/click/state): you pipe Python helper code to `browser-use` in a heredoc. Covers the helper API, the per-store wrapper and its sessions, parallel workers, file uploads, and reading pages without vision. Load it before the first browser-use command in any task."
allowed-tools: Bash(browser-use:*)
---

<!-- Adapted from the upstream 0.13 skill in the browser_use wheel; see
     docs/browser-use-0.13-migration.md before re-syncing. -->

# Browser automation with browser-use (0.13, heredoc interface)

browser-use 0.13 has **no subcommands** — `browser-use open <url>` is gone.
You pipe Python to `browser-use`; the helpers below are pre-imported and the
harness attaches a daemon to the store's browser before your code runs.

```bash
browser-use <<'PY'
new_tab("https://example.com")
wait_for_load()
print(page_info())
PY
```

The wrapper takes the heredoc form only, so every statement goes inside it.
`browser-use --doctor` checks installation and CDP connectivity.

## The wrapper is the only way in

- **Store tasks:** `browser-use` on your PATH is a per-store wrapper that
  injects the store session (`BU_NAME`) and the store's CDP proxy
  (`BU_CDP_WS`). It is what keeps you on the store's own browser, account
  and IP — and off the user's local Chrome. So call plain `browser-use`
  and never set `BU_NAME`, `BU_CDP_URL`, `BU_CDP_WS`, `VIBE_TASK_ID` or
  `--mcp` yourself; the wrapper rejects them, as it rejects local-profile
  and cloud-daemon helpers (`start_remote_daemon`, profile sync).
- **Seller-center work runs in the default session only.** For
  non-seller-center sites on the same store (tracking pages, supplier
  sites) use `--session <slug>-aux`; no other `--session` value is
  accepted. Logging into a seller center from the wrong browser or IP
  trips platform security on the store's account.
- **No-store (orchestrator) tasks:** `browser-use` is the store-less `web`
  wrapper. Use it for neutral public web work (search, tracking,
  research). For a store's seller center or any store/platform login,
  create a store sub-task instead.

## Budget each invocation: the wrapper kills it at 120 s

The wrapper runs each call under a 120-second alarm and treats a timeout as
a wedged browser: it force-reloads the daemon, and on the **second
consecutive** timeout prints `UNRECOVERABLE` and exits 75 — at that point
stop retrying and report the gap. Any other outcome, even a Python error,
resets the count. Because a slow-but-healthy call looks the same as a
wedged browser, keep every call well under the limit:

- **One unit of work per invocation** — one page, one SKU, one export.
  Drive loops from the shell and append results to a file in the task
  CWD so progress survives a timeout.
- **Count your polls.** `range(15)` × `sleep(3)` is already 45 s.
- **Poll long operations across separate invocations** (slow exports)
  rather than sleeping inside one.
- The idle browser session is recycled after about 8 minutes, so between
  invocations wait at most ~7 minutes (30–60 s polls are better).

A timeout leaves the tab mid-operation: re-read the page at the start of the
next call instead of assuming where you left off.

## Core workflow

1. **Navigate with `new_tab(url)`** — for the first page and every later
   one. There is no `page` object (`page.goto` is a `NameError`), and
   `goto_url(url)` moves whichever tab is current, which after a timeout
   may not be the one you think.
2. **Read state from the DOM:** `page_info()` for url/title/size, `js(...)`
   for text, values and element coordinates (`getBoundingClientRect`).
3. **Interact:** click with `click_at_xy(x, y)` at coordinates read in
   step 2; type into inputs with `fill_input(selector, text)`. Re-read the
   DOM afterwards to confirm the change took.
4. **After navigation:** `wait_for_load()`; for single-page apps that
   render after `readyState` is complete, `wait_for_element(selector)`.
   If the tab is stale or internal, `ensure_real_tab()`.

## Screenshots: DOM for data, pictures for confirmation

`capture_screenshot()` saves a PNG (default
`~/.vibe-seller/bh-tmp/<session>/shot.png`, overwritten each call) and
returns its path; call it **inside** the heredoc, print the path, then Read
the PNG. A screenshot is good for confirming what the page looks like —
that a dialog opened, a file was staged, a layout is what you expect — and
for steps that are only visual (a canvas calendar, an OTP/passkey overlay).

**A screenshot is not a data source.** Every value you act on or report —
an ID, a number, a row, whether a column exists, a count — comes from the
DOM via `js(...)`. Models reading screenshots of sparse tables reliably
invent plausible rows and columns; when the picture and the DOM disagree,
the DOM is right.

Without vision, skip screenshots: everything in this skill works from the
DOM. If a step in another skill can only be done by looking and you
cannot view images, ask the user with `AskUserQuestion`.

## Helper API

Pre-imported in the heredoc namespace:

```python
# navigation & tabs
new_tab(url="about:blank")        # open a tab and navigate — use for every navigation
goto_url(url)                     # navigate the CURRENT tab (prefer new_tab)
page_info()                       # {url,title,w,h,sx,sy,pw,ph}; or {dialog:{...}} if a native dialog is open
list_tabs(include_chrome=True)    # page targets
current_tab()                     # {targetId,url,title}
switch_tab(target, activate=False)  # attach to a tab (id or dict) without showing it
activate_tab(target)              # make a tab the visible one
close_tab(target=None)            # close a tab (default: current)
ensure_real_tab()                 # move off a chrome:// / stale tab
iframe_target(url_substr)         # targetId of an iframe → js(expr, target_id=...)

# waiting
wait(seconds=1.0)
wait_for_load(timeout=15.0)       # document.readyState == 'complete'
wait_for_element(selector, timeout=10.0, visible=False)  # SPA renders
wait_for_network_idle(timeout=10.0, idle_ms=500)         # after submits/XHR

# reading
js(expression, target_id=None)    # run JS; returns the SERIALIZABLE result
capture_screenshot(path=None, full=False, max_dim=None)  # → PNG path (confirmation only)

# input (trusted CDP events)
click_at_xy(x, y, button="left", clicks=1)
fill_input(selector, text, clear_first=True, timeout=0.0)  # React/Vue-controlled inputs
type_text(text)                   # insert text at the focus (no key events)
press_key(key, modifiers=0)       # "Enter", "Tab", "ArrowDown", "a"; mods 1=Alt 2=Ctrl 4=Meta 8=Shift
dispatch_key(selector, key="Enter", event="keypress")  # synthetic key event on an element
scroll(x, y, dy=-300, dx=0)       # mouse-wheel at a point
upload_file(selector, path)       # set a plain <input type=file> (see Uploading)

# low level
cdp(method, session_id=None, **params)  # raw CDP; params are KEYWORDS
drain_events()                    # return and clear buffered CDP events
http_get(url, headers=None, timeout=20.0)  # plain HTTP from the agent, no browser cookies
```

Notes:

- **Inputs:** `fill_input` focuses, clears, types real keys and fires
  `input`/`change`, so framework-controlled fields (most seller centers)
  register the value and their Submit buttons enable. Assigning
  `.value` from `js(...)` or `type_text` bypasses those listeners and
  usually leaves the form thinking the field is empty; use it only on
  plain inputs you have confirmed react to it.
- **`http_get` does not use the store's browser** — no login cookies, and
  it goes out from the agent's own IP. Use it for public pages and APIs,
  never for seller-center data.
- **`js()` returns serializable values only.** Return numbers, strings, or
  plain objects/arrays — `document.querySelector(...)` comes back `None`
  because a DOM node can't serialize. For an element reference (file
  inputs) use `cdp('Runtime.evaluate', expression=..., returnByValue=False)`
  and take `objectId`.
- **`js()` does not parse JSON**, so don't `JSON.stringify` your result:
  `return [{a:1}]` arrives as a list of dicts, while a stringified result
  arrives as a `str` you'd have to `json.loads`. If you inherit code of
  uncertain shape, normalise once:

  ```python
  import json
  def jsjson(expr):
      d = js(expr)
      return json.loads(d) if isinstance(d, str) else d
  ```

- **`cdp()` takes keyword params.** `cdp(method, session_id=None, **params)`
  — a dict in the second slot binds to `session_id` and the proxy rejects it
  with `-32600 "Message may have string 'sessionId' property"`.
- **Import the stdlib you use** (`time`, `json`, `re`). Only the helpers are
  guaranteed in scope; other names that happen to leak in are not a
  contract. Bare `sleep 3` inside the heredoc is a `SyntaxError`. Prefer
  `wait_for_load()` / `wait_for_element()` over `time.sleep`.
- **Raw-string JS that contains backslashes** (`js(r"""…""")`) — regexes
  like `\d` otherwise trigger `SyntaxWarning` and can corrupt the JS.
- Titles may carry a leading 🐴 (the harness marks the tab it drives);
  strip it before comparing or reporting.

## Locate and click without vision

Read the element's box from the DOM and click its centre. This drives any
page, including shadow-DOM `kat-*` components:

```bash
browser-use <<'PY'
box = js("""
  var el = document.querySelector('button.submit');
  if(!el) return null;
  var r = el.getBoundingClientRect();
  return {text:(el.innerText||el.value||'').slice(0,40),
          x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
""")
print("target:", box)
if box: click_at_xy(box["x"], box["y"])

# or list every clickable and pick by text:
els = js("""
  return [].slice.call(document.querySelectorAll('a,button,input,[role=button],kat-button'))
    .map(function(el){var r=el.getBoundingClientRect();
      return {text:(el.innerText||el.value||'').slice(0,40),
              x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};})
    .filter(function(e){return e.x>0 && e.y>0;});
""")
print(els)
PY
```

- **A plain `<a href>` is a navigation, not a click.** Read the href and
  `new_tab(href)`. An anchor is often a small target inside a larger row,
  so the row's centre misses it and the click silently does nothing.
  Click only controls without an href (buttons, `kat-*`, JS handlers).
- **Shadow roots:** pierce them in the selector,
  `document.querySelector('kat-file-upload').shadowRoot.querySelector('input')`.
- **A JS `.click()` is untrusted** and many `kat-*` controls ignore it; use
  `click_at_xy`.

### A control below the fold that won't scroll into view

Some panels (e.g. Amazon's "Generate Spreadsheet" popover) sit below a
short viewport and ignore `scrollIntoView`, and `click_at_xy` can't hit an
off-screen y. Grow the viewport, click, then restore:

```bash
browser-use <<'PY'
import time
cdp("Emulation.setDeviceMetricsOverride",
    width=1920, height=2400, deviceScaleFactor=1, mobile=False)
time.sleep(1)
box = js("""
  var els=document.querySelectorAll('kat-button,button,[role=button]');
  for(var i=0;i<els.length;i++){var t=(els[i].innerText||els[i].getAttribute('label')||'').trim();
    if(/generate spreadsheet/i.test(t)){var r=els[i].getBoundingClientRect();
      return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};}}
  return null;
""")
if box: click_at_xy(box["x"], box["y"])
time.sleep(3)
cdp("Emulation.clearDeviceMetricsOverride")
PY
```

## Uploading a file to `<input type=file>`

**Stage the file in the store's downloads dir,
`~/.vibe-seller/downloads/<slug>/`, and pass its absolute path.** The
browser process reads the file, not you: macOS Ziniao's Chrome is sandboxed
and can't read `/tmp`, and on the winchrome backend (WSL
agent → native Windows Chrome) pass the Windows form of the path
(`wslpath -w`). A path the browser can't read fails silently —
`files.length` stays 0 and Submit never enables.

You can't drive the OS file picker, so never click "Browse" expecting one.
There are two kinds of file input:

### Method 1 — a plain input (`upload_file`, or set the node)

```bash
browser-use <<'PY'
upload_file("input[type=file]", "/abs/path/to/file.txt")
print('files.length:', js("document.querySelector('input[type=file]').files.length"))
PY
```

`upload_file` takes a plain CSS selector (it can't pierce a shadow root) and
raises if nothing matches. For an input inside an open shadow root, set the
node through an objectId:

```bash
browser-use <<'PY'
sel = "document.querySelector('host').shadowRoot.querySelector('input[type=file]')"
oid = cdp('Runtime.evaluate', expression=sel, returnByValue=False)['result']['objectId']
cdp('DOM.setFileInputFiles', objectId=oid, files=['/abs/path/to/file.txt'])
print('files.length:', js(sel + ".files.length"))
PY
```

Check `files.length` is 1 afterwards. If it stays 0 although the call
succeeded, you set an **inert placeholder** the component never reads
(Amazon `kat-file-upload`'s `input#kat-file-attachment` is one) — use
Method 2.

### Method 2 — a component-managed widget (intercept the file chooser)

Widgets that create the real input on demand (Amazon `kat-file-upload`,
most React uploaders): suppress the OS dialog, make a **trusted** click on
the widget's button, and attach to the `backendNodeId` Chrome reports:

```bash
browser-use <<'PY'
import time
cdp('Page.enable')
cdp('Page.setInterceptFileChooserDialog', enabled=True)
drain_events()
box = js("""var b=document.querySelector('kat-file-upload').shadowRoot.querySelector('#select-file');
            var r=b.getBoundingClientRect();
            return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};""")
click_at_xy(box['x'], box['y'])        # trusted; a JS .click() opens no chooser
bnid = None
for _ in range(8):
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

The widget then stages the file (Amazon shows the filename and "File Type …
(Automatically detected)"). Confirm by the Submit button becoming enabled,
or visually with a screenshot. The shadow root's `textContent` is not a
reliable signal — it carries hidden error strings ("File upload was
unsuccessful") even on success. Then `click_at_xy` the page's Submit and
`wait_for_load()`.

## Parallel subagents: `--worker N`

Subagents inherit your environment, so a subagent's `browser-use` lands on
**your** daemon and **your** tab.

- If you start a subagent and wait for it, change nothing — it sees the
  page you left and the calls take turns.
- If drivers run **concurrently** (subagents with each other or with you),
  give each its own slot, or they interleave on one tab and return each
  other's pages:

```bash
browser-use --worker 2 <<'PY'
new_tab("https://example.com/report")
print(page_info())
PY
```

A slot is its own daemon and tab on the **same logged-in browser** — same
cookies, same downloads dir, no second login.

- The parent assigns slot numbers (1, 2, 3) because only it knows how many
  subagents it is launching; don't reuse a number while its subagent runs.
- `--worker` and `--session` are mutually exclusive; slots live on the main
  store browser, not on `-aux`. Out-of-range or non-numeric slots are
  rejected.
- Downloads are shared across slots (`~/.vibe-seller/downloads/<slug>/`),
  so have workers report the filenames they downloaded rather than assume
  the newest file is theirs.

## Troubleshooting

- **Daemon can't connect:** `browser-use --doctor`.
- **Element not where expected:** `wait_for_load()` / `wait_for_element()`
  and re-read its box; scroll with `scroll(x, y, dy=600)` or
  `js("window.scrollBy(0, 600)")`.
- **`page_info()` returns `{dialog: …}`:** a native alert/confirm is open
  and the page's JS is frozen until it is handled.
- **Stale or internal tab:** `ensure_real_tab()`.
