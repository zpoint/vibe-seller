"""Every workflow job that installs the frontend gets pnpm the same way.

install.sh, start.sh and ci.yml use Node 22 + ``corepack prepare
pnpm@latest``; the lockfile is written by that pnpm. A job that pins an
older pnpm (``pnpm/action-setup`` with ``version: '9'``) refuses the
lockfile with ERR_PNPM_LOCKFILE_CONFIG_MISMATCH, which is how the
release and nightly builds broke while ci.yml stayed green.
"""

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

WORKFLOWS = Path(__file__).resolve().parents[2] / '.github' / 'workflows'


def _jobs():
    for wf in sorted(WORKFLOWS.glob('*.yml')):
        doc = yaml.safe_load(wf.read_text())
        for name, job in (doc.get('jobs') or {}).items():
            yield f'{wf.name}:{name}', job.get('steps') or []


def _installs_pnpm_deps(steps):
    return any(
        'pnpm install' in (s.get('run') or '') for s in steps
    )


def test_some_job_installs_frontend_deps():
    assert any(_installs_pnpm_deps(steps) for _, steps in _jobs())


@pytest.mark.parametrize(
    'job,steps',
    [(j, s) for j, s in _jobs() if _installs_pnpm_deps(s)],
)
def test_pnpm_comes_from_corepack_on_node_22(job, steps):
    uses = [s.get('uses') or '' for s in steps]
    assert not any(u.startswith('pnpm/action-setup') for u in uses), (
        f'{job}: pins pnpm via pnpm/action-setup; use corepack like ci.yml'
    )
    runs = '\n'.join(s.get('run') or '' for s in steps)
    assert 'corepack prepare pnpm@latest --activate' in runs, job
    nodes = [
        s for s in steps if (s.get('uses') or '').startswith(
            'actions/setup-node')
    ]
    assert nodes, f'{job}: no actions/setup-node step'
    for s in nodes:
        major = int(str(s['with']['node-version']).split('.')[0])
        assert major >= 22, f'{job}: node {major} < 22'
