## Browser Routing Rules

Every store has exactly TWO browsers. Route by what the page needs:

### Seller / merchant center → the MAIN (Ziniao) browser — no exceptions
Any seller/merchant dashboard where this store holds an account MUST
use the main **Ziniao Browser** (the plain `browser-use` wrapper /
per-task session):
- It holds the store's saved credentials and auto-fills password / 2FA.
- It provides IP isolation — the correct network environment for the
  seller account; logging in from anywhere else may trigger platform
  security alerts.
- Trade-off: it restricts which sites it can open (many non-seller
  URLs are blocked or gated).

Use your judgment to identify seller-center URLs — the platform's
dedicated seller/merchant portal ("seller central", "merchant
dashboard", "vendor central", …).

### Everything the main browser restricts → the AUX browser
`--session {slug}-aux` is this store's **independent, login-less
Chromium** — started lazily on first use, isolated per store. It exists
precisely for what Ziniao blocks: public product pages, supplier sites
(e.g. 1688), search engines, logistics/carrier portals, documentation.
- It has NO seller login, and must NEVER be used for seller-center
  work (uploads, inventory edits, reports) — anything it shows you
  about the seller account is not your account's state.
- Its profile persists across tasks (cookies for public sites survive).

### Custom routing
If the user has configured custom routing rules, check
`stores/<slug>/browser-routing.md` in the workspace and follow those.
Custom rules override the defaults above.
