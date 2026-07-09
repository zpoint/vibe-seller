# Skill "Definition of Done" review — infra mechanism

> **Principle: jobs get *done*, not *claimed*.** A task that invoked a
> skill is not complete until an independent review confirms — from
> **evidence** (artifacts / live state), not the agent's prose — that the
> skill's job actually finished. "I uploaded the listing" is not done;
> *a batch id + a processing report that says success* is done. "I
> reviewed the ads" is not done; *every trafficked campaign drilled to
> the word level with per-term actions* is done.

This generalizes the ad-specific gates into one mechanism that fires for
**any** skill (Amazon / noon / qianniu / listing-creator / ad-tune /
reports / review-collect …).

## 1. Each skill declares a DoD contract

In `SKILL.md` frontmatter, a `review:` block (all keys optional; a skill
with no `review:` is not gated):

```yaml
review:
  checks: [ads_coverage]        # deterministic checker names (code)
  evidence:                     # artifacts the reviewer MUST inspect
    - AUDIT_SCOPE.json
    - "stores/*/ads/**/*.tsv"
  criteria: |                   # semantic rubric for the LLM judge
    - Every trafficked campaign is drilled to the WORD level: each search
      term listed with clicks/spend/orders and a per-term action
      (negate / raise+amount). A count or category ("N words wasted") is
      NOT a drill.
    - No marketplace is called "empty" unless AUDIT_SCOPE proves it was
      enumerated and had zero active campaigns.
    - Bid directions obey the rules (don't lower a profitable keyword;
      zero-impression → raise/eligibility, never "maintain"; ACOS=0 with
      spend = waste, not "good").
```

- **`checks`** — the **deterministic floor** (§3a). Language-agnostic,
  ground-truth, uncheatable. Where a skill *can* express "done" as code,
  it MUST (an LLM can't reproduce ground truth — see §3a).
- **`evidence`** — glob(s) of artifacts under the task workspace the
  reviewer reads. Forces skills to *capture proof* of completion, and
  keeps the judge grounded in artifacts instead of the result prose.
- **`criteria`** — the **semantic rubric** (§3b) the LLM judge applies.
  Prose, cross-platform, cross-language. This is the only place quality
  rules live — never as phrase-matching regex.

## 2. Trigger — task-level, format-agnostic

The reviewer must fire whenever the skill was used, regardless of what
the agent named its output. So the trigger is **task-level**, not a
filename or a result-shape regex (both proven leaky — a report named
`FOO.md` escaped the old `AD_AUDIT_*.md` glob).

- When a skill is resolved/loaded for a task, its name is recorded
  durably (existing `record_skill_load` → `data/gate_bindings/<task>`).
- At completion, `recorded_skills(task_id)` gives every skill bound to
  the task; the infra runs the DoD review for each that declares one.
- Survives resume / compaction / surgical-fix sessions (the reason the
  durable binding exists).

## 3. The review — hybrid, two tiers

Runs at BOTH completion paths: `set_task_result` (400 on fail) and the
Stop hook / end-of-turn (the streaming-result bypass). Same contract.

### 3a. Deterministic floor (`checks`) — runs first

Cheap, in-process, ground-truth checkers registered by name (same
registry as today's gates). They produce **hard, uncheatable denies**
for anything structurally verifiable:

- `ads_coverage` — from `AUDIT_SCOPE.json` (authoritative active-campaign
  ids pulled from the platform API by `ads_bulk … scope`): every
  enumerated marketplace has a section; every active id appears as a
  drill block; drill counts are **monotonic** across rounds (catches the
  compaction-clobber regression). *This is what an LLM judge cannot
  reproduce and must not replace.*
- `listing_submitted` — a submission artifact exists for every attempted
  SKU with a batch id and a processing report parsed to
  `errors == 0`. ("Uploaded?" answered by the report, not the claim.)
- `export_produced` — the requested export file exists, non-empty,
  row-count > 0.
- `reviews_manifest` — the reviews manifest exists with the expected
  per-country counts.

If the floor denies, we don't even spend a judge call.

### 3b. Active verification reviewer (`criteria`) — runs if the floor passes

For the "actually done, done well / fully" dimension code can't see. The
reviewer is **not a passive text judge** — it is an **active verifier
that re-opens the source of truth and cross-checks the deliverable
against reality**, then loops until the bar is met. "Did the ads get
fully drilled?" / "Did the listing actually get created and succeed?" are
answered by **going and looking**, not by reading the writer's prose.

- **Agent-spawned reviewer subagent with browser + tool access.** It runs
  in the task context (so it can drive the store's Ziniao `browser-use`
  wrapper and MCP tools) but in a **separate, adversarial context** whose
  only job is to disprove "done". A server-side judge was rejected here:
  it cannot open a live console, and the whole point is verification
  against live data. (Feasibility: the codebase has **no server-side LLM
  client** — all model use is the Claude Code subprocess — so an
  out-of-band judge would be new infra anyway; the agent-spawned reviewer
  reuses the existing `reviewer-loop` mechanism.)
- **Grounded in live data = hard to fake.** The reviewer independently
  **opens the actual page** (ad console, listing detail, processing
  report) and the **exports**, and **cross-verifies** them against the
  report's claims — back and forth. The writer cannot fabricate the live
  console, so a verdict grounded in it is not mere self-attestation. This
  is what upgrades option B from "write `Status: ok`" to real
  verification; the deterministic floor (§3a) still guarantees the
  structural bar underneath.
- **Strong reviewer model** — spawn the reviewer on a capable model
  (e.g. opus-tier), not the weak writer model.
- **Structured verdict** — the reviewer writes a schema'd verdict
  (`{verdict: pass|gaps, gaps:[{criterion, evidence_checked, discrepancy}]}`)
  where each gap cites **what it opened and what didn't match**, not a
  bare `Status:`.
- **Injection-safe** — report / search-term / listing text is
  shopper-controlled; the reviewer treats all report + page content as
  untrusted DATA, never instructions.

### 3d. Verification principles (the reviewer's standing guide)

Principle-guided, not a rigid script — the reviewer is capable and knows
how to verify; the per-skill recipe (§5) says *what* source to open and
*what* to cross-check:

1. **Verify against the source of truth, never the report's prose.** A
   claim with no matching live/export evidence is a gap.
2. **Re-open the exact page / export and cross-check.** Sample the
   claimed items (campaigns, SKUs, rows), open them live, compare metrics
   / status / content to the report; a mismatch is a gap.
3. **Prove negatives by looking.** "Market X empty", "no wasted terms",
   "all uploaded" must be checked by opening X / the search-terms export
   / each SKU's status — not accepted.
4. **Back-and-forth until the bar is met.** If verification fails, return
   specific gaps (what was opened, what didn't match); the writer fixes;
   re-verify. Converge with the stall-based policy (§3c).
5. **Don't pass a half-done job.** Partial upload, undrilled campaign,
   empty export, "please do the rest manually" → gaps, not pass.

### 3c. Multi-round convergence

On `gaps`, the agent receives the structured gap list and must fix +
re-submit; the review re-runs. Enforcement is **stall-based, not a flat
iteration cap** (a fixed cap accepts a half-finished report while it's
still improving). Accept the best result only after `STALL_CAP` rounds
with **no net progress** (server tracks progress deterministically —
coverage counts + result delta). A weak-but-progressing model is never
trapped; a stalled-shallow one is never rubber-stamped.

## 4. What changes vs. today

- **Keep** `ad_scope.py` + the coverage/monotonicity/stall core of
  `ad_completeness_review` — rename to the generic `ads_coverage`
  deterministic checker. It is already language-agnostic; it is the floor.
- **Delete** the brittle phrase-matching quality sub-gates
  (`ad_zero_impression`, `ad_searchterm_drill`, and the Chinese-phrase
  regexes) — their intent moves into `criteria` for the LLM judge.
- **Add** the server-side LLM judge + the `review:` declaration parser +
  the DoD orchestrator at both completion paths.
- **Trigger** by task-level binding, not filename.

## 5. Per-skill DoD guidelines (initial set)

Each row: `checks` = deterministic floor ("did it happen?"), `criteria` =
the reviewer's semantic bar, **verify by** = what the reviewer OPENS live
and CROSS-CHECKS (the active part — §3b/§3d).

**`amazon-ads` / `noon-ads` / `qianniu-ads` (audit/tune)**
- floor `ads_coverage`: `AUDIT_SCOPE.json` combos + every active id has a
  drill block + monotonic across rounds.
- criteria: every trafficked campaign drilled to the **word level** with
  a per-term action; correct bid direction; no marketplace inferred-empty;
  no aggregate-only / defer / fabrication.
- **verify by:** open the ad console (or re-export the bulk / Search-Terms
  CSV); sample the campaigns the report claims drilled and confirm the
  search terms + spend/orders match; for any marketplace the report calls
  empty, **switch to it and look**; confirm flagged waste terms exist in
  the export.

**`amazon-listing` / `noon-listing` / `qianniu-listing` (listing creator)**
- floor `listing_submitted`: a submission artifact with a batch id and a
  processing report parsed to `errors == 0` for **every** attempted SKU.
- criteria: the listing is **actually live and successful**; content
  (title / attrs / images / variations) matches the request.
- **verify by:** open the **processing/submission report** and confirm
  success (not "submitted, pending"); open the **live listing detail page**
  (seller central) for each SKU and confirm it exists with the intended
  content — not just that an upload was fired.

**`noon-exports` / `amazon-reports` (import / export / report)**
- floor `export_produced`: the requested file(s) exist, non-empty, rows > 0.
- criteria: the report answers the question and covers the requested scope
  (all stores / countries / date window asked for).
- **verify by:** open the exported file and the source page/report; confirm
  the row count and scope match the ask (e.g. all requested countries
  present), not a partial pull.

**Others** — `ad execution (apply)` (`exec_applied`: every recommendation
applied AND re-verified in console; open the console to confirm new
state), `amazon-invoice` (`invoices_generated`: a PDF per requested order,
totals correct), `noon-fbn` (`fbn_artifact`: confirmation id; open the
shipment/inventory page), `review-collect` (`reviews_manifest`: non-empty
per country; open the product page to confirm reviews exist) — same shape.

Each becomes that skill's `review:` block during implementation.

## 6. Migration / rollback / testing

- **Release-line freeze:** land in `app/skills_v2` only. Frozen
  `app/skills` (0.12.x clients) keeps its current server gate until those
  clients age out — do NOT rewrite it.
- **Rollback:** the `review:` block is additive; a skill with none is
  ungated. Ship skill-by-skill (ads first, prove baselines, then listing…).
- **CI-testable without a live store:**
  - Floor checkers: unit tests on fixture artifacts (placeholder data).
  - Judge: unit-test the *prompt assembly* + a schema-validated verdict
    on saved golden reports (a full-drill one → pass; a shallow
    aggregate-only one → gaps). The judge model call is mocked in unit
    tests; the live judgment is exercised by the debug-store baseline reruns.
- **Baselines (live, debug-store):** two tasks must pass end-to-end —
  a specific SA+AE ad review, and a full "all ads" amazon+noon+cross
  country. Both must full-drill; the listing baseline must show a real
  batch-id + success report.
