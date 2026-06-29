"""Store entries for the workspace UI: knowledge + run data, joined.

Disk keeps two roots on purpose — ``stores/<slug>/`` (curated
knowledge, wired into catalogs/prompts/reflection) and
``store-data/<slug>/`` (run artifacts, never knowledge). The STORE
is one domain entity though, so the slug join happens HERE in the
backend; the frontend renders one entry per store with no combining.
"""

from pathlib import Path


def _collect_store_tree(root: Path, base_dir: Path) -> list[dict]:
    """One entry per <slug> dir under base_dir, files rglob'd."""
    entries = []
    if not base_dir.is_dir():
        return entries
    for store_path in sorted(base_dir.iterdir()):
        if not store_path.is_dir() or store_path.name.startswith('.'):
            continue
        files = []
        for f in sorted(store_path.rglob('*')):
            if f.is_file() and '.git' not in f.parts:
                rel = f.relative_to(root)
                stat = f.stat()
                files.append({
                    'path': str(rel),
                    'name': f.name,
                    'size': stat.st_size,
                    'mtime': stat.st_mtime,
                    'has_content': stat.st_size > 50,
                })
        entries.append({
            'slug': store_path.name,
            'path': str(store_path.relative_to(root)),
            'files': files,
            'file_count': len(files),
            'has_content': any(f['has_content'] for f in files),
        })
    return entries


def collect_store_entries(root: Path) -> list[dict]:
    """stores/ + store-data/ joined by slug — one entry per store."""
    store_profiles = _collect_store_tree(root, root / 'stores')
    store_data = _collect_store_tree(root, root / 'store-data')
    # Run data reads newest-first (knowledge keeps stable name order).
    for d in store_data:
        d['files'].sort(key=lambda f: -f['mtime'])
    data_by_slug = {d['slug']: d for d in store_data}
    for entry in store_profiles:
        d = data_by_slug.pop(entry['slug'], None)
        entry['data_path'] = d['path'] if d else f'store-data/{entry["slug"]}'
        entry['data_files'] = d['files'] if d else []
        entry['data_file_count'] = d['file_count'] if d else 0
    for d in data_by_slug.values():
        # Run data without a stores/ profile still surfaces.
        store_profiles.append({
            'slug': d['slug'],
            'path': f'stores/{d["slug"]}',
            'files': [],
            'file_count': 0,
            'has_content': False,
            'data_path': d['path'],
            'data_files': d['files'],
            'data_file_count': d['file_count'],
        })
    return store_profiles
