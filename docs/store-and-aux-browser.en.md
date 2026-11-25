---
description: An anti-detect store's (Ziniao / Yuehulian) tasks have two browsers — the store browser for the seller center, and a Chrome auxiliary browser for the sites the anti-detect browser can't open.
---

# Store Browser & Auxiliary Browser

This is Vibe Seller's browser design: an anti-detect store's (Ziniao /
Yuehulian) tasks have two browsers, each with its own job.

| | Store browser (main — Ziniao / Yuehulian) | Chrome auxiliary browser |
|---|---|---|
| Used for | The seller center: listings, inventory, reports, ads | Everything outside the seller center: carrier tracking, overseas sites, search engines, supplier sites, public product pages |
| Strength | Fingerprint and IP isolation, holds the store's login — the safest way into the seller center | No restrictions; opens any site |
| Limits | Only some sites open; many overseas and logistics sites are blocked | Not signed in to the seller account, and **never** used for seller-center work |

The anti-detect browser protects the store account but blocks a lot; Chrome
covers exactly the sites it can't open, so the two complement each other.
For example, a task that "checks the delivery status of recent orders"
reads the orders from the seller center in the store browser and looks up
the tracking on the carrier's site in Chrome, then puts both into one
report.

All of this is automatic: the auxiliary browser starts the first time it's
needed, there's one per store (never shared between stores), and cookies
for public sites survive across tasks. The AI decides which URL goes to
which browser; you don't choose. To pin certain URLs to one browser, write
rules in `browser-routing.md` in the store's folder — they take precedence
over the defaults.

A Chrome store is plain Chrome with none of these limits, so it uses a
single browser.

Which browsers are supported and how to choose: [Browsers Overview](browsers.en.md).
