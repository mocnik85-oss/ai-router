"""PM runtime — the authoritative Project-Manager-side entry point.

MVP-001 of the Autonomous Coding MVP (Protocol V1.1).  This package is
the PM-side execution entry point and proposal representation:

- ``python -m pm propose "user text request"`` accepts a text request,
  loads the relevant PM-owned project context
  (``project/PROJECT_STATE.md``, ``project/TASKS.md``,
  ``MASTER_PLAN.md``), and converts the request into a structured,
  machine-readable :class:`pm.proposal.PMProposal`;
- proposals are persisted under ``project/proposals/`` as JSON built
  from the existing V1.1 artifacts (``ProjectArtifact``,
  ``TaskArtifact``, ``VerificationDefinition``, exact ``TaskState``
  vocabulary, correlation ID, idempotency key);
- :meth:`pm.runtime.PMRuntime.approve` records the explicit PM
  approval;
- :meth:`pm.runtime.PMRuntime.handoff` is the single implementation
  gate: it refuses any proposal that does not exist or is not
  approved, so no implementation backend can be reached before an
  approved proposal exists.

Authority boundaries this package keeps:

- **PM state authority stays here.** ``project/TASKS.md`` and
  ``project/PROJECT_STATE.md`` remain PM-owned and are read-only to
  this runtime; proposing, approving, and handing off rewrite no
  register and create no new V1.1 lifecycle state (a proposal's task is
  always ``PENDING``).
- **Backend mechanics stay out.** No ``router``/``providers`` import
  and no hard-coded execution backend: the handoff backend is an
  opaque, caller-supplied callable.
- **No permission is granted.** Approval authorizes handing off one
  proposal — nothing else.  The runtime holds no execution policy,
  workspace, credential, network access, or external-action capability,
  and passes only the proposal to the backend.
- **No result is interpreted here.** Raw backend output stays raw until
  ``protocol.interpretation`` turns it into a PM decision, which the
  Project Manager alone applies.
"""

from __future__ import annotations

from pm.context import ProjectContext, TaskRegisterEntry
from pm.errors import (
    PMContextError,
    PMRuntimeError,
    ProposalAlreadyApprovedError,
    ProposalNotFoundError,
    ProposalNotApprovedError,
)
from pm.proposal import (
    PROPOSAL_SCHEMA,
    PROPOSAL_SCHEMA_VERSION,
    ApprovalRecord,
    PMProposal,
    ProposalIdentity,
    derive_identity,
)
from pm.runtime import PMRuntime, ProposalStore, default_project_root

__all__ = [
    "PROPOSAL_SCHEMA",
    "PROPOSAL_SCHEMA_VERSION",
    "ApprovalRecord",
    "PMContextError",
    "PMProposal",
    "PMRuntime",
    "PMRuntimeError",
    "ProjectContext",
    "ProposalAlreadyApprovedError",
    "ProposalIdentity",
    "ProposalNotFoundError",
    "ProposalNotApprovedError",
    "ProposalStore",
    "TaskRegisterEntry",
    "default_project_root",
    "derive_identity",
]
