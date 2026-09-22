"""PM runtime — control errors for the PM-side execution entry point.

Every error in this module is raised on the Project-Manager side
*before* any implementation backend is reached.  They are runtime
control failures, not governance data:

- none of them is a V1.1 task or handoff lifecycle state (the exact
  state sets stay in :mod:`protocol.artifacts`);
- none of them is a PM decision (decisions stay in
  ``protocol.interpretation``);
- none of them transitions authoritative project/task state.

Importing this module pulls in nothing but ``__future__``, so it is
safe from any layer.
"""

from __future__ import annotations


class PMRuntimeError(Exception):
    """Base class for PM runtime control failures."""


class PMContextError(PMRuntimeError):
    """The PM-owned project context could not be loaded or is invalid."""


class ProposalNotFoundError(PMRuntimeError):
    """No proposal exists for the requested proposal identity.

    Raised instead of any partial/derived record: implementation work
    may only ever be considered against a stored proposal.
    """


class ProposalAlreadyApprovedError(PMRuntimeError):
    """A recorded approval must not be overwritten or silently rewritten."""


class ProposalNotApprovedError(PMRuntimeError):
    """Implementation handoff refused: the proposal is not approved.

    This is the fail-closed result of the MVP-001 approval gate: an
    unapproved proposal never reaches an implementation backend.
    """


class PMBatchError(PMRuntimeError):
    """Base class for batch-level PM runtime control failures."""


class BatchNotFoundError(PMBatchError):
    """No batch exists for the requested batch identity.

    Raised instead of any derived or partial record: a batch handoff or
    approval may only ever be considered against a stored batch.
    """


class BatchAlreadyApprovedError(PMBatchError):
    """A recorded batch approval must not be overwritten or rewritten."""


class BatchNotApprovedError(PMBatchError):
    """Implementation handoff refused: the batch is not approved.

    This is the fail-closed result of the MVP-002 batch gate: an
    unapproved batch (and any proposal that belongs to it) never
    reaches an implementation backend.
    """
