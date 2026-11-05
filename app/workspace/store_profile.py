"""Scaffolding a store profile directory.

Split out of ``app/workspace/manager.py`` (which had grown past the
repo's 800-line limit): scaffolding a store folder is its own
concern — the FILES a new store starts with — not a manager
mechanism. The manager keeps the git commit and the public method
name (``WorkspaceManager.create_store_profile``); this module owns
what lands on disk.

The slug becomes a filesystem path segment, so it is validated here —
at the layer that builds the path — against the shape store names
derive to (single source: ``app.browser.wrapper.validate_store_slug``).
No caller above can hand this an escaping string, whatever the route
layers check.
"""

from pathlib import Path

from app.browser.wrapper import validate_store_slug
from app.workspace.store_seed import write_catalog_stub


def scaffold_store_profile(
    root: Path,
    slug: str,
    name: str,
    backend: str = 'chrome',
) -> Path:
    """Create ``<root>/stores/<slug>/`` and seed its profile files.

    Every file is written only when it does not exist yet, so
    re-scaffolding an existing store never overwrites the notes a
    human (or an agent) has written. Returns the store directory.
    """
    validate_store_slug(slug)
    store_dir = root / 'stores' / slug
    store_dir.mkdir(parents=True, exist_ok=True)

    store_md = store_dir / 'STORE.md'
    if not store_md.exists():
        store_md.write_text(
            f"""---
browser: {backend}
---

# Store: {name}

""",
            encoding='utf-8',
        )

    notes_md = store_dir / 'notes.md'
    if not notes_md.exists():
        notes_md.write_text(f'# Notes for {name}\n\n', encoding='utf-8')

    logistics_md = store_dir / 'logistics.md'
    if not logistics_md.exists():
        logistics_md.write_text(f'# Logistics for {name}\n\n', encoding='utf-8')

    write_catalog_stub(store_dir, slug, name)

    if backend == 'ziniao':
        routing_md = store_dir / 'browser-routing.md'
        if not routing_md.exists():
            routing_md.write_text(
                '# Browser Routing Rules\n\n'
                '<!-- Custom routing rules for this store. '
                'These override the default dual-browser '
                'routing. -->\n\n'
                '## Examples\n\n'
                '<!-- Uncomment and edit as needed:\n'
                '- logistics.example.com → Chrome aux\n'
                '- sellercentral.amazon.* → Ziniao\n'
                '-->\n',
                encoding='utf-8',
            )

    return store_dir
