---
description: Cost and model choice — what Vibe Seller costs to run, and which LLM to pick.
---

# Cost & Model Choice

## How much does it cost?

Vibe Seller itself is free and open source. The only thing you pay for is your own LLM API key, billed by the provider directly — no middleman.

Rough scale: one ad-tuning run on DeepSeek, 10 campaigns or fewer, costs about 1 RMB (~0.15 USD) and takes 3–5 minutes. More campaigns and more pages mean more tokens. To spend less, run the routine work on a cheap tier and save the flagship models for the hard jobs — see "Which LLM should I pick?" below.

## Which LLM should I pick?

Pick one that satisfies both: **a long context window** and **vision (multimodal)** — GLM-5.3-Flash, for instance, is labelled "1M context" *and* **Vision** right in the model dropdown under **Settings → AI**. The agent reads page screenshots to work out what state a page is in, so a text-only model degrades badly on browser automation; don't miss the **Vision** / **Text only** badge in that dropdown.

Then pick the tier by how hard the task is:

| Task type | Tier |
|---|---|
| **Complex** — exploring a new platform (no built-in Skill), ad optimization, inventory strategy, long multi-page flows | The provider's **SOTA flagship** |
| **Simple** — email classification, fixed-flow data extraction, filling forms field by field | A **flash / budget** tier is enough |

On complex tasks the agent reads its Skill, judges page state, calls tools and takes notes all at once; weaker models skip steps, garble tool arguments or forget earlier decisions. One failed complex run costs more tokens and time than getting it right once on a flagship. For simple tasks the choice barely matters, so save the tokens.

Where to get each key and which preset to choose: [Configure AI Models](llm-keys.md).
