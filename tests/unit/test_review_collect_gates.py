"""Unit tests for the review-collect coverage gates + manifest helper.

The two gates (``review_completeness_review`` soft-converge,
``review_output_gate`` hard backstop) both validate the on-disk
``reviews/v1`` dataset via ``app.ai.review_manifest.audit_run``. These
tests stub the task→slug DB lookup and point the reviews root at a
tmp_path, then exercise the manifest/product-file contract directly.
"""

import importlib.util
import json

import pytest

import app.ai.review_manifest as rm
from app.ai.skill_gate_loader import discover_skill_gates
from app.ai.stop_gates import get_registered_gates
from app.config import BASE_DIR

# The review gates now ship in the skill dir (review-collect/gates/) and
# are discovered by the skill-gate loader, not imported as core modules.
# Load them by path here to exercise their internals directly.
_GATES_SRC = BASE_DIR / 'app' / 'skills_v2' / 'review-collect' / 'gates'


def _load_gate_module(name):
    spec = importlib.util.spec_from_file_location(
        f'test_{name}', _GATES_SRC / f'{name}.py'
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


completeness = _load_gate_module('review_completeness_review')
output_gate = _load_gate_module('review_output_gate')

pytestmark = pytest.mark.unit

SLUG = 'example-store'
TASK = 'task-123'


@pytest.fixture
def reviews_root(tmp_path, monkeypatch):
    """Point review_manifest at the TASK-LOCAL reviews dir + stub slug."""
    monkeypatch.setattr(rm, 'VIBE_SELLER_DIR', tmp_path)
    monkeypatch.setattr(rm, 'store_slug_for_task', lambda task_id: SLUG)
    # Task-local: tasks/<task_id>/reviews (not shared store-data/<slug>).
    root = tmp_path / 'tasks' / TASK / 'reviews'
    root.mkdir(parents=True)
    # Each test starts with clean gate progress state.
    completeness.reset_progress(TASK)
    output_gate.reset_progress(TASK)
    return root


def _product(rating=4.1, reviews=None, collected_at='2026-06-17T09:00:00Z'):
    doc = {
        'schema': 'reviews/v1',
        'store_slug': SLUG,
        'product_id': 'B0AAA',
        'reviews': [] if reviews is None else reviews,
    }
    if rating is not None:
        doc['rating'] = rating
    if collected_at is not None:
        doc['collected_at'] = collected_at
    return doc


def _write(root, platform, country, pid, doc):
    # Freshness is structural now: the task-local reviews dir is emptied
    # on retry, so a file simply existing here == collected this run.
    d = root / platform / country
    d.mkdir(parents=True, exist_ok=True)
    (d / f'{pid}.json').write_text(json.dumps(doc), encoding='utf-8')


def _manifest(root, combos):
    (root / rm.MANIFEST_NAME).write_text(
        json.dumps({'schema': 'reviews/v1', 'combos': combos}),
        encoding='utf-8',
    )


# ── manifest helper ──────────────────────────────────────────────────


class TestValidateProductFile:
    def test_well_formed(self, reviews_root):
        _write(reviews_root, 'amazon', 'us', 'B0AAA', _product())
        assert rm.validate_product_file(TASK, 'amazon', 'us', 'B0AAA') is None

    def test_missing_file(self, reviews_root):
        assert rm.validate_product_file(TASK, 'amazon', 'us', 'NOPE')

    def test_null_rating_is_defect(self, reviews_root):
        _write(reviews_root, 'amazon', 'us', 'B0AAA', _product(rating=None))
        assert 'rating' in rm.validate_product_file(
            TASK, 'amazon', 'us', 'B0AAA'
        )

    def test_rating_zero_is_ok(self, reviews_root):
        # A product with no rating yet uses 0, not null — that is valid.
        _write(reviews_root, 'amazon', 'us', 'B0AAA', _product(rating=0))
        assert rm.validate_product_file(TASK, 'amazon', 'us', 'B0AAA') is None

    def test_reviews_not_a_list(self, reviews_root):
        doc = _product()
        doc['reviews'] = 'oops'
        _write(reviews_root, 'amazon', 'us', 'B0AAA', doc)
        assert rm.validate_product_file(TASK, 'amazon', 'us', 'B0AAA')

    def test_missing_collected_at(self, reviews_root):
        _write(
            reviews_root,
            'amazon',
            'us',
            'B0AAA',
            _product(collected_at=None),
        )
        assert rm.validate_product_file(TASK, 'amazon', 'us', 'B0AAA')

    def test_bad_json(self, reviews_root):
        d = reviews_root / 'amazon' / 'us'
        d.mkdir(parents=True)
        (d / 'B0AAA.json').write_text('{not json', encoding='utf-8')
        assert rm.validate_product_file(TASK, 'amazon', 'us', 'B0AAA')


class TestAuditRun:
    def test_no_slug_returns_none(self, reviews_root, monkeypatch):
        monkeypatch.setattr(rm, 'store_slug_for_task', lambda t: None)
        assert rm.audit_run(TASK) is None

    def test_no_manifest(self, reviews_root):
        audit = rm.audit_run(TASK)
        assert audit is not None and not audit.manifest_present

    def test_fully_collected(self, reviews_root):
        _write(reviews_root, 'amazon', 'us', 'B0AAA', _product())
        _manifest(
            reviews_root,
            [
                {
                    'platform': 'amazon',
                    'country': 'us',
                    'expected': ['B0AAA'],
                    'collected': ['B0AAA'],
                }
            ],
        )
        audit = rm.audit_run(TASK)
        assert audit.total_expected == 1 and audit.total_ok == 1
        assert not audit.shortfalls and not audit.defects

    def test_shortfall_and_defect(self, reviews_root):
        _write(reviews_root, 'amazon', 'us', 'B0AAA', _product())
        # B0BBB enumerated but never written → both a shortfall (collected
        # 1/2) and a defect (missing file).
        _manifest(
            reviews_root,
            [
                {
                    'platform': 'amazon',
                    'country': 'us',
                    'expected': ['B0AAA', 'B0BBB'],
                    'collected': ['B0AAA'],
                }
            ],
        )
        audit = rm.audit_run(TASK)
        assert audit.shortfalls and audit.defects
        assert audit.total_ok == 1 and audit.total_expected == 2

    def test_reviews_dir_is_task_local(self, reviews_root, tmp_path):
        # Freshness is structural: the audited dir is the task-local
        # tasks/<task_id>/reviews, NOT the shared store-data tree — so a
        # prior run / another task can't leave files the gate would see.
        assert rm.reviews_dir(TASK) == tmp_path / 'tasks' / TASK / 'reviews'
        _write(reviews_root, 'amazon', 'us', 'B0AAA', _product())
        _manifest(
            reviews_root,
            [
                {
                    'platform': 'amazon',
                    'country': 'us',
                    'expected': ['B0AAA'],
                    'collected': ['B0AAA'],
                }
            ],
        )
        audit = rm.audit_run(TASK)
        assert audit.total_ok == 1 and not audit.defects


# ── completeness reviewer (soft) ─────────────────────────────────────


class TestCompletenessGate:
    def test_no_task_id_noop(self, reviews_root):
        assert completeness.check('x', None) is None

    def test_complete_passes(self, reviews_root):
        _write(reviews_root, 'amazon', 'us', 'B0AAA', _product())
        _manifest(
            reviews_root,
            [
                {
                    'platform': 'amazon',
                    'country': 'us',
                    'expected': ['B0AAA'],
                    'collected': ['B0AAA'],
                }
            ],
        )
        assert completeness.check('report', TASK) is None

    def test_no_manifest_denies(self, reviews_root):
        deny = completeness.check('report', TASK)
        assert deny is not None and 'MANIFEST' in deny.reason

    def test_shortfall_denies(self, reviews_root):
        _write(reviews_root, 'amazon', 'us', 'B0AAA', _product())
        _manifest(
            reviews_root,
            [
                {
                    'platform': 'amazon',
                    'country': 'us',
                    'expected': ['B0AAA', 'B0BBB'],
                    'collected': ['B0AAA'],
                }
            ],
        )
        deny = completeness.check('report', TASK)
        assert deny is not None and deny.gate == completeness.GATE_NAME

    def test_stalls_after_cap(self, reviews_root):
        _manifest(
            reviews_root,
            [
                {
                    'platform': 'amazon',
                    'country': 'us',
                    'expected': ['B0AAA'],
                    'collected': [],
                }
            ],
        )
        assert not completeness.is_stalled(TASK)
        # Re-submit an unchanged report; each no-progress round increments
        # the stall counter until it fails open.
        for _ in range(completeness.STALL_CAP + 1):
            assert completeness.check('same report text', TASK) is not None
        assert completeness.is_stalled(TASK)


# ── output gate (hard backstop) ──────────────────────────────────────


class TestOutputGate:
    def test_complete_passes(self, reviews_root):
        _write(reviews_root, 'amazon', 'us', 'B0AAA', _product())
        _manifest(
            reviews_root,
            [
                {
                    'platform': 'amazon',
                    'country': 'us',
                    'expected': ['B0AAA'],
                    'collected': ['B0AAA'],
                }
            ],
        )
        assert output_gate.check('report', TASK) is None

    def test_missing_file_denies(self, reviews_root):
        _manifest(
            reviews_root,
            [
                {
                    'platform': 'amazon',
                    'country': 'us',
                    'expected': ['B0AAA'],
                    'collected': ['B0AAA'],
                }
            ],
        )
        deny = output_gate.check('report', TASK)
        assert deny is not None and deny.gate == output_gate.GATE_NAME

    def test_fails_open_after_cap(self, reviews_root):
        _manifest(
            reviews_root,
            [
                {
                    'platform': 'amazon',
                    'country': 'us',
                    'expected': ['B0AAA'],
                    'collected': ['B0AAA'],
                }
            ],
        )
        for _ in range(output_gate.STALL_CAP):
            assert output_gate.check('report', TASK) is not None
        assert output_gate.is_stalled(TASK)


class TestRegistry:
    def test_gates_are_skill_bundled_not_core_registered(self):
        # Discovered from the skill dir by the loader...
        discovered = discover_skill_gates(BASE_DIR / 'app' / 'skills_v2')
        assert 'review_completeness_review' in discovered
        assert 'review_output_gate' in discovered
        # ...and no longer in the core plugin registry.
        reg = get_registered_gates()
        assert 'review_completeness_review' not in reg
        assert 'review_output_gate' not in reg
