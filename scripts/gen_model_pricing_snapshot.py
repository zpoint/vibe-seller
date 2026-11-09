"""Regenerate app/ai/model_pricing_snapshot.py from models.dev.

The snapshot is what a release prices with until (or without) the
``model_pricing`` sync job: an install that is offline, or that has not
synced yet, still shows a cost. Run before a release, or after adding a
provider preset (and its ``PRESET_PROVIDERS`` entry):

    python scripts/gen_model_pricing_snapshot.py [path/to/api.json]
"""

from __future__ import annotations

import json
from pathlib import Path
import pprint
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ai.model_pricing import slice_catalog  # noqa: E402
from app.ai.model_pricing_sync import DEFAULT_URL  # noqa: E402

OUT = ROOT / 'app' / 'ai' / 'model_pricing_snapshot.py'


def main() -> None:
    if len(sys.argv) > 1:
        body = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    else:
        with urllib.request.urlopen(DEFAULT_URL, timeout=60) as resp:
            body = json.loads(resp.read())
    sliced = slice_catalog(body)
    OUT.write_text(
        '"""Bundled slice of models.dev — generated, do not edit.\n\n'
        'Regenerate with ``python scripts/gen_model_pricing_snapshot.py``.\n'
        'See app/ai/model_pricing.py.\n"""\n\n'
        '# fmt: off\n'
        f'SNAPSHOT = {pprint.pformat(sliced, width=79, sort_dicts=True)}\n',
        encoding='utf-8',
    )
    models = sum(len(p['models']) for p in sliced.values())
    print(f'wrote {OUT} ({len(sliced)} providers, {models} models)')


if __name__ == '__main__':
    main()
