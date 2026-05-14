## Browser Routing Rules

### MANDATORY: Seller center → Ziniao (no exceptions)
Any seller/merchant center dashboard (where this store has a seller
account) MUST use the **Ziniao Browser**. This is non-negotiable because:
- Ziniao has the store's saved credentials and 2FA auto-fill
- Ziniao provides the correct IP environment for the seller account
- Opening seller center in Chrome aux will lack login state and may
  trigger platform security alerts

Use your judgment to identify seller center URLs — they are typically
the platform's dedicated seller/merchant portal (e.g. "seller central",
"seller portal", "merchant dashboard", "vendor central").

### Everything else → Chrome Auxiliary
For all non-seller-center URLs (search engines, logistics, tracking,
carrier portals, documentation, etc.) use the **Chrome Auxiliary Browser**.
It has a persistent profile — cookies and saved passwords persist
across tasks.

Ziniao may block non-seller URLs, so always use Chrome aux for those.

### Custom routing
If the user has configured custom routing rules, check
`stores/<slug>/browser-routing.md` in the workspace and follow those.
Custom rules override the defaults above.
