---
description: Cost and model choice — what Vibe Seller costs to run, and which LLM to pick.
---

# Cost & Model Choice

## How much does it cost?

Vibe Seller itself is free and open source. The only thing you pay for is your own LLM API key, billed by the provider directly — no middleman.

Rough scale: one ad-tuning run on DeepSeek, 10 campaigns or fewer, costs about 1 RMB (~0.15 USD) and takes 3–5 minutes. More campaigns and more pages mean more tokens. To spend less, run the routine work on a cheap tier and save the flagship models for the hard jobs — see "Which LLM should I pick?" below.

### Measured reference: what one run of a common task costs

The table below is real usage from two computers running store tasks in October 2026, averaged per task type. Prices are for Qwen.

| Task | Tokens per run | Token Plan | Pay-as-you-go API |
|---|---|---|---|
| Check today's orders and revenue | ~1.2M | ~¥0.03 | ~¥0.3 |
| Check 4 stores' email, push exceptions to WeCom | ~6.3M | ~¥0.2 | ~¥1.6 |
| Download one store's report for last month | ~5.8M | ~¥0.2 | ~¥1 |
| Collect reviews and ratings for all of one store's products (~40–200 products) | ~11M | ~¥0.3 | ~¥2.8 |
| Edit a batch of listings (marketplace by marketplace, verifying each) | ~4.7M | ~¥0.1 | ~¥2.3 |
| Export one store's quarterly tax report and work out the figures | ~7.2M | ~¥0.2 | ~¥4 |
| Review one store's ads and suggest changes (2 marketplaces, ~70 campaigns) | ~60–80M | ~¥2 | ~¥11–14 |

**For reference only.** Real usage changes over time, with model capability and with each provider's pricing, and it grows with the store: more ads means more tokens — with only 10 campaigns, one run is roughly a tenth of the figure above.

The biggest saving is to use an official API instead of having the AI click through the seller center page by page: with Amazon Ads connected through the official API, the same ad task dropped from 147 minutes to 11 — see [Amazon Ads API setup](ads-api.en.md).

## Which LLM should I pick?

Pick one that satisfies both: **a long context window** and **vision (multimodal)** — GLM-5.3-Flash, for instance, is labelled "1M context" *and* **Vision** right in the model dropdown under **Settings → AI**. The agent reads page screenshots to work out what state a page is in, so a text-only model degrades badly on browser automation; don't miss the **Vision** / **Text only** badge in that dropdown.

Then pick the tier by how hard the task is:

| Task type | Tier |
|---|---|
| **Complex** — exploring a new platform (no built-in Skill), ad optimization, inventory strategy, long multi-page flows | The provider's **SOTA flagship** |
| **Simple** — email classification, fixed-flow data extraction, filling forms field by field | A **flash / budget** tier is enough |

On complex tasks the agent reads its Skill, judges page state, calls tools and takes notes all at once; weaker models skip steps, garble tool arguments or forget earlier decisions. One failed complex run costs more tokens and time than getting it right once on a flagship. For simple tasks the choice barely matters, so save the tokens.

Where to get each key and which preset to choose: [Configure AI Models](llm-keys.md).
