# Project Knowledge

3-level knowledge hierarchy:

## L1: Builtin Knowledge (this directory)

Shipped with the `app` package and synced to every workspace
(`~/.vibe-seller/knowledge/project/`).

```
knowledge/project/
  common/                # Flat .md files (universal rules)
    amazon-sites.md      # Amazon Seller Central URLs for all 23 countries
    ziniao-browser.md    # Ziniao auto-fill behavior, passwords, 2FA
    ziniao-block-page.md # Ziniao URL block detection, whitelisting
    skill-environment.md # Shared Python venv setup for skills
  CATALOG.md             # L1 catalog (maintainer-committed)
  README.md              # This file
  MANIFEST.txt           # File list for remote sync
```

- Maintainer commits to `app/knowledge/`, `knowledge_sync.fetch()` copies to workspace
- Read-only for agents and users — changes require a code commit

## L2: Cross-Store Relationships (local workspace)

`~/.vibe-seller/knowledge/notes.md` — minimal, user/AI-editable.

Contains ONLY cross-store relationships:
- "Store B is 跟卖 of Store A on Amazon SA"
- "Store A and Store C share the same brand X"
- "Store D and Store E use the same supplier"

Agents read this to understand store interdependencies.
Users can create additional files (e.g., `brand-x-stores.md`).

## L3: Per-Store Knowledge (local workspace)

`~/.vibe-seller/stores/<slug>/<platform>/<COUNTRY>/*.md`

ALL store-specific knowledge goes here:
- Platform/country selectors and page layouts for THIS store
- SKU lists, shipment info, order templates
- Short notes → `<platform>/<COUNTRY>/notes.md`
- Longer content → `<platform>/<COUNTRY>/<descriptive-name>.md`

NOT shared between stores. Each store has its own isolated knowledge tree.

## Sync

1. `knowledge_sync.fetch()` copies L1 from package to workspace on startup
2. Remote sync checks GitHub for commit changes (>24h cooldown)
3. Manual sync via `POST /api/workspace/knowledge/sync`
4. Agent workspace symlinks: `knowledge/` → `~/.vibe-seller/knowledge/`

## Catalogs

Each level has a `CATALOG.md` index so agents read one file instead of scanning:

- L1: `knowledge/project/CATALOG.md` — maintainer runs `generate_catalog.sh`
- L2: `knowledge/CATALOG.md` — daily scheduled task (AI-generated)
- L3: `stores/<slug>/CATALOG.md` — daily scheduled task (pure file ops)

## Contributing Builtin Knowledge

- Add markdown files with platform-specific information to `common/`
- Run `cd app/knowledge && bash generate_catalog.sh` to regenerate CATALOG.md
- Update MANIFEST.txt:
  `cd app/knowledge && find . -type f -not -name MANIFEST.txt -not -name '.*' | sed 's|^\./||' | sort > MANIFEST.txt`
- Commit both the new file and updated CATALOG.md + MANIFEST.txt
