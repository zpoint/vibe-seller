"""Access-time enforcement of the declared ad scope.

The declaration was enforced only on the REPORT
(``stop_gates.ad_declaration_checks``): a turn that declared
``campaigns: [A1234567]`` could still READ the excluded campaign and
simply not report it. Observed in CI 201i — a follow-up turn's
"OTHER ROUTES PROBE" fetched ``/campaign/A7654321``, and only the
test's console-hit ground truth caught it.

Invariant: once a turn has declared a campaign-narrowed scope, a
``vibe_seller_ads_call`` request — a typed call that may also WRITE —
cannot address an out-of-scope campaign. Browser commands are not
parsed: what the agent reads in the console is its own judgement, held
to the declaration by the prompt. Same fail-safe direction as the
report gates:
no declaration — or one whose ``campaigns`` list is empty ("every
campaign in the declared combos") — restricts nothing. No obligation
is ever inferred without a declaration.

Campaign ids are matched by SHAPE (CLAUDE.md: regexes match shapes,
never hardcoded values) — a campaign PATH anywhere in the command or
call text counts as a target: ``/campaign/<id>`` (the e2e console and
noon pages), ``/campaign/details/<id>`` (noon deep links),
``/cm/<sp|sb|sd>/campaigns/<id>`` (the real Amazon Ads console) and
``campaignId=<id>`` deep links. The command text is what will execute,
so a path quoted inside a probe list or a script literal is a target
too. Tool OUTPUT is never scanned: a bare id that merely appears in
printed page text is not a target and is never refused.
"""

import json
import re

from app.ai.ad_declaration import campaign_in_scope, load_declaration

# Path shapes, longest-first so /campaign/details/<id> is not also
# read as /campaign/details + a bare id.
_TARGET_RES = (
    re.compile(r'/campaign/details/([A-Za-z0-9_-]{2,})'),
    re.compile(r'/campaign/(?!details/)([A-Za-z0-9_-]{2,})'),
    re.compile(r'/cm/[a-z]+/campaigns/([A-Za-z0-9_-]{2,})'),
    re.compile(r'[?&]campaignId=([A-Za-z0-9_-]{2,})'),
)


def campaign_targets(text: str) -> list[str]:
    """Campaign ids this text ADDRESSES (navigation/fetch/probe args)."""
    out: list[str] = []
    for rx in _TARGET_RES:
        out.extend(rx.findall(text or ''))
    return out


def refusal(task_id: str | None, text: str) -> str | None:
    """The deny message when *text* addresses an out-of-scope campaign.

    ``None`` when the declaration is absent, does not narrow campaigns,
    or every addressed campaign is in scope.
    """
    decl = load_declaration(task_id)
    if not decl:
        return None
    scope = decl.get('scope') or {}
    declared = [str(c).strip() for c in (scope.get('campaigns') or [])]
    if not declared:
        return None
    offenders = sorted({
        cid
        for cid in campaign_targets(text)
        if not campaign_in_scope(scope, cid)
    })
    if not offenders:
        return None
    return (
        f'This turn declared kind="{decl.get("kind")}" narrowed to '
        f'campaigns {declared}, and this call addresses '
        f'campaign(s) {offenders} — outside that scope. Reading is '
        'limited to the declared campaigns. Two legal moves: (1) work '
        f'only within {declared}; or (2) stop and ask the user — a '
        'NEW user message re-opens declaring, while within this turn '
        'the declaration only narrows and can never widen to include '
        'these campaigns.'
    )


def ads_call_refusal(task_id: str | None, payload: dict) -> str | None:
    """Scope refusal for one ``vibe_seller_ads_call`` request."""
    text = ' '.join([
        str(payload.get('path') or ''),
        json.dumps(payload.get('params') or {}),
        json.dumps(payload.get('body') or {}),
    ])
    return refusal(task_id, text)
