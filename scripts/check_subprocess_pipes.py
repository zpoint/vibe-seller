"""Gate: no ``wait()`` on a subprocess whose pipes nobody reads.

A child started with ``stdout=PIPE``/``stderr=PIPE`` blocks in
``write()`` the moment it fills a pipe buffer that nobody drains.  If
the parent only ``await proc.wait()``s, that wait never returns -- and
whatever lock the caller holds is held forever.  That is how
``app/workspace/manager.py::_run_git`` wedged every later store create
(on Windows, ``git add -A`` prints one LF->CRLF warning per file, more
than a pipe holds); ``app/workspace/git_run.py`` drains with
``communicate()`` instead.

This is the static gate for that bug class.  Per function it finds a
process spawned by ``asyncio.create_subprocess_exec``/``_shell`` or
``subprocess.Popen`` with ``stdout=``/``stderr=`` set to PIPE, and a
``wait()`` on it that no earlier line drained.  Draining a stream means

* ``proc.communicate()`` -- also inside ``asyncio.wait_for(...)``;
* a read of the stream itself: ``proc.stdout.read()``, ``async for ...
  in proc.stdout``, or handing ``proc.stdout`` to a reader task;
* handing ``proc`` to a call whose name says it reads output
  (``_pump(proc)``, ``collect_output(proc)``, ...).

Not a violation: a ``wait()`` after ``kill()``/``terminate()`` in an
error path while ``communicate()`` is the function's normal path, and a
stream pointed at DEVNULL / a file / ``stdout`` (only PIPE needs a
reader).  ``stderr=STDOUT`` with ``stdout=PIPE`` still needs stdout
read.  The scan is per function and heuristic on purpose: it exists to
catch the shape above, not to model every process tree.

Escape hatch: ``# pipe-ok: <reason>`` on the spawn or the ``wait()``
line (the reason is part of the contract).  Nothing else is allowlisted.

Run standalone (whole ``app/`` tree, or just the files named):

    python scripts/check_subprocess_pipes.py [file.py ...]

prints every violation (exit 1) or confirms the tree is clean.
"""

import ast
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / 'app'
# Agent-side scripts: copied into the task workspace and run as their
# own processes, never on the server's event loop.
EXCLUDED_DIRS = ('skills', 'skills_v2', 'knowledge')

OK_COMMENT = re.compile(r'#\s*pipe-ok:\s*(?P<reason>.*)')

HINT = (
    'hint: drain the pipes before waiting -- await proc.communicate(), '
    'or read stdout/stderr; a child that fills an unread pipe blocks '
    'and wait() never returns'
)

# Spawners that hand US the pipe to read.
SPAWNERS = frozenset({
    'asyncio.create_subprocess_exec',
    'asyncio.create_subprocess_shell',
    'asyncio.subprocess.create_subprocess_exec',
    'asyncio.subprocess.create_subprocess_shell',
    'subprocess.Popen',
})

STREAMS = ('stdout', 'stderr')

FuncNode = ast.FunctionDef | ast.AsyncFunctionDef

WAIT_METHODS = frozenset({'wait'})
KILL_METHODS = frozenset({'kill', 'terminate', 'send_signal'})

# A call whose name says it reads a child's output: handing the process
# (or a stream of it) to one of those counts as draining.
READER_HINT = re.compile(
    r'read|drain|pump|consume|stream|forward|collect|capture|tee|relay'
)


@dataclass(frozen=True)
class Spawn:
    """One piped child, and the name it is bound to."""

    var: str
    streams: tuple[str, ...]
    node: ast.Call


@dataclass(frozen=True)
class Drain:
    """Something that empties a pipe.  ``stream`` None = all of them."""

    var: str
    stream: str | None
    line: int
    communicate: bool = False


@dataclass(frozen=True)
class Wait:
    """A ``wait()`` on a tracked child."""

    var: str
    line: int
    node: ast.Call


@dataclass(frozen=True)
class Kill:
    """A ``kill()``/``terminate()``/``send_signal()`` on a child."""

    var: str
    line: int


@dataclass(frozen=True)
class Violation:
    """A ``wait()`` that can block forever on an unread pipe."""

    path: str
    line: int
    fn_name: str
    var: str
    streams: tuple[str, ...]

    def format(self) -> str:
        """Render ``path:line`` plus a one-line reason."""
        piped = ', '.join(f'{s}=PIPE' for s in self.streams)
        return (
            f'{self.path}:{self.line} {self.fn_name}(): wait() on '
            f'{self.var} with {piped} that nothing read first\n'
            f'  {HINT}'
        )


def _dotted(expr: ast.expr, bindings: dict[str, str]) -> str | None:
    """Resolve ``a.b.c`` to a dotted name through the module's imports."""
    parts: list[str] = []
    node = expr
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.reverse()
    root = bindings.get(node.id, node.id)
    return '.'.join([root, *parts])


def _collect_bindings(tree: ast.Module) -> dict[str, str]:
    """Every imported name in the module, to its dotted target.

    Function-level imports count too: a local ``import subprocess``
    binds the same primitive.  Shadowing is not modelled -- a binding is
    only ever a hint towards a spawner name.
    """
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bindings[alias.asname] = alias.name
                else:
                    top = alias.name.split('.')[0]
                    bindings[top] = top
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == '*' or not node.module:
                    continue
                prefix = '.' * node.level + node.module
                name = alias.asname or alias.name
                bindings[name] = f'{prefix}.{alias.name}'
    return bindings


def _unawait(expr: ast.expr) -> ast.expr:
    return expr.value if isinstance(expr, ast.Await) else expr


def _piped_streams(call: ast.Call) -> tuple[str, ...]:
    """The ``stdout=``/``stderr=`` kwargs of *call* that are a PIPE."""
    piped: list[str] = []
    for kw in call.keywords:
        if kw.arg not in STREAMS or not _is_pipe(kw.value):
            continue
        piped.append(kw.arg)
    return tuple(s for s in STREAMS if s in piped)


def _is_pipe(expr: ast.expr) -> bool:
    """True for ``PIPE``, ``subprocess.PIPE``, ``asyncio.subprocess.PIPE``."""
    if isinstance(expr, ast.Name):
        return expr.id == 'PIPE'
    if isinstance(expr, ast.Attribute):
        return expr.attr == 'PIPE'
    return False


def _spawn_targets(scope: ast.AST) -> dict[int, str]:
    """Map each spawn call in *scope* to the name it is bound to."""
    targets: dict[int, str] = {}

    def bind(value: ast.expr | None, var: ast.expr | None) -> None:
        if value is None or var is None:
            return
        inner = _unawait(value)
        if isinstance(inner, ast.Call):
            targets[id(inner)] = ast.unparse(var)

    for node in ast.walk(scope):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            bind(node.value, node.targets[0])
        elif isinstance(node, ast.AnnAssign):
            bind(node.value, node.target)
        elif isinstance(node, ast.With | ast.AsyncWith):
            for item in node.items:
                bind(item.context_expr, item.optional_vars)
    return targets


def _iter_nodes(
    node: ast.AST, nested: bool = False
) -> Generator[tuple[ast.AST, bool], None, None]:
    """Every node under *node*, flagging the ones in a nested scope.

    A nested ``def``/``lambda`` is its own function and is scanned on
    its own; its body still counts as a READER of the enclosing
    function's pipes (``async def pump(): async for x in proc.stdout``),
    so the caller decides what to do with ``nested``.
    """
    for child in ast.iter_child_nodes(node):
        scope = isinstance(
            child,
            ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda,
        )
        yield child, nested or scope
        yield from _iter_nodes(child, nested or scope)


def _iter_functions(tree: ast.AST) -> Generator[FuncNode, None, None]:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node


def _callee_names(call: ast.Call) -> set[str]:
    """Names called anywhere inside *call* (``create_task(_pump(x))``)."""
    return {
        ast.unparse(sub.func).split('.')[-1].lower()
        for sub in ast.walk(call)
        if isinstance(sub, ast.Call)
    }


def _arg_vars(call: ast.Call, known: frozenset[str]) -> set[str]:
    """Tracked names passed to *call* as an argument."""
    found: set[str] = set()
    for arg in list(call.args) + [kw.value for kw in call.keywords]:
        for sub in ast.walk(arg):
            if isinstance(sub, ast.Name | ast.Attribute):
                name = ast.unparse(sub)
                if name in known:
                    found.add(name)
    return found


class PipeChecker:
    """Per-function scan for piped children that ``wait()`` undrained.

    ``sources`` maps a repo-relative path to its text, so the same
    checker runs over ``app/`` (the CI gate) and over inline snippets
    (the self-tests in tests/unit/test_subprocess_pipes_drained.py).
    """

    def __init__(self, sources: dict[str, str]) -> None:
        self.sources = dict(sources)
        self.invalid_hatches: list[str] = []
        self._violations: list[Violation] = []
        self._scan()
        self._violations.sort(key=lambda v: (v.path, v.line, v.fn_name))
        self.invalid_hatches = sorted(set(self.invalid_hatches))

    def violations(self) -> list[Violation]:
        """Every ``wait()`` on a pipe nothing drained first."""
        return list(self._violations)

    # -- scanning -------------------------------------------------

    def _scan(self) -> None:
        for path in sorted(self.sources):
            tree = ast.parse(self.sources[path])
            bindings = _collect_bindings(tree)
            for fn in _iter_functions(tree):
                self._check_function(path, fn, bindings)

    def _check_function(
        self, path: str, fn: FuncNode, bindings: dict[str, str]
    ) -> None:
        source = self.sources[path]
        targets = _spawn_targets(fn)
        spawns: dict[str, Spawn] = {}
        drains: list[Drain] = []
        waits: list[Wait] = []
        kills: list[Kill] = []

        for node, nested in _iter_nodes(fn):
            if not isinstance(node, ast.Call) or nested:
                continue
            if _dotted(node.func, bindings) in SPAWNERS:
                var = targets.get(id(node))
                piped = _piped_streams(node)
                if var is not None and piped:
                    spawns[var] = Spawn(var, piped, node)
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            var = ast.unparse(node.func.value)
            if var not in spawns:
                continue
            if node.func.attr in WAIT_METHODS:
                waits.append(Wait(var, node.lineno, node))
            elif node.func.attr in KILL_METHODS:
                kills.append(Kill(var, node.lineno))
            elif node.func.attr == 'communicate':
                drains.append(Drain(var, None, node.lineno, True))

        if not spawns:
            return
        known = frozenset(spawns)
        for node, _ in _iter_nodes(fn):
            # Stream reads count from a nested scope too: a reader
            # coroutine defined inline still empties the pipe.
            if isinstance(node, ast.Attribute) and node.attr in STREAMS:
                var = ast.unparse(node.value)
                if var in known and isinstance(node.ctx, ast.Load):
                    drains.append(Drain(var, node.attr, node.lineno))
            elif isinstance(node, ast.Call):
                names = _callee_names(node)
                if not any(READER_HINT.search(n) for n in names):
                    continue
                for var in _arg_vars(node, known):
                    drains.append(Drain(var, None, node.lineno, True))

        for wait in waits:
            self._judge(path, source, fn, spawns[wait.var], wait, drains, kills)

    def _judge(
        self,
        path: str,
        source: str,
        fn: FuncNode,
        spawn: Spawn,
        wait: Wait,
        drains: list[Drain],
        kills: list[Kill],
    ) -> None:
        """Decide whether one ``wait()`` left a pipe unread."""
        mine = [d for d in drains if d.var == wait.var]
        unread = tuple(
            s
            for s in spawn.streams
            if not any(
                d.line < wait.line and d.stream in (None, s) for d in mine
            )
        )
        if not unread:
            return
        killed = any(k.line < wait.line for k in kills if k.var == wait.var)
        if killed and any(d.communicate for d in mine):
            # Error/timeout path: killed, then reaped.  communicate() is
            # the normal path, so the pipes are drained there.
            return
        hatched = self._has_ok(path, source, spawn.node) or self._has_ok(
            path, source, wait.node
        )
        if hatched:
            return
        self._violations.append(
            Violation(
                path=path,
                line=wait.line,
                fn_name=fn.name,
                var=wait.var,
                streams=unread,
            )
        )

    def _has_ok(self, path: str, source: str, node: ast.AST) -> bool:
        """True when *node*'s lines carry a valid ``# pipe-ok:``."""
        lines = source.splitlines()
        last = getattr(node, 'end_lineno', None) or node.lineno
        for lineno in range(node.lineno, last + 1):
            if lineno > len(lines):
                break
            match = OK_COMMENT.search(lines[lineno - 1])
            if match is None:
                continue
            if match.group('reason').strip():
                return True
            self.invalid_hatches.append(f'{path}:{lineno}')
            return False
        return False


def load_app_sources() -> dict[str, str]:
    """Every server-side ``app/`` module, by repo-relative path."""
    sources: dict[str, str] = {}
    for path in sorted(APP_ROOT.rglob('*.py')):
        rel = path.relative_to(REPO_ROOT)
        if set(EXCLUDED_DIRS).intersection(rel.parts):
            continue
        sources[rel.as_posix()] = path.read_text()
    return sources


def load_sources(files: list[str]) -> dict[str, str]:
    """The named ``app/`` python files, by repo-relative path."""
    sources: dict[str, str] = {}
    for raw in files:
        path = Path(raw)
        if not path.is_absolute():
            path = REPO_ROOT / path
        try:
            rel = path.resolve().relative_to(REPO_ROOT)
        except ValueError:
            continue
        if rel.suffix != '.py' or not path.is_file():
            continue
        if set(EXCLUDED_DIRS).intersection(rel.parts):
            continue
        sources[rel.as_posix()] = path.read_text()
    return sources


def main(argv: list[str] | None = None) -> int:
    """Scan ``app/`` (or *argv*) and report every violation."""
    args = list(sys.argv[1:] if argv is None else argv)
    sources = load_sources(args) if args else load_app_sources()
    checker = PipeChecker(sources)
    violations = checker.violations()
    for violation in violations:
        print(violation.format())
    if violations:
        print(f'{len(violations)} wait() on a piped subprocess nothing reads')
        return 1
    print('every piped subprocess is drained before wait()')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
