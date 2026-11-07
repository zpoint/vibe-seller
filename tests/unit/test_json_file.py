"""Unit tests for the shared atomic JSON read-modify-write."""

import json
import os
import threading

import pytest

from app.utils.json_file import read_json_object, update_json_file

pytestmark = pytest.mark.unit


def test_update_merges_into_existing_content(tmp_path):
    """A mutation merges keys; a missing parent dir is created."""
    path = tmp_path / 'sub' / 'meta.json'
    update_json_file(path, lambda m: m.update({'a': 1}))
    update_json_file(path, lambda m: m.update({'b': 2}))
    assert json.loads(path.read_text(encoding='utf-8')) == {'a': 1, 'b': 2}


def test_missing_corrupt_and_non_object_files_read_as_empty(tmp_path):
    """The read tolerates what a half-dead file can look like."""
    path = tmp_path / 'meta.json'
    assert read_json_object(path) == {}
    path.write_text('{not json', encoding='utf-8')
    assert read_json_object(path) == {}
    path.write_text('[]', encoding='utf-8')
    assert read_json_object(path) == {}
    path.write_text('"x"', encoding='utf-8')
    assert read_json_object(path) == {}


def test_a_mutation_from_an_empty_read_replaces_corrupt_content(tmp_path):
    """Corrupt content is healed, not preserved forever (#204 readers
    treated a torn file as {} — the writer agrees)."""
    path = tmp_path / 'meta.json'
    path.write_text('{torn', encoding='utf-8')
    update_json_file(path, lambda m: m.update({'a': 1}))
    assert json.loads(path.read_text(encoding='utf-8')) == {'a': 1}


def test_noop_mutation_does_not_rewrite(tmp_path):
    """A mutation that changes nothing leaves the file (inode) alone —
    an already-trimmed file is not rewritten."""
    path = tmp_path / 'meta.json'
    update_json_file(path, lambda m: m.update({'a': 1}))
    inode = path.stat().st_ino
    update_json_file(path, lambda m: m.update({'a': 1}))  # same value
    update_json_file(path, lambda m: m.pop('absent', None))  # no-op pop
    assert path.stat().st_ino == inode
    update_json_file(path, lambda m: m.pop('a', None))  # real change
    assert path.stat().st_ino != inode
    assert json.loads(path.read_text(encoding='utf-8')) == {}


def test_failed_write_keeps_original_and_cleans_tmp(tmp_path, monkeypatch):
    """A failed atomic write leaves the original file and no tmp."""
    path = tmp_path / 'meta.json'
    update_json_file(path, lambda m: m.update({'a': 1}))

    def boom(src, dst):
        raise OSError('no replace')

    monkeypatch.setattr(os, 'replace', boom)
    with pytest.raises(OSError):
        update_json_file(path, lambda m: m.update({'b': 2}))

    assert json.loads(path.read_text(encoding='utf-8')) == {'a': 1}
    assert not list(tmp_path.glob('*.tmp'))


def test_concurrent_writers_on_threads_lose_no_keys(tmp_path):
    """N threads each add a distinct key — every one must survive.

    The skills mirror mutates .sync_meta.json from worker threads
    (``asyncio.to_thread``) while other writers run on the event loop.
    A read-modify-write that is merely "synchronous" still interleaves
    ACROSS threads: two writers read the same snapshot and the second
    one back over the first. ``update_json_file`` serialises the whole
    step so no key is lost.
    """
    path = tmp_path / 'meta.json'
    count = 32
    barrier = threading.Barrier(count)

    def writer(i: int) -> None:
        barrier.wait()
        update_json_file(path, lambda m: m.update({f'k{i}': i}))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert json.loads(path.read_text(encoding='utf-8')) == {
        f'k{i}': i for i in range(count)
    }
