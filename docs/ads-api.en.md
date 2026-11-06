---
description: Move Amazon ad work from clicking the seller console in a browser to the Amazon Ads API — measured comparison, step-by-step binding, verification, unbinding, troubleshooting.
---

# Amazon Ads API setup

Once bound, the agent's Amazon ad work stops clicking through the seller console in a browser and goes **straight to the Ads API** — steadier for tuning and bulk operations. **Amazon stores only, for now. Each store authorizes separately.**

## What binding buys you

|  | Browser path (unbound) | Ads API (bound) |
|---|---|---|
| How work runs | The agent clicks through the ad console in the store's browser | The agent calls the API — no browser |
| Stability | Depends on pages; redesigns and slow loads break it | Plain API calls; bulk operations stay steady |
| History | Only what the page shows right now | Historical bids and states, negatives, before/after of every change (no Amazon report carries these) |
| Change log | None | The service records every write and its outcome |

In our test the same ad task went from **147 minutes to 11**, with far fewer errors and far less need for a person to step in.

You never pick the path per task — the routing follows the **store's binding state**: authorized stores go over the API, unbound ones stay on the browser.

## Measured comparison

One store (acme, AE marketplace), one report-only ad-optimization task, one model (MiniMax-M3.1-Flash) — run once over the Ads API and once through the browser:

| Metric | Ads API path | Browser path | Ratio |
|---|---|---|---|
| Total time (task created → done) | **11.1 min** | 147 min | 13× |
| Net execution (excl. user-wait & external pause) | **11.0 min** | 90.5 min | 8.2× |
| Assistant turns | **130** | 710 | 5.5× |
| Tool calls (of which browser / ads API) | 69 (0 / **21**) | 361 (**123** browser commands / 0) | 5.2× |
| Tokens (in / out / cache-read) | 0.68M / 0.19M / 15.4M | 2.34M / 0.99M / **151.6M** | cache-read 9.8× |
| Estimated model cost | **≈ ¥3 CNY** | ≈ ¥21 CNY | 7× |
| Campaign coverage | every campaign (incl. zero-spend/paused) | same complete list, all drilled | **equal** |
| Report output | one 24 KB report | 103 KB report + PDF (full per-target / per-term tables) | richer |
| Interruptions | none | 1 operator question (breakeven-ACOS) + 1 model-quota pause (36 min) | |

| Where the time goes | Ads API path | Browser path |
|---|---|---|
| LLM thinking & generation | ■■■■■■■■■■■■■■■■■■ **96.3%** (10.6 min) | ■■■■■■■■■■■■■ **68.6%** (62.1 min) |
| Tool execution | ▪ 2.3% (ads API calls 0.23 min) | ▪ **31.4%** (page opens/clicks 14.5% + page waits 16.5%) |
| Remainder | 1.4% | <0.1% |

The browser path's tool time is almost entirely opening and waiting for pages; the API path's 21 calls took 13.8 seconds in total.

## Prerequisites and binding (4 steps)

### Prerequisites

- A vibe-seller **admin** account (authorize / unbind are admin-only).
- Network reachability to the ads service at `https://listwizard.cloud/ads-api`.
- An Amazon store (only Amazon is supported so far).
- **That store's Amazon Seller Central login** — the consent has to be completed inside the store's own browser environment (the Ziniao window, or whatever browser the store uses).
- **Nothing to type or configure**: no API key, client id or secret. The first time the Integrations page opens, this machine registers itself with the ads service and keeps the returned key encrypted locally; the whole Amazon consent round trip happens on the service's own domain.

### 1. Open the panel

`Settings → Integrations` → the **Vibe Seller Ads API** card. The table lists every store, one row each, with its authorization status.

### 2. Click Authorize, copy the link

Click **Authorize** next to the store:

![Click Authorize next to a store](images/ads_panel_en.png)

The link section opens and the link is **copied to the clipboard automatically**:

![The authorization link](images/ads_link_en.png)

The link is short-lived — the page shows how many minutes are left. Expired: click Authorize / Re-authorize for a fresh one.

### 3. Complete the Amazon consent in the store's browser

Open the copied link **in Ziniao (or the browser this store uses)**, sign in as **this store's** Amazon seller, and approve.

> ⚠ Check the seller account you are signed in to is this store's — otherwise the wrong account gets bound to it. The consent binds whichever account is signed in there, and a wrong one looks exactly like a success.

That step is Amazon's own consent page (on the service's domain), so it isn't screenshot here: sign in as the store's seller, approve, and wait for the success page.

### 4. Come back and click Done authorizing

![Done authorizing](images/ads_done_en.png)

A green "✓ acme authorized: US · AE" means it worked — the store row now lists the authorized marketplaces. If you get "No authorization for this store yet", the Amazon consent isn't finished: complete it in the store's browser, wait for the success page, and click again.

![Demo: bind the Ads API](images/ads_bind_en.gif)

### How to verify

- The green ✓ above is the confirmation. Ad tasks created on the store afterwards automatically switch to the `amazon-ads-api` skill over the API (the task prompt says which path it is on).
- **A freshly authorized store needs about 30 minutes of history sync** — history questions answer "syncing" until it lands; live data is unaffected.
- Every answer carries `as_of`, `window` and `data_through`, so you can check what the numbers cover.

## Unbinding

Click **Revoke** on the store's row; an inline confirm section spells out the consequences:

![Revoke authorization](images/ads_unbind_en.png)

- After revoking, the ads service stops working on this store's ads for this machine and agents **fall back to the browser**. You can authorize again at any time.
- "Also delete this store's ad history" is **off by default** — history is kept so a later re-authorization carries on where it stopped. Check it to delete the history too (it is kept for other machines if another vibe-seller has authorized the same Amazon ads account).
- Once no store remains authorized, the `amazon-ads-api` skill is removed from the workspace.

## Troubleshooting

**The panel opens with a red "Could not reach the ads service …"**
This machine cannot reach the ads service (firewall / proxy / the service is briefly down). Reopen the page once connectivity is back — every open re-runs the sync and registration.

**"Done authorizing" says "No authorization for this store yet"**
The Amazon consent hasn't finished, or you clicked before the success page. Finish the consent in the store's browser, then click again.

**A task says "Store … is not authorized with the ads service"**
The store isn't bound, or its authorization died. Click Authorize / Re-authorize in Settings → Integrations.

**502, and the message says the store has to be re-authorized**
The grant the service held is dead (password changed, or the authorization was revoked on Amazon's side). A **person** re-authorizes in Settings — the agent cannot do it for you.

**503**
Amazon is throttling. Wait and try again later rather than burning the quota on retries.

**A freshly authorized store answers "syncing" (202)**
The first history pull takes ~30 minutes; ask again later. Live endpoints are unaffected.

**One store advertising on several marketplaces**
Name the marketplace (e.g. `US` / `AE`) — the service refuses rather than guesses, so marketplace A's numbers never get read as marketplace B's.

**The link won't open / has expired**
Link lifetime is whatever the page shows (not long), and the link must be opened **inside the store's own browser environment** (the Ziniao window, or the store's browser) — not your everyday browser, or you may bind the wrong seller account. Expired links: click Authorize again.

**Security**
Vibe Seller never stores your Amazon password. The OAuth consent happens on the ads service's own domain; this machine keeps one encrypted installation key, which no HTTP endpoint ever returns.
