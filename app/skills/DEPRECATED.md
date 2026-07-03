# DEPRECATED — legacy skill tree (browser-use 0.12.x)

**Do not add new skills or rewrite these for a newer runtime.** This
directory is **frozen** as the skill tree for clients on `browser-use`
0.12.x (the subcommand CLI: `browser-use open/click/state …`).

## Why it must stay frozen

Every vibe-seller client released up to **v0.0.7** hardcodes `app/skills` as
its skills-sync path — both the download base URL
(`config.py: SKILLS_REPO_URL`) and the GitHub commit-poll path
(`skills_sync.py`). Those clients keep pulling this directory on `main`
before every task. Clients **before v0.0.3** additionally lack the
local-precedence guard in `skills_sync._do_remote_sync`, so anything added
here is pulled onto them and could break them against their pinned 0.12.x
binary.

## Where current skills live

Active development happens in **`app/skills_v2/`** (browser-use 0.13, the
heredoc/env-var interface). New releases point `SKILLS_SUBDIR`
(`app/config.py`) at it, so `_get_local_source`, `SKILLS_REPO_URL`, and the
commit-poll path all resolve to `app/skills_v2`.

For any future breaking runtime change, **freeze the current tree and add
`app/skills_vN+1`** rather than editing in place.

See [docs/browser-use-0.13-migration.md](../../docs/browser-use-0.13-migration.md).

<!-- This is a top-level file (not a skill subdir) and is intentionally
     absent from MANIFEST.txt, so neither sync tier ever copies it. -->
