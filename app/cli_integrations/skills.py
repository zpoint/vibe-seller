"""Turn a CLI's official agent skills into ONE umbrella skill.

The platforms ship 15–25 skills each. Flat, they would put every one of
them into every agent's always-on skill index; under one umbrella the
index holds one entry per enabled platform and the agent opens the
sub-skill files it needs.

    <home>/.claude/skills/<key>/
    ├── SKILL.md            umbrella: rules + catalog (generated)
    ├── shared/SKILL.md     ← <prefix>shared/SKILL.md
    └── calendar/...        ← <prefix>calendar/...

Sub-skill dirs drop the platform prefix, and every relative path that
pointed at a sibling (``../<prefix>x/...``) is rewritten to ``../x/...``
so it still resolves. The skills always come from the SAME version as
the installed binary, so the commands they document exist.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import re
import shutil
import tempfile

from app.cli_integrations.base import CliIntegration, SkillLayout
from app.cli_integrations.paths import (
    SKILLS_VERSION_FILE,
    skill_dir,
    skills_root,
    skills_version,
)
from app.workspace.skill_swap import swap_in

_FRONTMATTER = re.compile(r'\A---\s*\n(.*?)\n---\s*\n', re.S)
_CATALOG_DESC_CHARS = 160


def _strip_top(paths: list[str]) -> str:
    """The single top-level dir every path shares ('' if none)."""
    tops = {p.split('/', 1)[0] for p in paths if '/' in p}
    if len(tops) == 1 and all('/' in p for p in paths):
        return tops.pop() + '/'
    return ''


def select(
    files: Iterable[tuple[str, bytes]], layout: SkillLayout
) -> dict[str, dict[str, bytes]]:
    """``{sub-skill name: {relative path: bytes}}`` from an archive.

    Only dirs under ``layout.root`` named ``<prefix><name>`` that hold a
    ``SKILL.md`` are kept; ``layout.exclude`` drops some by source name
    and ``layout.keep`` keeps only the names it accepts.
    """
    entries = list(files)
    top = _strip_top([p for p, _ in entries])
    root = layout.root.strip('/') + '/'
    out: dict[str, dict[str, bytes]] = {}
    for path, body in entries:
        rel = path[len(top) :] if top and path.startswith(top) else path
        if not rel.startswith(root):
            continue
        src, _, inner = rel[len(root) :].partition('/')
        if not inner or not src.startswith(layout.prefix):
            continue
        if src in layout.exclude:
            continue
        name = src[len(layout.prefix) :]
        if layout.keep is not None and not layout.keep(name):
            continue
        out.setdefault(name, {})[inner] = body
    return {n: f for n, f in out.items() if 'SKILL.md' in f}


def rewrite_refs(text: str, prefix: str) -> str:
    """``../<prefix>x/`` → ``../x/`` (relative sibling paths only)."""
    pattern = re.compile(
        r'\.\./' + re.escape(prefix) + r'([a-z0-9][a-z0-9-]*)/'
    )
    return pattern.sub(r'../\1/', text)


def frontmatter(text: str) -> dict[str, str]:
    """``key: value`` pairs of a SKILL.md frontmatter (flat keys only)."""
    m = _FRONTMATTER.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ':' not in line or line.startswith((' ', '\t', '#')):
            continue
        k, _, v = line.partition(':')
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _short(desc: str) -> str:
    desc = ' '.join(desc.split()).replace('|', '/')
    if len(desc) <= _CATALOG_DESC_CHARS:
        return desc
    return desc[: _CATALOG_DESC_CHARS - 1].rstrip() + '…'


def umbrella_md(
    integ: CliIntegration, version: str, subs: dict[str, dict[str, bytes]]
) -> str:
    """The generated ``SKILL.md`` at the umbrella root."""
    shared = 'shared' if 'shared' in subs else None
    rows = []
    for name in sorted(subs):
        meta = frontmatter(subs[name]['SKILL.md'].decode('utf-8', 'replace'))
        rows.append(
            f'| `{integ.layout.prefix}{name}` | {_short(meta.get("description", ""))}'
            f' | `./{name}/SKILL.md` |'
        )
    forbidden = ', '.join(
        f'`{integ.binary} {c}`' for c in integ.forbidden_commands
    )
    first = (
        'Read `./shared/SKILL.md` first — it covers flags, output and '
        'permissions every other sub-skill assumes.\n\n'
        if shared
        else ''
    )
    desc = integ.description.replace('"', "'")
    return (
        '---\n'
        f'name: {integ.key}\n'
        f'description: "{desc}"\n'
        f'allowed-tools: Bash({integ.binary}:*), Read\n'
        '---\n\n'
        f'# {integ.title} — `{integ.binary}` {version}\n\n'
        f'`{integ.binary}` is installed for you and already '
        'signed in by the workspace admin (Settings → Integrations). Every '
        'command acts as that one identity, for the whole team.\n\n'
        '## Rules\n\n'
        'These rules win over anything the sub-skills below say about '
        'installing, updating or signing in.\n\n'
        f'- Never run {forbidden}. If a command fails because the sign-in '
        'is missing or expired, or its authorization has '
        f'expired{integ.expired_hint}, stop — do not try to sign in or renew it '
        'yourself, and do not pass on a link from the error — and tell '
        'the user in plain words, without command names or error codes, '
        'what stopped working and that an admin should open Settings → '
        'Integrations (设置 → 集成) and click "Re-authorize" (重新授权) on '
        f'the {integ.title} row. Include this exact markdown link, which '
        'opens that page (you may translate its text, never its target): '
        f'[Settings → Integrations](#integrations-{integ.key}).\n'
        '- If it fails because a permission is missing or restricted, '
        'signing in again does not help: stop and tell the user which '
        f'permission the platform named, and that their {integ.title} '
        'admin has to grant it.\n'
        f'- Never install, update or reinstall `{integ.binary}` or these '
        'skills (no npm / npx / curl installers, no update or upgrade '
        'commands): Vibe Seller manages both, and the version below is the '
        'one these documents match.\n'
        '- Only send messages or emails, invite people, or change or '
        'delete documents when the task asks for it.\n'
        '- The sub-skills below are plain files in this folder, not '
        f'separate skills: where a document names a `{integ.layout.prefix}'
        'xxx` skill, open `./xxx/SKILL.md` with the Read tool.\n\n'
        f'{first}'
        '## Sub-skills\n\n'
        '| Official skill | What it covers | File |\n'
        '|---|---|---|\n' + '\n'.join(rows) + '\n'
    )


def _populate(
    staging: Path,
    integ: CliIntegration,
    version: str,
    subs: dict[str, dict[str, bytes]],
) -> None:
    for name, files in subs.items():
        for rel, body in files.items():
            dest = staging / name / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if rel.endswith('.md'):
                text = body.decode('utf-8', 'replace')
                body = rewrite_refs(text, integ.layout.prefix).encode('utf-8')
            dest.write_bytes(body)
    (staging / 'SKILL.md').write_text(
        umbrella_md(integ, version, subs), encoding='utf-8'
    )
    # Swapped in with the files it describes, so it can never name a
    # version the umbrella does not hold (``manager.update`` compares it
    # with the installed binary's).
    (staging / SKILLS_VERSION_FILE).write_text(version, encoding='utf-8')


def install(
    integ: CliIntegration,
    version: str,
    subs: dict[str, dict[str, bytes]],
    home: Path,
) -> list[str]:
    """Build the umbrella in staging and swap it in (blocking).

    The caller holds ``skills_tree.hold()``. Staging lives in
    ``.claude/.sync-staging`` — excluded from every task-workspace copy
    and swept at boot — never inside the skills dir.
    """
    if not subs:
        raise ValueError(f'no {integ.title} skills found in the archive')
    root = skills_root(home)
    root.mkdir(parents=True, exist_ok=True)
    stage_root = root.parent / '.sync-staging'
    stage_root.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(dir=str(stage_root), prefix=f'{integ.key}-')
    )
    try:
        _populate(staging, integ, version, subs)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    backup = stage_root / f'{integ.key}-previous'
    swap_in(staging, skill_dir(integ.key, home), backup)
    return sorted(subs)


def refresh_umbrella(integ: CliIntegration, home: Path) -> bool:
    """Re-render an installed umbrella's own ``SKILL.md`` (blocking;
    caller holds the tree lock). True when it changed.

    Its rules are this release's text, not the vendor's: a release that
    changes them must reach a platform enabled under the old text,
    though its CLI version — all ``manager.update`` compares — did not
    move. Local only: rendered from the sub-skills the umbrella already
    holds, which stay untouched.
    """
    root = skill_dir(integ.key, home)
    current = root / 'SKILL.md'
    version = skills_version(integ.key, home)
    if version is None or not current.is_file():
        return False
    subs = {
        d.name: {'SKILL.md': (d / 'SKILL.md').read_bytes()}
        for d in sorted(root.iterdir())
        if d.is_dir() and (d / 'SKILL.md').is_file()
    }
    text = umbrella_md(integ, version, subs)
    if current.read_text(encoding='utf-8') == text:
        return False
    tmp = root / '.SKILL.md.tmp'
    tmp.write_text(text, encoding='utf-8')
    tmp.replace(current)
    return True


def uninstall(integ: CliIntegration, home: Path) -> bool:
    """Remove the umbrella (blocking; caller holds the tree lock)."""
    dest = skill_dir(integ.key, home)
    if not dest.exists():
        return False
    shutil.rmtree(dest)
    return True
