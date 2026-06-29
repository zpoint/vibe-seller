# Noon Ads — Keyword Research Playbook

How to build a buyer-validated keyword list for a noon Manual
campaign. The shortcut everyone makes is "ask the LLM for keywords"
— that produces seller-side language (technical, spec-heavy) that
real buyers don't type. This playbook avoids that trap by going
through noon's actual storefront.

Pair with `ads-creation.md` (how to feed the resulting list into
the create form) and `../SKILL.md § 6` (how to harvest from existing
campaigns' Customer Queries).

## Principle: buyer language ≠ seller language

A category may have:

| Seller-side term (don't bid on these alone) | Buyer-side term (bid on these) |
|---|---|
| `4-quart air fryer 1500W` | `air fryer`, `air fryer family`, `air fryer small` |
| `silicone collapsible food storage container 8-pack` | `food container`, `meal prep`, `lunch box` |
| `multi-stage water filter pitcher 8-cup` | `water filter`, `water pitcher`, `filter jug` |

The seller term carries SKU/spec language; buyers search the
category + a use-case modifier. Always validate via the actual
storefront search before adding a keyword.

## Step 1 — Storefront autocomplete (English)

Noon's storefront search has live autocomplete reflecting *actual
recent searches by buyers in this country*. This is the gold-
standard signal.

```
https://www.noon.com/{country-slug}/   (e.g. egypt-en)
```

Workflow:
1. Open the storefront in the SAME browser session as the seller
   center (avoids cold-start on autocomplete).
2. Click the search box. Type one or two characters of your seed
   word at a time. Wait for autocomplete suggestions.
3. Record every category-relevant suggestion. Repeat with each
   seed word from your category (e.g. for cookware: `kitchen`,
   `cooker`, `pan`, `oven`).
4. Click each recorded suggestion; observe the search results page.
   If it surfaces *your category*, the keyword is valid. If it
   surfaces a different category, it's a false friend — skip.

8–15 validated buyer terms is plenty. Quality over quantity for a
Manual campaign — Manual Targeting allows dozens of keywords but
each one dilutes budget attention.

## Step 2 — Storefront autocomplete (local language)

In any marketplace whose buyers don't search primarily in English,
**a substantial fraction of buyers search in the local language**
on noon — often a higher local-language share than the same buyers'
share on the equivalent Amazon marketplace. Skipping the local
language on noon means missing real volume.

Switch the storefront language via the toggle (top-right; varies
by country page). Autocomplete will now reflect local-language
buyer searches.

Two important conventions on noon:

- **Colloquial > formal/literary forms**. Buyers type how they
  talk, not how they read a newspaper. Common patterns:
  - colloquial spelling that drops formal diacritics/endings buyers
    commonly omit
  - shorter, dialectal forms over textbook plurals
  - English transliterations of common terms are also widely
    searched and appear in autocomplete; worth bidding even though
    they "look" English.
- **Plural / dialectal forms vary by country.** Buyers in different
  countries don't always use the same plural. Re-do autocomplete
  per country.

Validate each local-language candidate the same way as English:
type into search, see if results match your category, record only
if so.

## Step 3 — Peer-listing reading

With a candidate list in hand, do a final filter pass against
peer listings on noon (same category, same buyer intent, *that
are selling well*).

Workflow:
1. Search a top-volume buyer term (from Step 1/2) on the storefront.
2. Open 3–5 of the top results that have visible review counts
   ≥50 (these are *selling*, not just listed).
3. Read each peer's title carefully. Extract:
   - Words appearing in 2+ peer titles → strong buyer-side terms.
   - Words ONLY in your title that don't appear in any peer's →
     either overly technical or category-mismatch; consider
     re-titling.
4. Compare your candidate list against this corpus. Drop any
   keyword no peer's title or autocomplete reflects.

This catches false positives from your own seller-side bias.
"Cotton" might feel like a strong term for a product listing,
but if every selling peer says "soft" or "breathable" instead,
the buyer doesn't search "cotton" — they search the use-case.

## Step 4 — Cross-check against your existing campaigns

Before adding a keyword to a NEW manual campaign, check whether
it's already running in any of your existing campaigns:

- Open each existing campaign → Targets tab → search for the term.
- Open each Auto campaign → Customer Queries tab → search.

Why: if the same keyword is in two of your own campaigns, they
auction-bid against each other, raising your eCPC and giving the
auction inventory to noon. Always:

- **Already in another Manual campaign**: skip it in the new
  campaign. Don't add it again.
- **Appears in an Auto campaign's Customer Queries with reasonable
  performance**: add as a Phrase keyword in the new Manual AND
  add as a *negative* in the Auto. This moves the conversion to
  your controllable Manual and stops Auto from competing with you.
- **Only in seller-suggested lists, not in any active campaign**:
  safe to add.

## Step 5 — Build the negative list (in parallel)

While doing buyer-side keyword research, log everything that's
*close to your category but wrong*:

| Source | What you'll see | Add as negative |
|---|---|---|
| Storefront autocomplete suggesting your seed word into adjacent category | "kitchen knife block" when your listing is cookware | `knife`, `block` |
| Search results showing wrong product type | "cookware" returns 5 cooking books | `book`, `recipe` |
| Peer-listing titles for a different variant / age | Peer titles say "kids" when your listing is the standard adult version | `kids`, `mini` |
| Country-specific dialect collision | A word that means a different category in another country | The dialect-specific token |

Negatives are as important as positives. Every wasted Click on a
wrong query is real money you could have spent on a relevant one.

## Step 6 — Match-type assignment

For each validated keyword:

- **Phrase** (default): single-word and 2-word terms with general
  buyer intent. e.g. `kitchen pan`, `non stick pan`. Captures word
  order but allows surrounding context.
- **Exact**: branded terms (your own brand to defend, or a
  competitor brand to siphon) and very high-intent generic terms
  where you want to outbid specifically.
- **Phrase + Exact** (same keyword, both types): only for the top
  3–5 most-converting terms after 30+ days of data. Doubles your
  inventory in the auction at higher per-impression cost — only
  worth it for proven winners.

## Output: campaign-ready list

Deliverable from this research is a structured list:

```
Campaign: <product-id> Manual <country> - agent

Keywords (Phrase unless noted):
  English (8 terms):
    <generic category term>
    <category + use-case>
    <category + audience>
    ...
  Local language (4 terms):
    <colloquial term 1>
    <colloquial term 2>
    ...

Negatives:
  Exact (6):
    <single-token negatives>
  Phrase (10):
    <multi-word negatives>

Per-keyword bid: <X> <marketplace currency> (high end of suggested range)
TOS boost: <Y>%
```

Hand this to the create-campaign flow (`ads-creation.md`) for
click-by-click execution.

## What this playbook does NOT do

- **Doesn't generate keywords from product specs alone.** That
  produces seller-side noise. Always validate via the storefront.
- **Doesn't recommend a specific keyword count.** 8–15 is typical;
  your category may need fewer or more. Listen to the autocomplete /
  peer-title signal — if it dries up, stop.
- **Doesn't substitute for Customer Queries harvest after launch.**
  This builds the *initial* list. Post-launch, Customer Queries on
  your live campaigns is the highest-quality keyword source you'll
  ever have.
