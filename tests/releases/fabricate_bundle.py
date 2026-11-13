#!/usr/bin/env python3
"""Copy a bundle document with ONE pin changed, fingerprint included.

A release whose runtime bundle differs from this install's must
be refused the pip path (app/bundle.py). This is that release
asset: the same inputs with one pin moved, hashed exactly the
way scripts/bundle_fingerprint.py hashes it.
"""

import hashlib
import json
import sys

src, dst = sys.argv[1], sys.argv[2]
doc = json.load(open(src, encoding='utf-8'))
pins = doc['pins']
fmt = pins.pop('format', 1)
key = next(
    k
    for k in sorted(pins)
    if isinstance(pins[k], dict) and 'version' in pins[k]
)
before = pins[key]['version']
pins[key]['version'] = f'{before}-FABRICATED-OTHER-RUNTIME'
base = {'format': fmt, 'pins': pins, 'files': doc['files']}
canonical = json.dumps(base, sort_keys=True, separators=(',', ':'))
doc['pins']['format'] = fmt
doc['fingerprint'] = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
open(dst, 'w', encoding='utf-8').write(
    json.dumps(doc, indent=2, sort_keys=True) + '\n'
)
print(f'fabricated bundle: {key} -> {pins[key]["version"]}')
print('fingerprint', doc['fingerprint'])
