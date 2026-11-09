"""Gate: no blocking I/O inside ``async def`` bodies.

Every ``async def`` in ``app/`` runs on one shared event loop.  A
single blocking call there -- a filesystem walk, a ``subprocess.run``,
a sync HTTP call, a ``sqlite3`` connection -- freezes every SSE stream
and request until it returns.  FastAPI's documented remedies are a
plain ``def`` route (run in the threadpool) or
``asyncio.to_thread`` / ``starlette.concurrency.run_in_threadpool``.

Two gates enforce that:

* ruff ``ASYNC`` (flake8-async), enabled in ``pyproject.toml``: sees
  DIRECT blocking calls (``open``, ``Popen``) inside ``async def``.
* this file: a transitive call-graph check.  ruff ``ASYNC`` cannot
  follow ``async def -> sync helper -> shutil.rmtree`` and knows
  nothing of ``shutil``/``os.walk``/``zipfile``/``sqlite3``, so the
  checker resolves calls through import aliases down to blocking
  primitives and reports the whole chain.

Calls are resolved properly, not by bare name: ``f()`` resolves to a
same-module function or an ``from app.x import f`` alias (relative
imports included), ``mod.f()`` through ``import app.x as mod``, and
``self.f()``/``cls.f()``/``super().f()`` to a method of the enclosing
class or its bases.  A call that cannot be resolved is ignored -- a
local ``remove``/``check``/``connect`` is never mistaken for a
primitive.

Escape hatch: ``# blocking-ok: <reason>`` on the call line (reason is
part of the contract).  Nothing else is allowlisted: the gate is only
worth having while the count is zero.

Run standalone:

    python scripts/check_blocking_async.py

prints every violation (exit 1) or confirms the tree is clean.
"""

import ast
from dataclasses import dataclass, field
from pathlib import Path
import re

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / 'app'
# Agent-side scripts: copied into the task workspace and run as their
# own processes, never on the server's event loop.
EXCLUDED_DIRS = ('skills', 'skills_v2', 'knowledge')

OK_COMMENT = re.compile(r'#\s*blocking-ok:\s*(?P<reason>.*)')

HINT = (
    'hint: wrap the blocking work in asyncio.to_thread(...) '
    'or make the route a plain def'
)

# Blocking primitives, by full dotted name (resolved through import
# aliases).  Cheap metadata ops (exists/mkdir/stat/read_text) are
# deliberately left out: negligible cost, huge noise.  Writes are the
# other side of that line -- Path.write_text is caught through the
# bulk-I/O special case in BlockingChecker.resolve (its size is the
# caller's payload, not the tree's metadata).
PRIMITIVES = frozenset({
    'subprocess.run',
    'subprocess.call',
    'subprocess.check_call',
    'subprocess.check_output',
    'subprocess.Popen',
    'os.system',
    'os.walk',
    'time.sleep',
    'urllib.request.urlopen',
    'sqlite3.connect',
    'shutil.rmtree',
    'shutil.copytree',
    'shutil.move',
    'shutil.make_archive',
    'zipfile.ZipFile',
    'tarfile.open',
    'httpx.get',
    'httpx.post',
    'httpx.put',
    'httpx.delete',
    'httpx.request',
    'httpx.Client',
})

# Families matched by module + attribute pattern instead of an exact
# name (``requests.*`` has no closed member list).
PRIMITIVE_WILDCARDS = (
    ('requests', lambda attr: True),
    ('smtplib', lambda attr: attr.startswith('SMTP')),
    ('imaplib', lambda attr: attr.startswith('IMAP4')),
)


def match_primitive(dotted: str) -> str | None:
    """Return ``dotted`` when it names a blocking primitive."""
    if dotted in PRIMITIVES:
        return dotted
    module, sep, attr = dotted.rpartition('.')
    if not sep or '.' in module:
        return None
    for mod, matches in PRIMITIVE_WILDCARDS:
        if module == mod and matches(attr):
            return dotted
    return None


def _flatten(expr: ast.expr) -> tuple[str | None, list[str]]:
    """Split ``a.b.c`` into (root name, attribute chain)."""
    attrs: list[str] = []
    while isinstance(expr, ast.Attribute):
        attrs.append(expr.attr)
        expr = expr.value
    if isinstance(expr, ast.Name):
        return expr.id, list(reversed(attrs))
    return None, []


def _abs_module(
    parts: list[str], is_package: bool, level: int, module: str | None
) -> str | None:
    """Resolve a (possibly relative) import to a dotted name."""
    if level == 0:
        return module
    base = list(parts) if is_package else list(parts[:-1])
    up = level - 1
    if up > len(base):
        return None
    if up:
        base = base[: len(base) - up]
    if module:
        base.extend(module.split('.'))
    return '.'.join(base)


@dataclass(frozen=True)
class Key:
    """Identity of a scanned function or class."""

    module: str
    qualname: str


@dataclass(frozen=True)
class Target:
    """What a call expression resolves to."""

    kind: str  # 'prim' | 'func' | 'class'
    name: str
    key: Key | None = None


@dataclass
class Hop:
    """One link of a blocking chain: what was called, and where."""

    name: str
    path: str
    line: int


@dataclass
class Violation:
    """An ``async def`` that reaches blocking work, with its chain."""

    path: str
    line: int
    fn_name: str
    chain: tuple[Hop, ...]

    def format(self) -> str:
        """Render ``path:line async fn() -> helper() -> prim @p:l``."""
        parts = [f'{self.path}:{self.line} async {self.fn_name}()']
        parts.extend(f'{hop.name}()' for hop in self.chain[:-1])
        last = self.chain[-1]
        parts.append(f'{last.name} @{last.path}:{last.line}')
        return ' -> '.join(parts) + f'\n  {HINT}'


@dataclass
class CallSite:
    """One call inside a function body, with its resolution."""

    line: int
    display: str
    target: Target | None


@dataclass
class FuncInfo:
    """A scanned ``def``/``async def``."""

    key: Key
    name: str
    node: ast.AST
    is_async: bool
    class_qualname: str | None
    assigned: frozenset[str]
    imports: dict[str, tuple[str, str]]
    calls: list[CallSite] = field(default_factory=list)


@dataclass
class ClassInfo:
    """A scanned class: its methods, nested classes and bases."""

    key: Key
    node: ast.ClassDef
    methods: dict[str, Key] = field(default_factory=dict)
    nested: dict[str, Key] = field(default_factory=dict)
    bases: list[ast.expr] = field(default_factory=list)


@dataclass
class ModuleInfo:
    """One scanned module: source, AST and top-level import bindings."""

    dotted: str
    path: str
    source: str
    tree: ast.Module
    is_package: bool
    bindings: dict[str, tuple[str, str]]


class BlockingChecker:
    """Transitive blocking-call analysis over a set of modules.

    ``sources`` maps dotted module name to source text, so the same
    checker runs over ``app/`` (the CI gate) and over inline snippets
    (the self-tests below).
    """

    def __init__(
        self,
        sources: dict[str, str],
        paths: dict[str, str] | None = None,
    ) -> None:
        self.sources = dict(sources)
        self.paths = paths or {
            dotted: dotted.replace('.', '/') + '.py' for dotted in sources
        }
        self.modules: dict[str, ModuleInfo] = {}
        self.funcs: dict[Key, FuncInfo] = {}
        self.funcs_by_name: dict[tuple[str, str], list[Key]] = {}
        self.classes: dict[Key, ClassInfo] = {}
        self.classes_by_name: dict[tuple[str, str], list[Key]] = {}
        self.blocking: dict[Key, tuple[Hop, ...]] = {}
        self.invalid_hatches: list[str] = []
        self._parse()
        self._collect_defs()
        self._collect_calls()
        self._compute_blocking()

    # -- indexing -------------------------------------------------

    def _parse(self) -> None:
        for dotted, source in self.sources.items():
            tree = ast.parse(source)
            parts = dotted.split('.')
            is_package = self.paths[dotted].endswith('__init__.py')
            bindings: dict[str, tuple[str, str]] = {}
            for node in tree.body:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.asname:
                            bindings[alias.asname] = ('module', alias.name)
                        else:
                            top = alias.name.split('.')[0]
                            bindings[top] = ('module', top)
                elif isinstance(node, ast.ImportFrom):
                    mod = _abs_module(
                        parts, is_package, node.level, node.module
                    )
                    if mod is None:
                        continue
                    for alias in node.names:
                        if alias.name == '*':
                            continue
                        name = alias.asname or alias.name
                        bindings[name] = ('attr', f'{mod}.{alias.name}')
            self.modules[dotted] = ModuleInfo(
                dotted=dotted,
                path=self.paths[dotted],
                source=source,
                tree=tree,
                is_package=is_package,
                bindings=bindings,
            )

    def _collect_defs(self) -> None:
        for mod in self.modules.values():
            self._walk_defs(mod, mod.tree.body, '', None)

    def _walk_defs(
        self,
        mod: ModuleInfo,
        body: list[ast.stmt],
        prefix: str,
        class_qname: str | None,
    ) -> None:
        for node in self._scope_defs(body):
            if isinstance(node, ast.ClassDef):
                qname = self._qualname(prefix, node.name)
                info = ClassInfo(key=Key(mod.dotted, qname), node=node)
                for sub in self._scope_defs(node.body):
                    if isinstance(sub, ast.ClassDef):
                        info.nested[sub.name] = Key(
                            mod.dotted, self._qualname(qname, sub.name)
                        )
                    elif isinstance(
                        sub, ast.FunctionDef | ast.AsyncFunctionDef
                    ):
                        info.methods[sub.name] = Key(
                            mod.dotted, self._qualname(qname, sub.name)
                        )
                info.bases = list(node.bases)
                self.classes[info.key] = info
                self.classes_by_name.setdefault(
                    (mod.dotted, node.name), []
                ).append(info.key)
                self._walk_defs(mod, node.body, qname, qname)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                qname = self._qualname(prefix, node.name)
                assigned, imports = self._scope_names(mod, node.body)
                info = FuncInfo(
                    key=Key(mod.dotted, qname),
                    name=node.name,
                    node=node,
                    is_async=isinstance(node, ast.AsyncFunctionDef),
                    class_qualname=class_qname,
                    assigned=assigned,
                    imports=imports,
                )
                self.funcs[info.key] = info
                self.funcs_by_name.setdefault(
                    (mod.dotted, node.name), []
                ).append(info.key)
                self._walk_defs(mod, node.body, qname, class_qname)

    @staticmethod
    def _scope_defs(body: list[ast.stmt]) -> list[ast.stmt]:
        """``def``/``class`` statements of one scope.

        A nested ``def`` under an ``if``/``try``/``with`` still belongs
        to the enclosing scope, so control-flow bodies are searched
        too -- only nested scopes stop the walk.
        """
        found: list[ast.stmt] = []
        stack: list[ast.AST] = list(body)
        while stack:
            node = stack.pop()
            if isinstance(
                node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            ):
                found.append(node)
                continue
            if isinstance(node, ast.Lambda):
                continue
            for attr in ('body', 'orelse', 'finalbody', 'handlers'):
                stack.extend(getattr(node, attr, None) or [])
        found.reverse()
        return found

    @staticmethod
    def _qualname(prefix: str, name: str) -> str:
        return f'{prefix}.{name}' if prefix else name

    def _scope_names(
        self, mod: ModuleInfo, body: list[ast.stmt]
    ) -> tuple[frozenset[str], dict[str, tuple[str, str]]]:
        """Names a function binds itself (so they shadow imports).

        Import statements bind to a resolvable target; plain
        assignments/parameters make the name unresolvable (no guessing
        through a local variable).
        """
        assigned: set[str] = set()
        imports: dict[str, tuple[str, str]] = {}
        stack: list[ast.AST] = list(body)
        while stack:
            node = stack.pop()
            if isinstance(
                node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            ):
                continue
            if isinstance(node, ast.Lambda):
                continue
            if isinstance(
                node,
                ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp,
            ):
                # Comprehension targets live in the comprehension's own
                # scope; only the element(s)/iterable are this scope's.
                for part in (
                    getattr(node, 'elt', None),
                    getattr(node, 'key', None),
                    getattr(node, 'value', None),
                ):
                    if part is not None:
                        stack.append(part)
                for gen in node.generators:
                    stack.append(gen.iter)
                    stack.extend(gen.ifs)
                continue
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                assigned.add(node.id)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        imports[alias.asname] = ('module', alias.name)
                    else:
                        top = alias.name.split('.')[0]
                        imports[top] = ('module', top)
            elif isinstance(node, ast.ImportFrom):
                mod_name = _abs_module(
                    list(mod.dotted.split('.')),
                    mod.is_package,
                    node.level,
                    node.module,
                )
                if mod_name is None:
                    continue
                for alias in node.names:
                    if alias.name == '*':
                        continue
                    name = alias.asname or alias.name
                    imports[name] = ('attr', f'{mod_name}.{alias.name}')
            else:
                stack.extend(ast.iter_child_nodes(node))
        return frozenset(assigned), imports

    # -- resolution ----------------------------------------------

    def resolve(
        self,
        expr: ast.expr,
        mod: ModuleInfo,
        fn: FuncInfo | None,
    ) -> Target | None:
        """Resolve a call/function expression to a scanned target."""
        if isinstance(expr, ast.Attribute) and expr.attr in (
            'rglob',
            'read_bytes',
            'write_bytes',
            'write_text',
        ):
            # Bulk byte I/O: a multi-megabyte read/write freezes the
            # loop as surely as a zip pack. ``write_text`` rides with
            # them — a WRITE is never a cheap metadata op: it blocks
            # on the disk for as long as the payload is big, and the
            # payload is the caller's business (a whole system prompt,
            # a settings file, a 1.5 MB skill archive). ``read_text``
            # and plain ``open`` stay out on the metadata-noise policy
            # with exists/mkdir/stat (every metadata read in the tree);
            # a miss there is a microsecond, a miss here is a stall.
            return Target('prim', f'Path.{expr.attr}')
        if (
            isinstance(expr, ast.Attribute)
            and isinstance(expr.value, ast.Call)
            and isinstance(expr.value.func, ast.Name)
            and expr.value.func.id == 'super'
            and fn is not None
            and fn.class_qualname is not None
        ):
            # ``super().m()`` resolves to a method of a BASE, never to
            # the enclosing class' own override.
            found = self._lookup_class_member(
                Key(fn.key.module, fn.class_qualname),
                expr.attr,
                from_bases=True,
            )
            if found is None or found[0] != 'method':
                return None
            member = found[1]
            return Target('func', f'{member.module}.{member.qualname}', member)
        root, attrs = _flatten(expr)
        if root is None:
            return None
        if root in ('self', 'cls'):
            if fn is None or fn.class_qualname is None or not attrs:
                return None
            return self._class_member(
                Key(fn.key.module, fn.class_qualname), attrs
            )
        root_target, root_module = self._resolve_root(root, mod, fn)
        if root_module is not None:
            dotted = '.'.join([root_module, *attrs])
            return self._resolve_dotted(dotted) if attrs else None
        if root_target is None:
            return None
        if not attrs:
            return root_target
        if root_target.kind != 'class' or root_target.key is None:
            return None
        return self._class_member(root_target.key, attrs)

    def _resolve_root(
        self, root: str, mod: ModuleInfo, fn: FuncInfo | None
    ) -> tuple[Target | None, str | None]:
        """Resolve the leftmost name: (target, module dotted name)."""
        if fn is not None:
            if root in fn.imports:
                kind, dotted = fn.imports[root]
                return self._binding_target(kind, dotted)
            if root in fn.assigned:
                return None, None
        return self._module_root(root, mod, fn)

    def _module_root(
        self, root: str, mod: ModuleInfo, fn: FuncInfo | None
    ) -> tuple[Target | None, str | None]:
        candidates = self.funcs_by_name.get((mod.dotted, root), [])
        wanted = [root]
        if fn is not None:
            wanted.append(f'{fn.key.qualname}.{root}')
        for qualname in wanted:
            for key in candidates:
                if key.qualname == qualname:
                    target = Target('func', f'{key.module}.{key.qualname}', key)
                    return target, None
        if len(candidates) == 1:
            key = candidates[0]
            return Target('func', f'{key.module}.{key.qualname}', key), None
        for key in self.classes_by_name.get((mod.dotted, root), []):
            if key.qualname == root:
                target = Target('class', f'{key.module}.{key.qualname}', key)
                return target, None
        binding = mod.bindings.get(root)
        if binding is not None:
            return self._binding_target(*binding)
        return None, None

    def _binding_target(
        self, kind: str, dotted: str
    ) -> tuple[Target | None, str | None]:
        if kind == 'module':
            return None, dotted
        return self._resolve_dotted(dotted), None

    def _resolve_dotted(
        self, dotted: str, seen: frozenset[str] = frozenset()
    ) -> Target | None:
        """Resolve ``a.b.c`` through primitives and scanned modules.

        ``seen`` guards re-export chains (``from . import x`` in the
        ``x`` package) from resolving a name back to itself.
        """
        primitive = match_primitive(dotted)
        if primitive is not None:
            return Target('prim', primitive)
        if dotted in seen:
            return None
        seen = seen | {dotted}
        parts = dotted.split('.')
        # Longest module prefix first: ``mod.helper`` is a function of
        # ``mod`` before it is a class member of ``mod``'s parent.
        for split in range(len(parts) - 1, 0, -1):
            mod = self.modules.get('.'.join(parts[:split]))
            if mod is not None:
                found = self._module_member(mod, parts[split:], seen)
                if found is not None:
                    return found
        return None

    def _module_member(
        self,
        mod: ModuleInfo,
        names: list[str],
        seen: frozenset[str] = frozenset(),
    ) -> Target | None:
        name = names[0]
        if len(names) == 1:
            for key in self.funcs_by_name.get((mod.dotted, name), []):
                if key.qualname == name:
                    return Target('func', f'{key.module}.{key.qualname}', key)
        for key in self.classes_by_name.get((mod.dotted, name), []):
            if key.qualname == name:
                if len(names) == 1:
                    return Target('class', f'{key.module}.{key.qualname}', key)
                return self._class_member(key, names[1:])
        binding = mod.bindings.get(name)
        if binding is not None:
            rest = '.'.join([binding[1], *names[1:]])
            return self._resolve_dotted(rest, seen)
        return None

    def _class_member(self, key: Key, names: list[str]) -> Target | None:
        if not names:
            return Target('class', f'{key.module}.{key.qualname}', key)
        found = self._lookup_class_member(key, names[0])
        if found is None:
            return None
        kind, member = found
        if kind == 'method':
            if len(names) > 1:
                return None
            return Target('func', f'{member.module}.{member.qualname}', member)
        return self._class_member(member, names[1:])

    def _lookup_class_member(
        self, key: Key, name: str, *, from_bases: bool = False
    ) -> tuple[str, Key] | None:
        """Find a method/nested class along the class' base chain.

        ``from_bases`` starts at the bases and skips the class itself —
        the lookup semantics of ``super().name``.
        """
        if from_bases:
            cls = self.classes.get(key)
            if cls is None:
                return None
            queue: list[Key] = []
            for base in cls.bases:
                target = self.resolve(base, self.modules[key.module], None)
                if target is not None and target.kind == 'class':
                    if target.key is not None:
                        queue.append(target.key)
            seen: set[Key] = {key}
        else:
            queue = [key]
            seen = set()
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            cls = self.classes.get(current)
            if cls is None:
                continue
            if name in cls.methods:
                return 'method', cls.methods[name]
            if name in cls.nested:
                return 'class', cls.nested[name]
            for base in cls.bases:
                target = self.resolve(base, self.modules[current.module], None)
                if target is not None and target.kind == 'class':
                    if target.key is not None:
                        queue.append(target.key)
        return None

    # -- call collection -----------------------------------------

    def _collect_calls(self) -> None:
        for fn in self.funcs.values():
            mod = self.modules[fn.key.module]
            for call in self._iter_calls(fn.node):
                if self._has_ok(mod, call):
                    continue
                target = self.resolve(call.func, mod, fn)
                if target is not None and target.kind == 'class':
                    assert target.key is not None
                    target = self._class_member(target.key, ['__init__'])
                fn.calls.append(
                    CallSite(
                        line=call.lineno,
                        display=ast.unparse(call.func),
                        target=target,
                    )
                )

    @staticmethod
    def _iter_calls(node: ast.AST) -> list[ast.Call]:
        """Calls executed by this function's body.

        Nested ``def``/``lambda``/``class`` bodies are separate scopes:
        their calls belong to them, not to the enclosing function.
        """
        calls: list[ast.Call] = []
        body = getattr(node, 'body', [])
        stack: list[ast.AST] = list(body)
        while stack:
            current = stack.pop()
            if isinstance(
                current,
                ast.FunctionDef
                | ast.AsyncFunctionDef
                | ast.ClassDef
                | ast.Lambda,
            ):
                continue
            if isinstance(current, ast.Call):
                calls.append(current)
            stack.extend(ast.iter_child_nodes(current))
        return calls

    def _has_ok(self, mod: ModuleInfo, call: ast.Call) -> bool:
        """True when the call line carries a valid ``# blocking-ok:``."""
        lines = mod.source.splitlines()
        last = call.end_lineno or call.lineno
        for lineno in range(call.lineno, last + 1):
            if lineno > len(lines):
                break
            match = OK_COMMENT.search(lines[lineno - 1])
            if match is None:
                continue
            if match.group('reason').strip():
                return True
            self.invalid_hatches.append(f'{mod.path}:{lineno}')
            return False
        return False

    # -- analysis -------------------------------------------------

    def _chain_for(
        self, fn: FuncInfo, site: CallSite
    ) -> tuple[Hop, ...] | None:
        """Blocking chain reached by one call site, if any."""
        target = site.target
        if target is None:
            return None
        path = self.modules[fn.key.module].path
        if target.kind == 'prim':
            return (Hop(target.name, path, site.line),)
        if target.kind != 'func' or target.key is None:
            return None
        callee = self.funcs.get(target.key)
        if callee is None or callee.is_async:
            return None
        sub = self.blocking.get(target.key)
        if sub is None:
            return None
        return (Hop(site.display, path, site.line),) + sub

    def _compute_blocking(self) -> None:
        changed = True
        while changed:
            changed = False
            for key, fn in self.funcs.items():
                if fn.is_async or key in self.blocking:
                    continue
                for site in fn.calls:
                    chain = self._chain_for(fn, site)
                    if chain is not None:
                        self.blocking[key] = chain
                        changed = True
                        break

    def violations(self) -> list[Violation]:
        """Every ``async def`` that reaches blocking work."""
        out: list[Violation] = []
        for fn in self.funcs.values():
            if not fn.is_async:
                continue
            path = self.modules[fn.key.module].path
            for site in fn.calls:
                chain = self._chain_for(fn, site)
                if chain is None:
                    continue
                out.append(
                    Violation(
                        path=path,
                        line=site.line,
                        fn_name=fn.name,
                        chain=chain,
                    )
                )
        out.sort(key=lambda v: (v.path, v.line, v.fn_name))
        return out


def load_app_sources() -> tuple[dict[str, str], dict[str, str]]:
    """All server-side ``app/`` modules, by dotted name and path."""
    sources: dict[str, str] = {}
    paths: dict[str, str] = {}
    for path in sorted(APP_ROOT.rglob('*.py')):
        rel = path.relative_to(REPO_ROOT)
        if set(EXCLUDED_DIRS).intersection(rel.parts):
            continue
        parts = list(rel.with_suffix('').parts)
        if parts[-1] == '__init__':
            parts = parts[:-1]
        dotted = '.'.join(parts)
        if dotted in sources:
            raise AssertionError(f'duplicate module {dotted}')
        sources[dotted] = path.read_text()
        paths[dotted] = rel.as_posix()
    return sources, paths


def main() -> int:
    """Scan ``app/`` and report every violation (exit 1 if any)."""
    sources, paths = load_app_sources()
    violations = BlockingChecker(sources, paths).violations()
    for violation in violations:
        print(violation.format())
    if violations:
        print(f'{len(violations)} blocking call(s) on the event loop')
        return 1
    print('no blocking calls in async def')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
