"""Which runs may wait on a person (app/ai/question_policy.py)."""

from types import SimpleNamespace

import pytest

from app.ai.question_policy import (
    agent_wait_condition,
    may_wait_on_a_person,
    policy_for,
    waits_on_a_person,
)

pytestmark = pytest.mark.unit


def _task(schedule_id=None, is_plan_only=False):
    return SimpleNamespace(schedule_id=schedule_id, is_plan_only=is_plan_only)


def test_scheduled_fire_declines():
    assert policy_for(_task(schedule_id='s1')) == 'decline'


def test_schedule_plan_authoring_run_may_ask():
    assert policy_for(_task(schedule_id='s1', is_plan_only=True)) == 'ask'


def test_task_without_schedule_may_ask():
    assert policy_for(_task()) == 'ask'


def test_missing_row_may_ask():
    assert policy_for(None) == 'ask'


MANUAL_WAIT = (
    'Done what I could.\n\n```wait-condition\n'
    'reason: waiting for your choice of shipping option\n'
    'check_strategy: manual\n```'
)
EMAIL_WAIT = (
    'Asked the carrier.\n\n```wait-condition\n'
    'reason: carrier reply\ncheck_strategy: email\nkeywords: ORD-1001\n```'
)


def test_scheduled_fire_may_not_wait_on_a_person():
    assert may_wait_on_a_person(_task(schedule_id='s1')) is False
    assert may_wait_on_a_person(_task(schedule_id='s1', is_plan_only=True))
    assert may_wait_on_a_person(_task())


def test_manual_wait_is_a_wait_on_a_person():
    assert waits_on_a_person({'check_strategy': 'manual'})
    assert not waits_on_a_person({})
    assert waits_on_a_person({'reason': 'x'})
    assert not waits_on_a_person({'check_strategy': 'email'})


def test_scheduled_fire_drops_its_wait_for_a_person():
    task = SimpleNamespace(id='t' * 8, schedule_id='s1', is_plan_only=False)
    assert agent_wait_condition(task, MANUAL_WAIT) is None


def test_scheduled_fire_keeps_its_wait_on_an_outside_event():
    task = SimpleNamespace(id='t' * 8, schedule_id='s1', is_plan_only=False)
    cond = agent_wait_condition(task, EMAIL_WAIT)
    assert cond['check_strategy'] == 'email'


def test_attended_task_keeps_its_wait_for_a_person():
    task = SimpleNamespace(id='t' * 8, schedule_id=None, is_plan_only=False)
    assert agent_wait_condition(task, MANUAL_WAIT)['check_strategy'] == (
        'manual'
    )
