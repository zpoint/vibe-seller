---
description: About Vibe Seller — how it differs from Amazon's SP-API, and how the two work together.
---

# About Vibe Seller

## How is it different from Amazon's SP-API?

The SP-API is Amazon's official API for developers and works for Amazon only: you register as a developer, obtain authorization, and write a program that exchanges data in Amazon's formats. It can do only what the API exposes. Advertising is not part of it — that is the separate Amazon Ads API, applied for on its own — and back-office features such as Account Health or Seller Support cases have no API either.

Vibe Seller has the AI work the store's browser directly. Anything the back-office pages allow can be done, on any platform, with no API access needed.

The two are not exclusive. Once a store is bound to the [Amazon Ads API](ads-api.en.md), its ad work goes through the API; unbound stores, and anything without an API, still go through the browser. The path is chosen automatically from each store's authorization status.
