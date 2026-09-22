"""Router V1.1 — the mechanical orchestration layer (ORCHESTRATOR role).

Protocol V1.1 separates three implementation responsibilities, and
this module is the middle one:

    RAW EXECUTION STATE / RESULT
            -> PM INTERPRETATION
            -> AUTHORITATIVE TASK / PROJECT STATE

- ``providers/*`` — specialist/provider execution.  Produces raw
  execution results and never touches governance state.
- ``router.jev.JEV`` — deterministic dispatch core: policy gate, model
  routing, provider invocation, raw execution result.
- ``router.orchestration`` (this module) — the mechanical
  ORCHESTRATOR.  It enforces the exclusive execution lease for a
  dispatch, transports the authorized context (the authoritative
  task/execution artifacts and the governing JEV task) down to the
  dispatch core, performs the dispatch, and persists the
  execution/recovery metadata together with the raw execution result
  in a :class:`DispatchRecord` handed back to the caller.
- ``protocol.interpretation`` — the PM interpretation layer: the only
  place that converts an execution result into a PM decision.

What this module deliberately cannot do:

- it never imports :mod:`protocol.interpretation`, so it can never
  produce a ``PMDecision`` or otherwise decide an outcome;
- it never classifies a raw result and never decides whether a task is
  complete, rerouted, blocked, or waiting on the user — a failed or
  successful execution is returned as raw state, nothing more;
- it never constructs, forges, or mutates authoritative project, task,
  or handoff artifacts; those are frozen governance objects and
  transitions belong to the Project Manager alone;
- it never reconciles, replaces, or authorizes an execution on its
  own: recovery and replacement stay behind the PM-authorized lease
  API in :mod:`protocol.lease`.

Direction of dependencies: orchestration may depend on the ``protocol``
governance models because it transports them; governance never depends
on orchestration or execution code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from protocol.artifacts import ExecutionArtifact, TaskArtifact
from protocol.lease import ExecutionLeaseManager, LeaseStatus
from router.jev import JEV, JEVError, JEVResult, JEVTask


class OrchestrationError(JEVError):
    """Raised when a dispatch request is structurally invalid.

    This is a mechanical orchestration/API error (a malformed request
    refused before anything is executed), not a governance error and
    never a PM decision.
    """


@dataclass(frozen=True, slots=True)
class DispatchRecord:
    """Mechanical bookkeeping record of exactly one dispatch.

    Transport only — it carries execution information upward and
    decides nothing:

    - ``execution``: the authoritative identity of the dispatched
      execution (created by the Project Manager, never by this layer);
    - ``lease_status``: the persisted execution/recovery metadata for
      the concluded dispatch (lease and execution bookkeeping states
      are *not* task or handoff lifecycle states);
    - ``raw_result``: the raw execution result produced by
      :class:`router.jev.JEV` — what the execution claimed, before any
      interpretation.

    The record contains no decision, no result status, and no task or
    handoff lifecycle state.  Interpreting ``raw_result`` into a
    decision is the PM layer's job (``protocol.interpretation``);
    applying a decision to the authoritative lifecycle is the Project
    Manager's job.  The record is frozen, so nothing downstream can
    rewrite what was transported.
    """

    execution: ExecutionArtifact
    lease_status: LeaseStatus
    raw_result: JEVResult


class Orchestrator:
    """Mechanical V1.1 orchestrator: lease enforcement, dispatch,
    transport.

    This class composes the existing JEV dispatch core with the
    governance lease model instead of absorbing either of them:

    - JEV keeps its existing behavior (model routing, policy gate,
      provider invocation) untouched;
    - :class:`protocol.lease.ExecutionLeaseManager` keeps ownership of
      the lease/recovery rules, including PM-authorized replacement;
    - this class performs only the mechanical duties V1.1 assigns to
      the orchestrator role: enforce the exclusive lease while
      dispatching, refuse duplicate or stale dispatch attempts, and
      return the raw result plus its bookkeeping metadata.

    It holds no project/task authority: it cannot interpret results,
    cannot decide outcomes, and cannot mutate authoritative state.
    """

    def __init__(
        self,
        *,
        jev: JEV | None = None,
        lease_manager: ExecutionLeaseManager | None = None,
    ) -> None:
        self._jev = jev or JEV()
        self._lease_manager = lease_manager or ExecutionLeaseManager()

    @property
    def lease_manager(self) -> ExecutionLeaseManager:
        """The lease/recovery governance store this orchestrator enforces.

        Exposed for Project-Manager-side introspection (lease status,
        reconciliation, PM-authorized replacement).  Dispatch itself
        only ever calls the mechanical ``acquire``/``release`` pair.
        """
        return self._lease_manager

    def dispatch(
        self,
        *,
        task: TaskArtifact,
        execution: ExecutionArtifact,
        jev_task: JEVTask,
        lease_id: str,
        ttl: float,
        available_models: list[Any] | None = None,
    ) -> DispatchRecord:
        """Mechanically dispatch one authorized execution.

        Steps — all mechanical, none of them interpretive:

        1. structurally validate the dispatch request (malformed
           requests are refused *before* a lease is consumed);
        2. acquire the exclusive execution lease for the task/version:
           duplicate lease holders, reused identities, and slots that
           expired into an unknown execution state are refused by the
           governance model before anything executes;
        3. dispatch through :class:`router.jev.JEV`, which returns the
           raw execution result;
        4. release the lease so the execution/recovery metadata is
           persisted and the slot is not stranded — including when the
           dispatch was refused (the attempt's identity is consumed
           and never silently reused).  If the lease term expired
           while the dispatch was running, release fails with a
           reconciliation requirement instead of concluding silently:
           recovery stays on the PM side;
        5. return a :class:`DispatchRecord` transporting the raw
           result and the lease status.

        What this method never does: it does not classify the raw
        result, does not validate acceptance evidence, produces no
        decision, and mutates no authoritative artifact.  Interpreting
        the raw result — including all identity/traceability
        validation of a structured result — belongs exclusively to
        :class:`protocol.interpretation.PMInterpreter`.  Errors
        propagate as exceptions; they are never converted here into
        decisions or lifecycle transitions.
        """

        if not isinstance(jev_task, JEVTask):
            raise OrchestrationError(
                "jev_task must be a JEVTask; "
                f"got {type(jev_task).__name__!r}"
            )

        # Identity/type validation of the governance artifacts and of
        # lease_id/ttl is the lease model's own contract — it refuses
        # duplicates and stale attempts; this layer does not second-
        # guess it or duplicate its logic.
        lease = self._lease_manager.acquire(
            task, execution, lease_id=lease_id, ttl=ttl
        )

        try:
            raw_result: JEVResult = self._jev.run(
                jev_task, available_models=available_models
            )
        finally:
            lease_status = self._lease_manager.release(lease)

        return DispatchRecord(
            execution=execution,
            lease_status=lease_status,
            raw_result=raw_result,
        )
