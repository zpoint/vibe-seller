"""What a report-download run owes, as data rather than as prose.

A scheduled monthly download has an exact deliverable set: for this
store, this month, these files must exist and must carry rows. Until
now that set existed only inside a plan document written for the agent
to read, which left three holes with one shape — nothing could *check*
the list:

* An unpublished upstream report downloads as a well-formed CSV holding
  a header and nothing else. It is not an error, its status upstream is
  ``Complete``, and it is larger than zero bytes, so every check the
  pipeline had passed it. One such file shipped inside a run that was
  marked COMPLETED and independently reviewed as ok.
* A store with no FBA was asked for a storage report it can never
  produce, and the run failed for the absence.
* An agent that attempted 9 of 19 files reported "9 expected, 9 done".
  When the denominator is self-declared, under-delivery is invisible.

:mod:`app.deliverables.manifest` derives the expected set from the
store's own capabilities and the calendar — both known before the agent
starts, so there is nothing here for an agent to declare and nothing to
re-declare around. :mod:`app.deliverables.verify` checks the workspace
against it and counts rows, because a byte count cannot.

The verdict is not a new terminal state. It rides the existing gate
contract (``app.ai.stop_gates``): unmet entries come back as ``gaps``,
which the outcome model already carries as caveats on a COMPLETED run
(see :mod:`app.task_outcome`). An expected-latency gap therefore lands
"completed, with this file listed as pending" without anybody writing a
day-of-month rule in prose.
"""

from app.deliverables.manifest import (
    Deliverable,
    DeliverableKind,
    derive_manifest,
    month_before,
    store_capabilities,
)
from app.deliverables.verify import (
    DeliverableStatus,
    VerifyReport,
    data_rows,
    verify_workspace,
)

__all__ = [
    'Deliverable',
    'DeliverableKind',
    'DeliverableStatus',
    'VerifyReport',
    'data_rows',
    'derive_manifest',
    'month_before',
    'store_capabilities',
    'verify_workspace',
]
