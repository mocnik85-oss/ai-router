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

State: PENDING

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

State: PENDING

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

State: PENDING

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

State: PENDING

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

State: PENDING

Dependencies:
TASK-007

Objective:
Update the human-readable project plan to reflect the verified repository and V1.1 architecture.

Acceptance Criteria:
- No stale test counts remain.
- V1.1 authority model is represented.
- Current architecture matches the repository.
- Future work is consistent with the authoritative task register.
