"""Gate tests for the unread-pipe checker.

The analyzer itself lives in ``scripts/check_subprocess_pipes.py`` —
runnable standalone (``python scripts/check_subprocess_pipes.py``) and
imported here. This file is the CI gate plus the analyzer's own
self-tests, run on inline source snippets.
"""

import textwrap

import pytest

from scripts.check_subprocess_pipes import (
    OK_COMMENT,
    PipeChecker,
    Violation,
    load_app_sources,
)

pytestmark = pytest.mark.unit


# -- the gate ----------------------------------------------------


def test_app_tree_drains_every_piped_subprocess() -> None:
    checker = PipeChecker(load_app_sources())
    violations = checker.violations()
    report = '\n'.join(v.format() for v in violations)
    assert not violations, (
        f'{len(violations)} wait() on a pipe nothing reads:\n{report}'
    )


def test_pipe_ok_comments_require_a_reason() -> None:
    bad = []
    for path, source in sorted(load_app_sources().items()):
        for lineno, line in enumerate(source.splitlines(), 1):
            match = OK_COMMENT.search(line)
            if match is not None and not match.group('reason').strip():
                bad.append(f'{path}:{lineno}')
    assert not bad, f'pipe-ok without a reason: {", ".join(bad)}'


# -- self-tests for the checker ----------------------------------


def _src(text: str) -> str:
    """Snippet with stable line numbers (no leading blank line)."""
    return textwrap.dedent(text).strip('\n') + '\n'


def _check(sources: dict[str, str]) -> list[Violation]:
    return PipeChecker(sources).violations()


def test_the_old_run_git_shape_is_flagged() -> None:
    """The bug this gate exists for, verbatim.

    ``app/workspace/manager.py::_run_git`` before c9ccbec4: git started
    with both pipes, ``await proc.wait()``, and the only read of stderr
    AFTER the wait -- in the failure branch. On Windows ``git add -A``
    prints one LF->CRLF warning per file, more than a pipe buffer
    holds, so git blocked writing, ``wait()`` never returned, and the
    workspace git lock was held forever.
    """
    source = _src(
        """
        import asyncio


        class WorkspaceManager:
            async def _run_git(self, *args, check: bool = True):
                async with self._git_lock:
                    proc = await asyncio.create_subprocess_exec(
                        'git',
                        *args,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await proc.wait()
                    if check and proc.returncode != 0:
                        err = (await proc.stderr.read()).decode()
                        raise RuntimeError(err)
                    return proc
        """
    )
    violations = _check({'app/workspace/manager.py': source})
    assert len(violations) == 1
    violation = violations[0]
    assert violation.fn_name == '_run_git'
    assert violation.streams == ('stdout', 'stderr')
    assert 'app/workspace/manager.py' in violation.format()
    assert source.splitlines()[violation.line - 1].strip() == (
        'await proc.wait()'
    )


def test_communicate_drains_both_pipes() -> None:
    source = _src(
        """
        import asyncio

        async def run_git(*args):
            proc = await asyncio.create_subprocess_exec(
                'git',
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await proc.communicate()
            return proc.returncode, err
        """
    )
    assert not _check({'app/a.py': source})


def test_communicate_inside_wait_for_drains_the_pipes() -> None:
    source = _src(
        """
        import asyncio

        async def run(cmd):
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE
            )
            out = await asyncio.wait_for(proc.communicate(), 30)
            await proc.wait()
            return out
        """
    )
    assert not _check({'app/a.py': source})


def test_wait_after_kill_on_a_communicate_path_is_allowed() -> None:
    source = _src(
        """
        import asyncio

        async def run(cmd, gave_up):
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            if gave_up:
                proc.kill()
                await proc.wait()
                return None
            return await proc.communicate()
        """
    )
    assert not _check({'app/a.py': source})


def test_wait_after_kill_with_no_drain_anywhere_is_flagged() -> None:
    source = _src(
        """
        import asyncio

        async def stop(cmd):
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE
            )
            proc.terminate()
            await proc.wait()
        """
    )
    violations = _check({'app/a.py': source})
    assert len(violations) == 1
    assert violations[0].streams == ('stdout',)


def test_a_reader_task_on_the_stream_drains_it() -> None:
    source = _src(
        """
        import asyncio

        async def run(cmd):
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE
            )
            pump = asyncio.create_task(_read_all(proc.stdout))
            await proc.wait()
            return await pump
        """
    )
    assert not _check({'app/a.py': source})


def test_a_nested_reader_coroutine_drains_it() -> None:
    source = _src(
        """
        import asyncio

        async def run(cmd):
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE
            )

            async def pump():
                async for _ in proc.stdout:
                    pass

            task = asyncio.create_task(pump())
            await proc.wait()
            await task
        """
    )
    assert not _check({'app/a.py': source})


def test_handing_the_process_to_a_reader_drains_it() -> None:
    source = _src(
        """
        import subprocess

        def run(cmd):
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
            _pump_output(proc)
            proc.wait()
        """
    )
    assert not _check({'app/a.py': source})


def test_reading_a_stream_before_wait_is_enough_for_that_stream() -> None:
    source = _src(
        """
        import subprocess

        def run(cmd):
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            body = proc.stdout.read()
            proc.wait()
            return body
        """
    )
    assert not _check({'app/a.py': source})


def test_only_the_stream_nobody_read_is_reported() -> None:
    source = _src(
        """
        import asyncio

        async def run(cmd):
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out = await proc.stdout.read()
            await proc.wait()
            return out
        """
    )
    violations = _check({'app/a.py': source})
    assert len(violations) == 1
    assert violations[0].streams == ('stderr',)


def test_devnull_and_file_handles_need_no_reader() -> None:
    source = _src(
        """
        import subprocess

        def run(cmd, log):
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=log
            )
            proc.wait()
        """
    )
    assert not _check({'app/a.py': source})


def test_stderr_folded_into_stdout_still_needs_stdout_read() -> None:
    source = _src(
        """
        import subprocess

        def run(cmd):
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )
            proc.wait()
        """
    )
    violations = _check({'app/a.py': source})
    assert len(violations) == 1
    assert violations[0].streams == ('stdout',)


def test_from_imports_and_aliases_resolve() -> None:
    source = _src(
        """
        from subprocess import PIPE, Popen

        def run(cmd):
            proc = Popen(cmd, stdout=PIPE)
            proc.wait()

        async def other(cmd):
            import asyncio as aio
            proc = await aio.create_subprocess_shell(
                cmd, stderr=aio.subprocess.PIPE
            )
            await proc.wait()
        """
    )
    violations = _check({'app/a.py': source})
    assert [(v.fn_name, v.streams) for v in violations] == [
        ('run', ('stdout',)),
        ('other', ('stderr',)),
    ]


def test_an_unbound_or_unwaited_child_is_not_reported() -> None:
    source = _src(
        """
        import asyncio
        import subprocess

        async def run(cmd):
            await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE
            )

        def tail(cmd):
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
            proc.kill()
        """
    )
    assert not _check({'app/a.py': source})


def test_a_context_manager_child_is_tracked() -> None:
    source = _src(
        """
        import subprocess

        def run(cmd):
            with subprocess.Popen(cmd, stderr=subprocess.PIPE) as proc:
                proc.wait()
        """
    )
    violations = _check({'app/a.py': source})
    assert len(violations) == 1
    assert violations[0].var == 'proc'


def test_pipe_ok_on_the_wait_line_is_honored() -> None:
    source = _src(
        """
        import asyncio

        async def run(cmd):
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE
            )
            await proc.wait()  # pipe-ok: a reader task owns the stream
        """
    )
    assert not _check({'app/a.py': source})


def test_pipe_ok_on_the_spawn_line_is_honored() -> None:
    source = _src(
        """
        import asyncio

        async def run(cmd):
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,  # pipe-ok: drained by caller
            )
            await proc.wait()
        """
    )
    assert not _check({'app/a.py': source})


def test_empty_pipe_ok_reason_is_rejected() -> None:
    source = _src(
        """
        import subprocess

        def run(cmd):
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
            proc.wait()  # pipe-ok:
        """
    )
    checker = PipeChecker({'app/a.py': source})
    violations = checker.violations()
    assert len(violations) == 1
    assert checker.invalid_hatches == ['app/a.py:5']


def test_an_unrelated_wait_is_not_reported() -> None:
    source = _src(
        """
        import asyncio

        async def run(cmd):
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.wait()
            await asyncio.sleep(0)
        """
    )
    assert not _check({'app/a.py': source})
