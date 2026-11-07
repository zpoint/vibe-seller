"""Gate tests for the blocking-call checker.

The analyzer itself lives in ``scripts/check_blocking_async.py`` —
runnable standalone (``python scripts/check_blocking_async.py``) and
imported here. This file is the CI gate plus the analyzer's own
self-tests, run on inline source snippets.
"""

import textwrap

import pytest

from scripts.check_blocking_async import (
    OK_COMMENT,
    BlockingChecker,
    Violation,
    load_app_sources,
)

pytestmark = pytest.mark.unit


# -- the gate ----------------------------------------------------


def test_async_functions_do_not_block_the_event_loop() -> None:
    sources, paths = load_app_sources()
    violations = BlockingChecker(sources, paths).violations()
    report = '\n'.join(v.format() for v in violations)
    assert not violations, (
        f'{len(violations)} blocking call(s) on the event loop:\n{report}'
    )


def test_blocking_ok_comments_require_a_reason() -> None:
    sources, paths = load_app_sources()
    bad = []
    for dotted, source in sorted(sources.items()):
        for lineno, line in enumerate(source.splitlines(), 1):
            match = OK_COMMENT.search(line)
            if match is not None and not match.group('reason').strip():
                bad.append(f'{paths[dotted]}:{lineno}')
    assert not bad, f'blocking-ok without a reason: {", ".join(bad)}'


# -- self-tests for the checker ----------------------------------


def _src(text: str) -> str:
    """Snippet with stable line numbers (no leading blank line)."""
    return textwrap.dedent(text).strip('\n') + '\n'


def _check(**sources: str) -> list[Violation]:
    return BlockingChecker(sources).violations()


def test_direct_primitive_call_is_flagged() -> None:
    source = _src(
        """
        import time

        async def run():
            time.sleep(1)
        """
    )
    violations = _check(**{'app.a': source})
    assert len(violations) == 1
    violation = violations[0]
    assert violation.fn_name == 'run'
    assert 'time.sleep' in violation.format()
    assert source.splitlines()[violation.line - 1].strip() == 'time.sleep(1)'


def test_transitive_call_through_sync_helper_is_flagged() -> None:
    helper = _src(
        """
        import os

        def helper():
            for _ in os.walk('.'):
                pass
        """
    )
    main = _src(
        """
        from app.helper import helper

        async def run():
            helper()
        """
    )
    violations = _check(**{'app.helper': helper, 'app.main': main})
    assert len(violations) == 1
    message = violations[0].format().splitlines()[0]
    assert message == (
        'app/main.py:4 async run() -> helper() -> os.walk @app/helper.py:4'
    )


def test_alias_and_local_import_resolve_to_primitives() -> None:
    source = _src(
        """
        async def run(cmd):
            import subprocess as sp
            sp.run(cmd)

        async def other(path):
            from shutil import rmtree
            rmtree(path)
        """
    )
    violations = _check(**{'app.a': source})
    names = {v.chain[-1].name for v in violations}
    assert names == {'subprocess.run', 'shutil.rmtree'}


def test_self_method_chain_is_flagged() -> None:
    source = _src(
        """
        class Worker:
            def _fetch(self):
                import subprocess
                subprocess.run(['true'])

            async def run(self):
                self._fetch()
        """
    )
    violations = _check(**{'app.a': source})
    assert len(violations) == 1
    message = violations[0].format().splitlines()[0]
    assert message == (
        'app/a.py:7 async run() -> self._fetch() -> subprocess.run @app/a.py:4'
    )


def test_super_call_resolves_to_a_base_method() -> None:
    source = _src(
        """
        class Base:
            def _fetch(self):
                import subprocess
                subprocess.run(['true'])

        class Worker(Base):
            async def run(self):
                super()._fetch()
        """
    )
    violations = _check(**{'app.a': source})
    assert len(violations) == 1
    assert 'subprocess.run' in violations[0].format()


def test_blocking_generator_iteration_is_flagged() -> None:
    source = _src(
        """
        def _walk():
            import os
            os.walk('.')
            yield 1

        async def run():
            for _ in _walk():
                pass
        """
    )
    violations = _check(**{'app.a': source})
    assert len(violations) == 1
    assert '_walk()' in violations[0].format()


def test_to_thread_reference_is_not_a_call() -> None:
    source = _src(
        """
        import asyncio

        def helper():
            import shutil
            shutil.rmtree('/tmp/x')

        async def run():
            await asyncio.to_thread(helper, '/tmp/x')
        """
    )
    assert not _check(**{'app.a': source})


def test_nested_def_body_is_not_the_async_fns_call() -> None:
    source = _src(
        """
        async def run():
            def inner():
                import subprocess
                subprocess.run(['true'])
            return inner
        """
    )
    assert not _check(**{'app.a': source})


def test_nested_def_under_control_flow_is_tracked() -> None:
    source = _src(
        """
        async def run(flag):
            if flag:
                def helper():
                    import subprocess
                    subprocess.run(['true'])
                helper()
        """
    )
    violations = _check(**{'app.a': source})
    assert len(violations) == 1
    message = violations[0].format()
    assert 'helper()' in message
    assert 'subprocess.run' in message


def test_unresolved_name_collision_is_not_flagged() -> None:
    source = _src(
        """
        class Box:
            def remove(self):
                import os
                os.walk('.')

        async def run(box):
            box.remove()
            check()
        """
    )
    # ``box.remove`` runs on a local variable and ``check`` is not
    # imported at all: neither may be mistaken for a blocking helper
    # just because some other module defines one by that name.
    assert not _check(**{'app.a': source, 'app.other': 'def check(): pass'})


def test_eager_call_inside_to_thread_is_still_flagged() -> None:
    source = _src(
        """
        import asyncio

        def helper():
            import time
            time.sleep(1)

        async def run():
            await asyncio.to_thread(helper())
        """
    )
    violations = _check(**{'app.a': source})
    assert len(violations) == 1
    assert 'time.sleep' in violations[0].format()


def test_blocking_ok_comment_is_honored() -> None:
    source = _src(
        """
        async def run():
            import time
            time.sleep(0.01)  # blocking-ok: startup pause before serving
        """
    )
    assert not _check(**{'app.a': source})


def test_empty_blocking_ok_reason_is_rejected() -> None:
    source = _src(
        """
        async def run():
            import time
            time.sleep(0.01)  # blocking-ok:
        """
    )
    checker = BlockingChecker({'app.a': source})
    violations = checker.violations()
    assert len(violations) == 1
    assert 'time.sleep' in violations[0].format()
    assert checker.invalid_hatches == ['app/a.py:3']
