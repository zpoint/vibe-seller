"""Print the Windows bundle fingerprint, or write its bundle.json.

The build side of ``app/bundle.py`` — a plain CLI so
``installer/windows/build.ps1`` (and the release workflows, which run on
a machine that never builds an installer) can ask the same question and
get the same answer:

    python scripts/bundle_fingerprint.py --print
    python scripts/bundle_fingerprint.py --version 0.3.2 -o staging/bundle.json

Writing is what puts the fingerprint INTO the installer (as
``{app}\\bundle.json``) and next to it on the release page (as the
``bundle.json`` asset). Both come from this one call, so the file the
installed app reads and the file an update check reads are the same
document by construction.

Exit 2 on a pin disagreement or a missing input: the build must stop
rather than ship a fingerprint that describes a bundle nobody built.
``--require-resolved`` (every build that publishes or installs a bundle)
also refuses a manifest whose floating pins still say ``latest`` — run
``scripts/resolve_bundle_pins.py`` first. Without it a checkout can
still be fingerprinted as it is, which is what the cross-OS agreement
check does.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import bundle  # noqa: E402 — the repo root goes on sys.path above


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        '--version',
        default='',
        help='app version the bundle belongs to (descriptive only)',
    )
    parser.add_argument(
        '-o',
        '--out',
        help='write the bundle document here (build.ps1: the staging dir)',
    )
    parser.add_argument(
        '--print',
        dest='show_fingerprint',
        action='store_true',
        help='print the bare fingerprint',
    )
    parser.add_argument(
        '--require-resolved',
        action='store_true',
        help='refuse pins still at "latest" (run resolve_bundle_pins.py)',
    )
    args = parser.parse_args(argv)

    try:
        doc = bundle.bundle_document(args.version)
        open_pins = bundle.unresolved_pins(doc['pins'])
        if args.require_resolved and open_pins:
            raise ValueError(
                f'{", ".join(open_pins)} still say "latest" — run '
                'scripts/resolve_bundle_pins.py before building'
            )
    except (bundle.PinMismatch, FileNotFoundError, ValueError) as exc:
        print(f'bundle fingerprint failed: {exc}', file=sys.stderr)
        return 2

    if args.out:
        bundle.write_bundle(doc, Path(args.out))
        print(f'wrote {args.out}')
    if args.show_fingerprint:
        print(doc['fingerprint'])
    elif not args.out:
        print(bundle.canonical_json(doc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
