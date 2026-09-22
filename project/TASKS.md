# AI Router — V1.1 PM Task Register

Protocol: Interrogator AI-Assisted Project Lifecycle Protocol
Protocol Version: V1.1
Project: AI Router / JEV

## TASK-001 — V1.1 Baseline & Architecture Reconciliation

State: COMPLETED

Objective:
Reconcile the inherited implementation with Protocol V1.1.

Evidence:
- Baseline commit: cc28fbe97440ec80a77f55334982fd735b232baf
- Migration baseline: 31f4d43
- 146 tests passed
- 8 subtests passed
- compileall passed
- Working tree clean
- Repository architecture reviewed

Result:
Existing implementation is retained. V1.1 governance gaps and architectural adaptations have been identified.

---

## TASK-002 — Establish V1.1 Artifact and Task Model

State: COMPLETED

Objective:
Implement the minimal authoritative project/task/handoff/execution artifact structures required by Protocol V1.1.

Requirements:
- Project identity and version
- Task identity and version
- Requirement version
- Handoff identity and lifecycle state
- Execution identity
- Correlation ID
- Idempotency key
- Acceptance criteria
- Dependencies
- Verification definition

Acceptance Criteria:
- Required V1.1 artifact identity fields can be represented.
- Task lifecycle states are represented without inventing new states.
- Handoff lifecycle states are represented exactly as defined by V1.1.
- Invalid/missing required identity is rejected.
- Existing JEV functionality remains compatible.
- Existing test suite remains passing.

Dependencies:
TASK-001

Required capability:
Python architecture / schema and test design

Verification:
Focused unit tests + full regression suite

Risk:
Medium

---

## TASK-003 — Execution Lease and UNKNOWN Recovery

State: COMPLETED

Dependencies:
TASK-002

Objective:
Implement V1.1 execution lease and UNKNOWN recovery behavior.

Acceptance Criteria:
- At most one active execution lease exists for task/version.
- Expired leases enter reconciliation.
- UNKNOWN executions are reconciled before replacement.
- Duplicate execution is never silently created.

---

## TASK-004 — Enforce Read-Only Execution Boundary

State: COMPLETED

Dependencies:
TASK-002

Objective:
Turn the existing JEV read-only policy into an independently verifiable execution constraint.

Acceptance Criteria:
- Read-only execution cannot silently perform edits, commits, or other prohibited actions.
- Repository state can be verified before and after execution.
- Existing read-only JEV behavior remains compatible.
- Security-relevant behavior has dedicated tests.

---

## TASK-005 — Result and Evidence Model

State: COMPLETED

Dependencies:
TASK-002

Objective:
Represent the V1.1 evidence chain:

Requirement -> Acceptance Criterion -> Verification Method -> Evidence -> Result

Acceptance Criteria:
- Evidence is traceable to the relevant task and execution.
- Results cannot claim completion without required evidence.
- Existing JEV results remain representable.

---

## TASK-006 — PM Interpretation Layer

State: COMPLETED

Dependencies:
TASK-003, TASK-005

Objective:
Prevent raw execution state/results from directly changing authoritative task/project state.

Acceptance Criteria:
- Execution results are interpreted before task state changes.
- Completion requires acceptance evidence.
- Failure, partial, blocked, and needs-user outcomes are distinguishable.

---

## TASK-007 — Separate Governance, Orchestration, and Execution

State: COMPLETED

Dependencies:
TASK-002, TASK-003, TASK-004, TASK-005, TASK-006

Objective:
Align the implementation architecture with the V1.1 authority model without discarding the existing JEV/router implementation.

Acceptance Criteria:
- PM authority is not embedded inside specialist execution code.
- Orchestration remains mechanical.
- Existing model routing remains reusable.
- Execution backends remain replaceable.

---

## TASK-008 — Reconcile MASTER_PLAN.md

State: COMPLETED

Dependencies:
TASK-007

Objective:
Update the human-readable project plan to reflect the verified repository and V1.1 architecture.

Acceptance Criteria:
- No stale test counts remain.
- V1.1 authority model is represented.
- Current architecture matches the repository.
- Future work is consistent with the authoritative task register.

---

## MVP-001 — PM Runtime (Proposal Path and Approval Gate)

State: COMPLETED

Batch:
MVP-BATCH-001

Objective:
Implement MVP-001 of the Autonomous Coding MVP: the authoritative
PM-side runtime that turns a user text request into a structured,
machine-readable PM proposal built from the existing V1.1 artifacts,
records the explicit PM approval, and refuses any implementation
handoff for a proposal that does not exist or is not approved.

Implementation:
- pm/__init__.py, pm/__main__.py — package and executable entry point
- pm/cli.py — `python -m pm` propose / show / approve
- pm/context.py — read-only loading of the PM-owned records
- pm/proposal.py — structured PM proposal, identity, approval record
- pm/runtime.py — propose / approve / handoff runtime and approval gate
- pm/errors.py — fail-closed runtime errors
- tests/test_pm_runtime.py — focused MVP-001 tests

Evidence:
- Focused MVP-001 tests (tests/test_pm_runtime.py): 31 passed
- Full regression suite: 414 passed, 243 subtests passed
- python -m compileall: passed
- HEAD remains 48138fc; no commit and no push were made
- No existing tracked source file was modified
- protocol/validation/TASK-003-live-validation.json remains untouched

Result:
MVP-001 is recorded COMPLETED in this register. It does not dispatch or
execute implementation work and grants no permission. MVP-002 and
MVP-004 remain the next approved tasks of MVP-BATCH-001; the
Autonomous Coding MVP as a whole is not complete.

---

## MVP-BATCH-001 — Autonomous Coding MVP

Batch membership:

- MVP-001 — PM runtime (proposal path and approval gate) — COMPLETED
- MVP-002 — next approved task — not yet started
- MVP-003 — Dependency Graph + Batch Scheduler — PLANNED, not activated
- MVP-004 — next approved task — not yet started

MVP-001 is the completed member of this batch. MVP-002 and MVP-004 are
the next approved batch tasks and remain not yet started. MVP-003 is a
member of the approved Autonomous Coding MVP plan — it provides the
dependency graph and batch scheduling machinery for the autonomous
coding workflow — but it is PLANNED and NOT ACTIVATED: it is not part
of the currently approved execution set, it has not started, and it is
not a dependency or prerequisite of MVP-002 or MVP-004, so it does not
block them. No other MVP task is approved or authorized by this
record; new tasks must be created and authorized by the PM.
