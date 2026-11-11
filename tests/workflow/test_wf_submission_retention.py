"""A refused submission survives its refusal.

Result gates refuse by raising, and the raise used to happen BEFORE
``task.result`` was assigned — so a refusal destroyed the submission.
Two live runs submitted 41 and 14 times, every one refused, and both
ended holding nothing; the end-of-turn fallback then filed the agent's
own chat narration as the deliverable and the task shipped FAILED over
a transcript.

Submission and verdict are separate facts. What the agent submitted is
recorded unconditionally; the refusal only annotates it. See
``app/task_outcome.py`` for the precedence that consumes them.
"""

import asyncio
import json

import pytest

from app.ai.stop_gates import GateDeny, record_skill_load
from app.config import VIBE_SELLER_DIR
import app.routers.task_submission as task_submission
from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow

PARTIAL = (
    'Audit report, 95% complete.\n\n'
    '| Campaign | Spend | ROAS |\n'
    '|---|---|---|\n'
    '| widget-006 | 10 | 4.2 |\n'
)
CLEAN = (
    'Audit report, complete.\n\n'
    '| Campaign | Spend | ROAS |\n'
    '|---|---|---|\n'
    '| widget-006 | 10 | 4.2 |\n'
)


def _bind_listing_skill(task_id: str) -> None:
    """This task loaded amazon-listing, whose SKILL.md declares the gate.

    ``upload_verdict`` is skill-bound: a marker alone does not run it. The
    durable binding (``record_skill_load``, what the Skill-tool hook writes
    in production) plus the runtime SKILL.md copy are the two halves of
    that declaration, so these tests arm the gate the same way a real
    listing session does.
    """
    record_skill_load(task_id, 'amazon-listing')
    md = (
        VIBE_SELLER_DIR / '.claude' / 'skills' / 'amazon-listing' / 'SKILL.md'
    )
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(
        '---\nname: amazon-listing\ngates: [upload_verdict]\n---\n',
        encoding='utf-8',
    )


def _arm_upload_gate(task_id: str) -> None:
    """An upload was STARTED this turn and no batch verdict exists: the
    upload gate refuses every submit until the marker is resolved.

    ``UPLOAD_ATTEMPT.json`` is what bh_upload_flatfile writes at its arm
    point. A template download writes nothing like it — fetching one is
    reading a category, and a run that only inspected one must not be
    refused for an unverified upload.
    """
    d = VIBE_SELLER_DIR / 'tasks' / task_id
    d.mkdir(parents=True, exist_ok=True)
    _bind_listing_skill(task_id)
    (d / 'UPLOAD_ATTEMPT.json').write_text(
        json.dumps({'file': 'out.txt', 'host': 'sellercentral.amazon.sa'}),
        encoding='utf-8',
    )


def _disarm_upload_gate(task_id: str) -> None:
    (VIBE_SELLER_DIR / 'tasks' / task_id / 'UPLOAD_ATTEMPT.json').unlink()


async def _running_task(admin_client, install_fake_agent, name):
    """A task whose fake agent stays alive, so MCP endpoints see RUNNING."""
    gate = asyncio.Event()
    install_fake_agent.default_scenario = FakeAgentScenario(
        result='streamed narration from the agent',
        gate=gate,
    )
    r = await admin_client.post('/api/stores', json={'name': name})
    store_id = r.json()['id']
    r = await admin_client.post(
        '/api/tasks',
        json={'title': 'retention', 'store_id': store_id},
    )
    task_id = r.json()['id']
    for _ in range(200):
        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        if data['status'] == 'running':
            break
        await asyncio.sleep(0.02)
    assert data['status'] == 'running'
    return task_id, gate


class TestRefusalKeepsTheSubmission:
    async def test_refused_submission_is_retained(
        self, admin_client, install_fake_agent
    ):
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Retain Store'
        )
        _arm_upload_gate(task_id)
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result', json={'result': PARTIAL}
        )
        assert r.status_code == 400

        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        # The refusal stands — no accepted deliverable…
        assert not data.get('result')
        # …but what the agent submitted is still here.
        assert data['submitted_result'] == PARTIAL
        assert data['submission_count'] == 1
        assert data['review_gaps']

        gate.set()
        await wait_for_task(admin_client, task_id)

    async def test_run_ending_on_refusals_ships_the_report_not_narration(
        self, admin_client, install_fake_agent
    ):
        """THE REGRESSION, end to end.

        Submission refused, agent reaches for the error channel as its
        exit, turn ends. The run must complete carrying the retained
        report plus an honest gap list — not FAILED showing the agent's
        chat narration.
        """
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Regression Store'
        )
        _arm_upload_gate(task_id)
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result', json={'result': PARTIAL}
        )
        assert r.status_code == 400

        r2 = await admin_client.post(
            f'/api/tasks/{task_id}/error',
            json={'error': 'upload verdict never came back'},
        )
        assert r2.status_code == 200

        gate.set()
        final = await wait_for_task(admin_client, task_id)

        assert final['status'] == 'completed'
        assert not final.get('error')
        assert '95% complete' in final['result']
        # The narration must NOT be the deliverable.
        assert 'streamed narration' not in final['result']
        # Both the gate's gap and the agent's own account ride along.
        assert 'upload verdict never came back' in final['result']

    async def test_accepted_submission_clears_the_gaps(
        self, admin_client, install_fake_agent
    ):
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Accept Store'
        )
        _arm_upload_gate(task_id)
        assert (
            await admin_client.post(
                f'/api/tasks/{task_id}/result', json={'result': PARTIAL}
            )
        ).status_code == 400

        _disarm_upload_gate(task_id)
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result', json={'result': CLEAN}
        )
        assert r.status_code == 200

        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        assert data['result'] == CLEAN
        assert data['submitted_result'] == CLEAN
        assert data['submission_count'] == 2
        assert not data['review_gaps']

        gate.set()
        final = await wait_for_task(admin_client, task_id)
        assert final['status'] == 'completed'
        assert final['result'] == CLEAN


class TestIncompleteExit:
    async def test_declared_incomplete_rides_along_as_caveats(
        self, admin_client, install_fake_agent
    ):
        """The honest exit: a deliverable plus what's missing.

        Reaching for ``set_task_error`` to explain caveats over real
        output is what the error channel was being abused for; this is
        the door that makes that unnecessary.
        """
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Incomplete Store'
        )
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            json={
                'result': CLEAN,
                'incomplete': ['noon AE search-term layer not collected'],
            },
        )
        assert r.status_code == 200

        gate.set()
        final = await wait_for_task(admin_client, task_id)
        # Declaring incompleteness is NOT a failure.
        assert final['status'] == 'completed'
        assert not final.get('error')
        assert 'noon AE search-term layer' in final['result']

    async def test_incomplete_does_not_bypass_a_gate(
        self, admin_client, install_fake_agent
    ):
        """It is an honest exit, not a skeleton key."""
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'No Bypass Store'
        )
        _arm_upload_gate(task_id)
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            json={'result': PARTIAL, 'incomplete': ['let me out']},
        )
        assert r.status_code == 400

        gate.set()
        await wait_for_task(admin_client, task_id)

    async def test_incomplete_does_not_bypass_a_soft_gate(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        """A soft gate's one refusal is not waived by ``incomplete``
        either: the agent still gets its one chance to fix, and only the
        second submit passes."""
        gap = 'reports_2026-01: fee report missing'

        async def short_delivery(db, task, task_root, **kw):
            return GateDeny(
                gate='deliverable_manifest', reason=gap, gaps=(gap,)
            )

        monkeypatch.setattr(
            task_submission, 'check_deliverables', short_delivery
        )
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Soft No Bypass Store'
        )
        body = {'result': PARTIAL, 'incomplete': ['let me out']}
        r = await admin_client.post(f'/api/tasks/{task_id}/result', json=body)
        assert r.status_code == 400

        r = await admin_client.post(f'/api/tasks/{task_id}/result', json=body)
        assert r.status_code == 200

        gate.set()
        final = await wait_for_task(admin_client, task_id)
        assert final['status'] == 'completed'
        assert 'let me out' in final['result']

    async def test_a_soft_gate_let_through_ships_its_gap(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        """Past its one refusal a soft gate lets the result through —
        with its gap among the result's caveats, not only in a log line."""
        gap = 'reports_2026-01: fee report missing'

        async def short_delivery(db, task, task_root, **kw):
            return GateDeny(
                gate='deliverable_manifest', reason=gap, gaps=(gap,)
            )

        monkeypatch.setattr(
            task_submission, 'check_deliverables', short_delivery
        )
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Soft Gap Store'
        )
        body = {'result': PARTIAL}
        r = await admin_client.post(f'/api/tasks/{task_id}/result', json=body)
        assert r.status_code == 400
        r = await admin_client.post(f'/api/tasks/{task_id}/result', json=body)
        assert r.status_code == 200
        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        assert gap in (data.get('review_gaps') or '')
        gate.set()
        await wait_for_task(admin_client, task_id)


def _capture_telemetry(monkeypatch) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        'app.telemetry.send',
        lambda event, props=None: events.append((event, dict(props or {}))),
    )
    return events


class TestGateTelemetry:
    async def test_soft_gate_refusal_then_let_through(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        events = _capture_telemetry(monkeypatch)
        gap = 'reports_2026-01: fee report missing'

        async def short_delivery(db, task, task_root, **kw):
            return GateDeny(
                gate='deliverable_manifest', reason=gap, gaps=(gap,)
            )

        monkeypatch.setattr(
            task_submission, 'check_deliverables', short_delivery
        )
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Gate Telemetry Store'
        )
        body = {'result': PARTIAL}
        await admin_client.post(f'/api/tasks/{task_id}/result', json=body)
        await admin_client.post(f'/api/tasks/{task_id}/result', json=body)
        gated = [p for e, p in events if e == 'task_result_gated']
        assert [(p['gate'], p['outcome'], p['attempt']) for p in gated] == [
            ('deliverable_manifest', 'refused', 1),
            ('deliverable_manifest', 'failed_open', 2),
        ]
        assert gated[0]['task_id'] == task_id
        assert gated[0]['gap_count_bucket'] == '1-5'
        gate.set()
        await wait_for_task(admin_client, task_id)

    async def test_a_banner_marked_result_is_reported_once(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        """Refused once; the next submit ships banner-marked: one
        banner_marked event, no failed_open on top, and each gap once."""
        events = _capture_telemetry(monkeypatch)

        class ContradictingGate:
            @staticmethod
            def check(result, task_id, rules):
                return GateDeny(
                    gate='fake_money',
                    reason='bad numbers',
                    gaps=('x', 'note'),
                    contradictions=('x',),
                )

        monkeypatch.setattr(
            'app.routers.tasks.resolve_skill_gates',
            lambda loaded, workspace: [('fake_money', ContradictingGate)],
        )
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Banner Gate Store'
        )
        url = f'/api/tasks/{task_id}/result'
        r = await admin_client.post(url, json={'result': CLEAN})
        assert r.status_code == 400, r.text
        r = await admin_client.post(url, json={'result': CLEAN})
        assert r.status_code == 200, r.text
        gated = [p['outcome'] for e, p in events if e == 'task_result_gated']
        assert gated == ['refused', 'banner_marked'], gated
        gate.set()
        done = await wait_for_task(admin_client, task_id)
        caveats = json.loads(done.get('review_gaps') or '[]')
        assert sorted(caveats) == ['note', 'x'], caveats

    async def test_only_the_fresh_check_is_a_refusal(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        """Two checks whose rounds are one submit apart.

        gate_a reports on submits 1 and 2, gate_b only from submit 2.
        Submit 2 is refused once — for gate_b, the check spending its
        round — and gate_a is counted failed_open rather than as a
        second refusal. Submit 3 goes through with both findings as
        caveats, banner-marked for gate_b's contradiction.

        The gate names are two the telemetry knows (``_GATES``), so the
        two events can be told apart.
        """
        events = _capture_telemetry(monkeypatch)
        calls = {'b': 0}

        class GateA:
            @staticmethod
            def check(result, task_id, rules):
                return GateDeny(
                    gate='ad_changes', reason='a gap', gaps=('a gap',)
                )

        class GateB:
            @staticmethod
            def check(result, task_id, rules):
                calls['b'] += 1
                if calls['b'] < 2:
                    return None
                return GateDeny(
                    gate='ad_coverage',
                    reason='spend disagrees',
                    gaps=('spend disagrees',),
                    contradictions=('spend disagrees',),
                )

        monkeypatch.setattr(
            'app.routers.tasks.resolve_skill_gates',
            lambda loaded, workspace: [
                ('ad_changes', GateA),
                ('ad_coverage', GateB),
            ],
        )
        task_id, release = await _running_task(
            admin_client, install_fake_agent, 'Fresh Refusal Store'
        )
        url = f'/api/tasks/{task_id}/result'
        for code in (400, 400):
            r = await admin_client.post(url, json={'result': CLEAN})
            assert r.status_code == code, r.text
        r = await admin_client.post(url, json={'result': CLEAN})
        assert r.status_code == 200, r.text

        gated = [
            (p['gate'], p['outcome'], p['attempt'])
            for e, p in events
            if e == 'task_result_gated'
        ]
        assert gated == [
            ('ad_changes', 'refused', 1),
            ('ad_changes', 'failed_open', 2),
            ('ad_coverage', 'refused', 1),
            ('ad_changes', 'failed_open', 2),
            ('ad_coverage', 'banner_marked', 2),
        ], gated

        release.set()
        done = await wait_for_task(admin_client, task_id)
        shown = done.get('result') or done.get('submitted_result') or ''
        assert shown.lstrip().startswith('>'), shown[:200]
        assert 'spend disagrees' in shown
        caveats = json.loads(done.get('review_gaps') or '[]')
        assert 'a gap' in caveats and 'spend disagrees' in caveats

    async def test_an_unverified_upload_is_one_round(
        self, admin_client, install_fake_agent
    ):
        """A started upload with no verdicted batch is raised ONCE; the
        next submit ships with it as a caveat instead of looping.

        End to end through the real gate: the session's skill binding
        declares ``upload_verdict``, the upload helper's attempt marker
        arms it, and the refusal is the agent-facing instruction while the
        caveat is the seller-facing item — two different texts.
        """
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Upload Round Store'
        )
        _arm_upload_gate(task_id)
        url = f'/api/tasks/{task_id}/result'
        r = await admin_client.post(url, json={'result': CLEAN})
        assert r.status_code == 400, r.text
        assert 'parse-feedback' in r.text and 'UPLOAD_ATTEMPT' in r.text
        r = await admin_client.post(url, json={'result': CLEAN})
        assert r.status_code == 200, r.text
        gate.set()
        done = await wait_for_task(admin_client, task_id)
        gaps = done.get('review_gaps') or ''
        # The caveat is the one unmet item, not the how-to-fix-it prose.
        assert 'left no verdicted batch' in gaps
        assert 'parse-feedback' not in gaps

    async def test_two_checks_are_one_refusal_listing_both(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        """Two checks finding something on the same submit: one refusal
        naming both — not one check per submit — then through."""

        def gate(name, gap):
            class G:
                @staticmethod
                def check(result, task_id, rules):
                    return GateDeny(gate=name, reason=gap, gaps=(gap,))

            return name, G

        monkeypatch.setattr(
            'app.routers.tasks.resolve_skill_gates',
            lambda loaded, workspace: [
                gate('gate_a', 'first gap'),
                gate('gate_b', 'second gap'),
            ],
        )
        task_id, release = await _running_task(
            admin_client, install_fake_agent, 'Two Checks Store'
        )
        url = f'/api/tasks/{task_id}/result'
        r = await admin_client.post(url, json={'result': CLEAN})
        assert r.status_code == 400
        assert 'first gap' in r.text and 'second gap' in r.text
        r = await admin_client.post(url, json={'result': CLEAN})
        assert r.status_code == 200, r.text
        release.set()
        await wait_for_task(admin_client, task_id)

    async def test_a_run_ending_after_the_refusal_keeps_the_banner(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        """Refused for a contradiction, the agent asks the user instead of
        resubmitting. What ships is the retained report — it must carry
        the warning at the top, not only in the caveats below."""

        class Contradicting:
            @staticmethod
            def check(result, task_id, rules):
                return GateDeny(
                    gate='fake_money',
                    reason='bid set to 11.3, approved 1.20',
                    gaps=('bid set to 11.3, approved 1.20',),
                    contradictions=('bid set to 11.3, approved 1.20',),
                )

        monkeypatch.setattr(
            'app.routers.tasks.resolve_skill_gates',
            lambda loaded, workspace: [('fake_money', Contradicting)],
        )
        task_id, release = await _running_task(
            admin_client, install_fake_agent, 'Banner Kept Store'
        )
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result', json={'result': CLEAN}
        )
        assert r.status_code == 400
        release.set()
        done = await wait_for_task(admin_client, task_id)
        shown = done.get('result') or done.get('submitted_result') or ''
        assert shown.lstrip().startswith('>'), shown[:200]
        assert 'approved 1.20' in shown

    async def test_a_skill_gate_is_one_round(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        """A skill gate refuses once; the next submit goes through with
        its gaps as caveats — never a loop."""
        events = _capture_telemetry(monkeypatch)

        class UnmetGate:
            @staticmethod
            def check(result, task_id, rules):
                return GateDeny(
                    gate='fake_review', reason='unmet', gaps=('a', 'b')
                )

        monkeypatch.setattr(
            'app.routers.tasks.resolve_skill_gates',
            lambda loaded, workspace: [('fake_review', UnmetGate)],
        )
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'One Round Store'
        )
        url = f'/api/tasks/{task_id}/result'
        assert (
            await admin_client.post(url, json={'result': CLEAN})
        ).status_code == 400
        assert (
            await admin_client.post(url, json={'result': CLEAN})
        ).status_code == 200
        gated = [p for e, p in events if e == 'task_result_gated']
        assert [(p['gate'], p['outcome']) for p in gated] == [
            ('custom', 'refused'),
            ('custom', 'failed_open'),
        ]
        gate.set()
        await wait_for_task(admin_client, task_id)
