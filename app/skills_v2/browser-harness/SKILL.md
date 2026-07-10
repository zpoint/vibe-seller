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
2. **Understand visible state**: `capture_screenshot()` — screenshot first.
3. **Inspect / extract**: `page_info()` for a structured summary; `js("...")`
   for DOM queries when coordinates are the wrong tool.
4. **Interact**: screenshot → read the pixel location → `click_at_xy(x, y)` →
   screenshot again to confirm.
5. **After navigation**: `wait_for_load()`; if the tab is stale/internal,
   `ensure_real_tab()`.

## Helper API

Helpers are pre-imported into the heredoc namespace:

```python
new_tab(url)  # open a new tab and navigate (use for EVERY navigation)
page_info()  # structured summary of the current page
capture_screenshot()  # → path to a PNG (fixed: ~/.vibe-seller/bh-tmp/shot.png,
                      # overwritten each call). Read that path to VIEW it.
click_at_xy(x, y)  # click at pixel coordinates
wait_for_load()  # wait for navigation/network to settle
ensure_real_tab()  # switch off a stale/internal (chrome://) tab
js('<javascript>')  # run JS; returns the SERIALIZABLE result only
cdp('Domain.method', ...)  # raw Chrome DevTools Protocol call
```

- **`capture_screenshot()`** returns a file path; `print()` it, then
  **Read that PNG to see the page** — this is how you understand layout
  and find click targets. If your model can't view images, you're
  half-blind: lean harder on `page_info()` and DOM text, and expect to
  work more carefully.
- **`js()` returns serializable values only.** `js("document.title")` and
  `js("return 1+1")` work; but `js("document.querySelector(...)")`
  returns a useless `{}` (a DOM node can't serialize). To get an
  **element reference** (e.g. to set a file input), use
  `cdp('Runtime.evaluate', {'expression': …, 'returnByValue': False})` →
  `result.objectId` (see "Uploading a file" below).

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

There is **no native upload helper** and a coordinate-click on the
visible "Browse" button opens the OS file picker (which you can't drive),
so DO NOT click it. Set the file **directly on the input element via
CDP** — the one reliable way. Get a Runtime `objectId` for the input,
then `DOM.setFileInputFiles`:

```bash
browser-use <<'PY'
# 1. objectId for the <input type=file>. For a plain input:
sel = "document.querySelector('input[type=file]')"
# For an input inside an OPEN shadow root (e.g. Amazon's kat-file-upload,
# where the light-DOM input count is 0), pierce it:
# sel = "document.querySelector('kat-file-upload').shadowRoot.querySelector('input[type=file]')"
obj = cdp('Runtime.evaluate', {'expression': sel, 'returnByValue': False})
oid = obj['result']['objectId']
# 2. Attach the file. The path MUST be ABSOLUTE; files is a list.
cdp('DOM.setFileInputFiles', {'objectId': oid, 'files': ['/abs/path/to/file.txt']})
print('attached')
PY
```

Then submit via the page's own button (`click_at_xy` the real submit
control, not the browse button) and `wait_for_load()`. If
`Runtime.evaluate` returns no `objectId`, the selector didn't match
(wrong shadow root / not yet rendered) — fix the selector; don't fall
back to clicking Browse or to `file://` navigation (both dead ends).

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

1. **Screenshot first**, then act on what you see — `capture_screenshot()`
   before `click_at_xy`.
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
