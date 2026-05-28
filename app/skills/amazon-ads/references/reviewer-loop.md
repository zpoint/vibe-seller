# Reviewer loop — Phase 3 self-check + Phase 4 execution review

This file documents **two reviewer subagents**:
- **Phase 3 (`ads-format-review`)** — checks the audit Markdown
  against `format-anchor.md`. Runs before the audit is delivered.
- **Phase 4 (`ads-execution-review`)** — checks that every
  Recommendation in the audit was actually applied to the live
  console, and that the per-campaign TSV files reflect the new
  state. Runs after Phase 4 application; gated by the Stop-hook
  via `EXEC_REVIEW_*_iter*.md`.

Both modes follow the same loop shape and the same Status
semantics. The differences are in *what they read* and *what they
check* — see § Execution-review mode below.

## Phase 3 — Format reviewer (`ads-format-review`)

Before calling `vibe_seller_set_task_result`, the agent runs the
audit through a reviewer subagent. The reviewer checks the
Markdown against `format-anchor.md`; if it finds gaps the agent
fixes them and re-reviews. The loop terminates when the reviewer
returns `Status: ok` or after 5 iterations with `Status:
incomplete`.

## Why a separate reviewer

A reviewer in a separate LLM context catches things the writing
agent overlooks. The writing agent under context pressure (long
audit, multiple compactions) routinely:

- Skips Customer Queries tables for some noon campaigns
- Writes Recommendation cells with `Trim to X (−40 %)`, breaking
  the 25 % cap
- Drops the priority table at the end because "context is full"
- Uses old jargon like `机械状态` from prior sessions

The reviewer reads only two files (audit + anchor) and checks one
list. Its context is small and dedicated; it can't be distracted
by drilling URLs or remembering 30 keyword bids.

The reviewer's output is a file on disk; the Stop-hook reads that
file to decide whether to allow stop. **The agent cannot exit
without the reviewer having written `Status: ok` (or
`incomplete` at iter 5).**

## How the loop works (concrete steps)

```
# At end of Phase 3, immediately after writing AD_AUDIT_<date>.md
# and all per-campaign TSVs:

iter = 1
loop:
    # Spawn reviewer subagent. Use general-purpose subagent_type;
    # the reviewer's job is fully specified by the prompt below.
    Agent(
        description="Format-check the audit",
        subagent_type="general-purpose",
        prompt=REVIEWER_PROMPT.format(
            audit_path="./AD_AUDIT_<YYYY-MM-DD>.md",
            anchor_path="<.claude/skills>/amazon-ads/references/format-anchor.md",
            iter=iter,
            review_out=f"./REVIEW_<YYYY-MM-DD>_iter{iter}.md",
        ),
    )

    # Reviewer wrote REVIEW_<date>_iter<N>.md. Parse its Status line.
    status = read_status_line(f"./REVIEW_<YYYY-MM-DD>_iter{iter}.md")

    if status == "ok":
        break                              # done — proceed to set_task_result
    if iter == 5:
        write_incomplete_status(iter)      # last-resort marker
        break
    fix_gaps_from_review_file(iter)        # Edit the audit in place
    iter += 1

# Stop-hook gate (in claude_backend_hooks.py) reads the last
# REVIEW_*_iter*.md and refuses to stop unless Status is "ok" or
# (iter >= 5 AND Status is "incomplete").

vibe_seller_set_task_result("./AD_AUDIT_<YYYY-MM-DD>.md")
```

## The REVIEWER_PROMPT (verbatim, agent must use this)

```
You are the ads-audit format reviewer. Read two files:

1. AUDIT: {audit_path}
2. ANCHOR: {anchor_path}

The ANCHOR file defines the canonical structure every audit must
follow. The AUDIT file is what the writing agent just produced.

Check the AUDIT against every "Mandatory components" rule in the
ANCHOR (currently rules 1 through 16). For each violation, record
one gap entry. Do NOT rewrite the audit. Do NOT make subjective
judgments about the audit's analytical quality — only check the
mechanical structure.

Be strict about rules 14, 15, 16 in particular. These three are
the most common "agent took a shortcut" failures:

- **Rule 14 (no placeholder rows):** scan every Targeting /
  Search Terms / Targets / Customer Queries table for an active
  campaign. Any row whose Recommendation cell reads "Drill
  skipped this cycle", "small campaign — skipped", "React
  Virtualized grid — extraction blocked", "data not captured",
  "see TSV for full detail" as a SUBSTITUTE for actual rows, or
  similar excuse phrasings = a gap. Small spend (e.g. AED 30) is
  not a skip reason. Mention each campaign ID separately so the
  agent knows which to drill.

- **Rule 15 (Pause needs Alternatives, evidence-backed):** scan
  every Recommendation cell containing `**Pause**` or `**Pause
  campaign**` or `Pause and reallocate`. Triggers Rule 15 only
  for campaign-level or major-cut pauses (≥ 50 % of the
  campaign's Targeting rows recommended Pause).

  The Alternatives section passes if EITHER:

  **(A) ≥ 3 verified rows.** Each row cites one of three permitted
  sources (A / B / C, per `format-anchor.md § Rule 15`). For
  each row, perform the verification:
    - Source A (cross-platform TSV): open the cited file at
      `stores/<slug>/ads/<other-platform>/<country>/<id>.tsv`
      from the workspace. Verify the file exists, the cited
      keyword appears as a row, and the ROAS/spend in the
      Evidence cell matches that row.
    - Source B (same-platform other-campaign TSV): same shape.
    - Source C (Brand Analytics): open the cited file at
      `stores/<slug>/ads/brand-analytics/<ASIN>_<date>.tsv`.
      Verify the file exists, the filter-ASIN matches the ASIN
      cited in the Evidence cell, and the cited keyword
      appears as a row in the file.

    Any row whose evidence cannot be verified on disk is a
    fabrication; flag it as a gap with the specific row's
    Source + Keyword cited.

  **(B) "Searched, none found" block.** The Alternatives section
  is a single block explicitly proving all three sources were
  searched and returned empty. Verify each of the three lines:
    - Cross-platform: the cited directory exists; no TSV inside
      mentions the ASIN.
    - Same-platform other: same shape against the cited
      directory.
    - Brand Analytics: the capture file exists at the cited
      path; the filter-ASIN matches; the data-row count is 0.

    Any of the three proofs failing → flag as gap.

  Fabricated rows are the worst class of defect — they look
  authoritative but mislead the merchant. Be strict: if you
  can't open the cited file and find the cited keyword, the
  row is a fabrication regardless of how plausible it looks.

  **Readable-name format check.** Every Evidence cell must open
  with a readable reference name (platform + country + campaign
  name + canonical id), not just a raw file path. Per
  `format-anchor.md § Readable evidence references`:

  - Source A/B opening: `<Platform> <Country> — <Campaign Name> (<id>):`
  - Source C opening:   `Amazon <Country> — Brand Analytics ASIN report for <SKU-name> (ASIN <BXX...>):`

  An Evidence cell that opens with `stores/...` (raw path only,
  no readable name) is a defect — the reviewer flags it even if
  the path verifies. The readable name is what makes the audit
  reviewable; the path is just so the reviewer can verify.

  **Brand Analytics infra-block exemption.** If the agent could
  not access Brand Analytics this session because of OTP /
  authenticator-app requirement or Brand Registry not enabled,
  it must produce one of these proof markers:
    - `stores/<slug>/ads/brand-analytics/<ASIN>_<date>.tsv` with
      a `# infra_block: OTP_AUTH_APP_REQUIRED` header line, OR
    - `stores/<slug>/ads/brand-analytics/_unavailable_<date>.txt`
      with one explanatory line.
  When such a marker exists, the agent may cite the Outcome (B)
  "Searched, none found" block with BA marked as `infra-blocked`
  rather than `0 rows`. Reviewer accepts. Without the marker, BA
  claims of unavailability are treated as fabrications.

- **Rule 16 (SB-Video extraction):** SB-Video campaigns must
  have actual keyword rows in the Targeting table, not "React
  Virtualized grid — per-row extraction blocked" excuses. If
  the agent couldn't extract, that's an infra issue to surface
  in the report's Notes section, but the keyword rows are still
  required.

Write your findings to: {review_out}

Use exactly this format:

# Review iter {iter} — <ISO timestamp>

Status: ok                              ← exactly one of: ok | gaps | incomplete
Audit file: {audit_path}
Reviewed against: {anchor_path}

## Gaps
<empty if Status=ok; one bullet per gap otherwise; each bullet
starts with [campaign_id] or [global] for context>

## Notes
<optional reviewer commentary; e.g. "iter 5 — accepting
incomplete per max-iters policy">

Set Status=ok ONLY if every rule passes. Otherwise Status=gaps
with the bullets. After writing, return the path to the file
({review_out}) as your final message. Do not return the gap list
in chat — the file is the source of truth.
```

## Status semantics

| Status | When the reviewer writes it | What the hook does |
|---|---|---|
| `ok` | All mandatory components present, no rule violated | Allows stop |
| `gaps` | At least one rule violated; gap bullets listed | **Denies stop**; agent must fix and re-review |
| `incomplete` | Only valid at iter 5; remaining gaps documented | Allows stop with caveat trail on disk |

## What "fix the gaps" looks like for the main agent

For each gap in the review file, edit the audit Markdown in place.
Do NOT re-drill, do NOT re-write the audit from scratch — surgical
patches only:

| Gap pattern | Surgical fix |
|---|---|
| `[A11111111] missing Recommendation column in Targeting table` | Add the column; populate every row using the per-row data already in the table |
| `[C_DEMO0001] missing Customer Queries table` | Add a `#### Customer Queries` section; if you didn't drill it, drill it now (just this one campaign), don't re-drill the rest |
| `[A22222222] Trim recommendation shows −33 %, exceeds 25 % cap` | Recompute the proposed bid as `current × 0.75` and update the cell |
| `[global] no priority table at end` | Append the 汇总建议 table |
| `[global] used "机械状态" jargon` | Replace with plain status line per anchor |

After fixes, spawn the reviewer again. The reviewer reads the
*updated* audit and re-checks. Each iteration writes a new
`REVIEW_<date>_iter<N>.md` (iter1, iter2, …) — disk shows the
full review history; the hook reads the latest one.

## What does NOT trigger a fix loop

The reviewer only checks structure. It does NOT second-guess:

- The agent's choice of bid value (within the 25 % cap)
- Which keywords to negate vs harvest
- Whether a campaign should be paused vs trimmed
- Cross-platform priority calls

Those are the writing agent's judgment calls. The reviewer's job is
solely "is the report shaped right".

---

## Phase 4 — Execution reviewer (`ads-execution-review`)

After Phase 3 has delivered an audit (`REVIEW_*_iter*.md Status: ok`)
and the user instructs the agent to **execute** the plan, the agent:

1. Creates `EXECUTION_LOG.md` (this file's existence flips the
   Stop-hook execution gate on — see
   `bash_safety.check_exec_review_status`).
2. Works each actionable Recommendation row from the audit, applying
   it on the live Amazon / Noon console with per-action read-back
   verification.
3. Updates the per-campaign TSV with the new bid / status /
   negative-keyword / harvested-keyword row.
4. Spawns the `ads-execution-review` subagent (same
   `subagent_type=general-purpose` — the role is defined by the
   prompt).
5. Reads the resulting `EXEC_REVIEW_<date>_iter<N>.md`.
6. Fixes gaps on the live console (NOT in the audit Markdown — the
   audit's recommendations are frozen at this point) and re-runs.
7. Repeats until `Status: ok` or `iter 5 + Status: incomplete`.

The Stop-hook denies `vibe_seller_set_task_result` until the
execution reviewer accepts. Same gate shape as Phase 3, different
file name (`EXEC_REVIEW_*` vs `REVIEW_*`).

### Why a separate execution reviewer

The Phase 3 reviewer reads the audit only. It can't tell whether the
recommendations were ever applied — it has no view of the live
console or the post-execution TSV state. The execution reviewer's
sole job is to **diff the recommendations against the resulting
state** and surface anything missing, claimed-but-unverified, or
applied to the wrong value.

A live console can disagree with the agent's claim for many reasons:
- Edit click landed but the field didn't save (network error)
- Wrong keyword row was edited (similar-looking duplicate)
- Campaign type doesn't support the recommended action (failed
  silently)
- Agent claimed `applied` from memory without re-reading the page

The execution reviewer can't re-open the console itself (it runs in
a different subagent context with no browser tools); instead it
demands the agent leave **on-disk artifacts that prove the live
state**: a Verification cell in `EXECUTION_LOG.md` quoting the
read-back text, and a corresponding row update in the per-campaign
TSV with `applied_at` set.

### The EXEC REVIEWER_PROMPT (verbatim, agent must use this)

```
You are the ads-execution reviewer. Read four sources:

1. AUDIT:           {audit_path}                            (recommendations)
2. EXECUTION LOG:   {exec_log_path}                         (what agent claims)
3. ANCHOR:          {anchor_path}                           (rules E1–E8)
4. TSV FILES:       stores/<slug>/ads/<platform>/<country>/*.tsv (on-disk state)

Walk the AUDIT row-by-row. For every Recommendation cell whose
verb is one of: Trim / Raise / Pause / Negate / Harvest / Pause
(campaign) — these are "actionable" rows. Hold and Hold (PROTECT)
are NOT actionable; skip them for this review.

For each actionable row, run the rule-E1..E8 checks from
`format-anchor.md § Mandatory execution components`. Specifically:

- **E1 (Coverage):** find a matching row in EXECUTION_LOG by
  (platform, country, campaign_id, keyword/target, verb). If none
  found → gap MISSING_ACTION with the audit row identifier.

- **E2 (Verification cell):** the matching EXECUTION_LOG row's
  Status is `applied`. The Verification cell must quote a live
  read-back (e.g. "page bid field shows 1.70", "negative-keywords
  list shows 'X' Exact"). Empty or generic ("done", "ok") → gap
  UNVERIFIED_CLAIM.

- **E3 (Trim/Raise → LIVE PAGE bid) — VERIFY AGAINST THE LIVE
  CONSOLE, NOT JUST THE TSV.** Open the campaign's targeting or
  negative-targeting page via `browser-use open …/targeting?…`. Wait
  10–15s for React Virtualized to render. Read the **Bid** column
  from the actual page text for the cited keyword. The live page
  Bid value must match the EXECUTION_LOG target value (±0.01).
  After the live check passes, ALSO confirm the per-campaign TSV's
  Bid column reflects the same value (it should, since the agent
  was supposed to sync them).

  **Critical incident (2026-05-24):** an earlier reviewer iteration
  only cross-checked EXECUTION_LOG target == TSV Bid column. Both
  were written by the agent. They matched each other but the live
  page showed the old bid. The user's independent live sweep found
  3 confirmed mismatches:
  - A44444444 row 78 "applied" negate of women's cotton socks —
    live shows only 3 unrelated negatives, the term was never added
  - A55555555 row 77 "applied" negate — same: not on live page
  - A66666666 row 79 "applied" reactivate at SAR 2.50 — live
    shows AED 2.00

  Cross-checking agent-written artifacts against each other does
  not catch this defect class. The reviewer MUST navigate the live
  console for every applied/already_present row.

  **The "live read-back" claim in EXECUTION_LOG's Verification cell
  is the agent's claim, not your verification.** Confirm it
  independently by opening the page yourself.

  Mismatch → gap LIVE_PAGE_MISMATCH with the campaign id, keyword,
  expected (from EXECUTION_LOG), actual (from live page).

  **Common reviewer mistake** to avoid: the TSV often has BOTH a
  `Bid` column and a `Recommendation` column saying "Trim to X". A
  freshly-written audit TSV may have the OLD bid in the Bid column
  AND "Trim to X" in the Recommendation column. Post-execution, the
  Bid column must be updated to X. If the Bid column still shows
  the old value, the TSV is stale — flag INCORRECT_APPLICATION even
  though the live page may show the new bid (and the agent's
  Verification cell in EXECUTION_LOG quotes that live read-back).
  Drift between the live page and the TSV is the bug class this
  rule catches.

  Concrete example of a stale-TSV defect (from a real run):
    EXECUTION_LOG row 9: "Trim Close match bid SAR 2.80 (was 3.50,
                          −20%) | applied | Verification: page bid
                          field shows SAR 2.80"
    TSV row: "Close match | Delivering | SAR 3.50 | ... |
              Recommendation: Trim to SAR 2.80 (−20%)"
    → INCORRECT_APPLICATION: Bid column shows 3.50, expected 2.80.

  Mismatch → gap INCORRECT_APPLICATION with the file path,
  campaign id, keyword/target, and observed-vs-expected values.

- **E4 (Pause):** TSV row's status column must show "Paused" (or
  the platform's equivalent — "Inactive" on Noon). For a
  Pause-campaign action, EVERY targeting row in that TSV must show
  Paused. Any row still "Delivering" → gap.

- **E5 (Negate) — VERIFY AGAINST LIVE NEGATIVE-TARGETING PAGE.**
  Open `…/campaigns/<id>/negative-keywords?…` (campaign-level) OR
  `…/campaigns/<id>/ad-groups/<ag>/negative-targeting?…`
  (ad-group-level). Read the page's negative-keywords table.
  The negated term + match type must appear as a row there. The
  TSV file's negative-keywords block is a record but NOT a
  substitute for live verification — see E3's critical incident.
  Missing on live page → gap LIVE_PAGE_MISMATCH (not just
  MISSING_ARTIFACT) — the action was claimed but never executed
  on the live console.

- **E6 (Harvest):** the harvested keyword must exist as a row in
  the target campaign's TSV. If the agent created a NEW campaign
  (because the original campaign type couldn't host the keyword),
  a new TSV file at
  `stores/<slug>/ads/<platform>/<country>/<new_id>.tsv` must
  exist and contain the keyword. Missing on both sides → gap
  MISSING_ARTIFACT.

- **E7 (Failed-without-resolution):** any EXECUTION_LOG row with
  Status=`failed` must be followed by EITHER a retry row that
  succeeded for the same recommendation OR an explicit Notes cell
  explaining why the action cannot be applied this session.
  Otherwise → gap UNRESOLVED_FAILURE.

- **E8 (Priority-table cross-check):** the audit's 汇总建议
  table summarizes priorities for the user. Every priority-table
  entry should map to one or more EXECUTION_LOG rows. A priority
  entry with no execution row → gap PRIORITY_NOT_EXECUTED.
  (Optional rule: a flood of EXECUTION_LOG rows not represented
  in the priority table is a NOTE, not a gap — the agent may have
  over-executed; reviewer logs it but does not deny.)

You do NOT verify the Phase 3 rules (1–16) again — those were
already gated by the format reviewer. You do NOT judge whether the
recommendation itself is sensible. You only check whether what the
audit says should happen actually happened on disk.

Write your findings to: {review_out}

Use exactly this format:

# Execution Review iter {iter} — <ISO timestamp>

Status: ok                              ← exactly one of: ok | gaps | incomplete
Audit file: {audit_path}
Execution log: {exec_log_path}
Reviewed against: {anchor_path} § Mandatory execution components

## Gaps
<empty if Status=ok; one bullet per gap otherwise. Each bullet
opens with the gap code in brackets, then the identifier:
- [MISSING_ACTION] A11111111 "wireless earbuds" Broad Trim
- [UNVERIFIED_CLAIM] Noon US C_DEMO0001 row "Harvest <cat-A>"
- [INCORRECT_APPLICATION] A11111111 TSV row 1 bid is USD 2.00, expected 1.70
- [MISSING_ARTIFACT] A11111111 negate "cheap wireless earbuds" — no
  negative-keyword row in TSV
- [UNRESOLVED_FAILURE] C_DEMO0001 harvest <cat-A> marked failed,
  no retry, no Notes
- [PRIORITY_NOT_EXECUTED] priority #2 "Negate 2 typo queries" — no
  EXECUTION_LOG rows for either query>

## Notes
<optional reviewer commentary; e.g. "iter 5 — accepting incomplete
per max-iters policy". Also log over-execution observations here
(EXECUTION_LOG rows beyond the priority table) as informational —
not gaps.>

Set Status=ok ONLY if every actionable Recommendation row maps to
a verified EXECUTION_LOG row + a TSV update. Otherwise Status=gaps.
After writing, return the path to the file ({review_out}) as your
final message. Do not return the gap list in chat — the file is
the source of truth.
```

### Fixing exec-review gaps

The fix loop for Phase 4 is NOT "edit the audit Markdown" — that's
the Phase 3 loop. Phase 4 fixes look like:

| Gap code | Surgical fix |
|---|---|
| `MISSING_ACTION` | Navigate to the live console, apply the missing action, read back, update TSV, append row to `EXECUTION_LOG.md` with Verification cell |
| `UNVERIFIED_CLAIM` | Re-open the campaign on the live console, read the field, paste the read-back text into the Verification cell |
| `INCORRECT_APPLICATION` | The page might already be right and the TSV stale, or vice versa. Open BOTH; fix whichever is wrong; update Verification cell |
| `MISSING_ARTIFACT` | The action was claimed but the TSV doesn't reflect it. Open the campaign, apply (or re-apply), update TSV |
| `UNRESOLVED_FAILURE` | Retry the action; if it genuinely cannot be applied, add a Notes cell explaining why and proceed |
| `PRIORITY_NOT_EXECUTED` | Apply the missing priority action OR (if it was correctly deferred) document the deferral in EXECUTION_LOG's Notes |

After fixes, spawn the execution reviewer again. Each iteration
writes a new `EXEC_REVIEW_<date>_iter<N>.md`; the hook reads the
latest.

### What does NOT trigger an exec-fix loop

- Audit Markdown content. The audit is frozen once it passed Phase 3
  reviewer. If the user wants the audit re-audited, that's a new
  task or a new follow-up, not an EXEC gap.
- Live-page UI quirks the agent worked around. As long as the
  Verification cell quotes a real read-back, the reviewer accepts.
- New recommendations the agent invents mid-execution. Those are
  out of scope; the reviewer only checks coverage of the audit's
  rows, not novelty.
