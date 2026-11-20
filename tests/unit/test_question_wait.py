"""The bounded question wait (app/ai/question_wait.py)."""

import asyncio

import pytest

from app.ai.question_wait import Verdict, verdict, wait_for_answer

pytestmark = pytest.mark.unit


async def test_wait_returns_true_when_answered_before_timeout():
    evt = asyncio.Event()
    asyncio.get_running_loop().call_later(0.05, evt.set)
    assert await wait_for_answer(evt, 1.0) is True


async def test_wait_returns_false_on_timeout():
    assert await wait_for_answer(asyncio.Event(), 0.1) is False


async def test_zero_timeout_waits_forever():
    evt = asyncio.Event()
    waiter = asyncio.create_task(wait_for_answer(evt, 0))
    await asyncio.sleep(0.3)
    assert not waiter.done()
    evt.set()
    assert await waiter is True


def test_verdict_answer_wins_over_timeout():
    assert (
        verdict(answered=True, timed_out=True, stopping=False)
        is Verdict.ANSWERED
    )


def test_verdict_answer_wins_over_stop():
    assert (
        verdict(answered=True, timed_out=False, stopping=True)
        is Verdict.ANSWERED
    )


def test_verdict_stop_wins_over_timeout():
    assert (
        verdict(answered=False, timed_out=True, stopping=True)
        is Verdict.STOPPED
    )


def test_verdict_unanswered_timeout_parks():
    assert (
        verdict(answered=False, timed_out=True, stopping=False)
        is Verdict.TIMED_OUT
    )
