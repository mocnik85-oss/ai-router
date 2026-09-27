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

---

## TASK-010 — Diagnose and Correct Live JEV → OpenCode Timeout

State: READY

Dependencies:
TASK-008

Derived from:
TASK-009 failure evidence and PM REROUTE decision

Objective:
Diagnose and correct the demonstrated JEV → OpenCode integration timeout while preserving Protocol V1.1 governance, READ_ONLY enforcement, mechanical orchestration, and provider separation.

Evidence basis:
- TASK-009 live execution invoked the real JEV → OpenCode path.
- TASK-009 selected model: opencode/nemotron-3.5-lightning-free.
- TASK-009 timed out at the configured 120.0 second OpenCode provider timeout.
- Direct OpenCode smoke test using the same model and repository completed successfully with exit status 0 in approximately 4–5 seconds.
- TASK-009 repository state remained unchanged.
- Existing regression suite passed: 615 tests, 243 subtests.

Scope:
- Diagnose the difference between direct OpenCode execution and the JEV → OpenCode execution path demonstrated by TASK-009.
- Inspect and correct only the concrete integration defect supported by evidence.
- Preserve the existing V1.1 governance/orchestration boundaries.
- Preserve READ_ONLY enforcement.
- Preserve provider/model separation.
- Validate the corrected path with a controlled live E2E execution.
- Preserve complete execution and verification evidence.

Explicitly out of scope:
- Automatic retry/fallback implementation.
- New execution authority or write-capable modes.
- --auto execution.
- Paid-model routing.
- Governance redesign.
- Frozen architecture redesign.
- Silent retry of TASK-009.

Acceptance Criteria:
- Root cause of the JEV → OpenCode timeout is identified with evidence.
- Minimal correction is implemented without weakening V1.1 governance or READ_ONLY enforcement.
- Existing automated regression suite remains passing.
- Corrected JEV → OpenCode path completes a controlled live read-only execution against /home/deck/ai-router.
- Live execution uses the intended model-routing path.
- Repository state is unchanged by the validation execution.
- No automatic retry/fallback occurs.
- Execution, verification, and evidence are traceable to TASK-010.
- Independent review confirms the correction does not bypass PM authority, orchestration controls, lease rules, or provider policy enforcement.

Required capability:
Python/JEV/OpenCode integration debugging, controlled live execution, repository verification, regression testing, and V1.1 governance compliance.

Verification:
Focused tests for the corrected integration path, full regression suite, compile check, controlled live E2E execution, pre/post repository-state comparison, diff inspection, and independent review.

Risk:
HIGH

Budget:
Existing local environment only. No paid model/API required.

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
MVP-004 were the next approved tasks of MVP-BATCH-001 at the time this
result was recorded; both are now COMPLETED. The Autonomous Coding MVP
as a whole is not complete.

---

## MVP-003 — Dependency Graph + Batch Scheduler

Task: MVP-003

Name: Dependency Graph + Batch Scheduler

State: COMPLETED

Objective:
Implement the PM-side dependency graph and deterministic batch scheduler
for the Autonomous Coding MVP.

Delivered:
- pm/scheduler.py — validated dependency graph, deterministic
  topological ordering, authoritative-state readiness, and
  dependency-safe batch scheduling
- pm/errors.py — dependency-specific fail-closed errors
- pm/runtime.py — proposal lookup and PM-owned schedule-to-batch bridge
- pm/__init__.py — public scheduler exports
- tests/test_pm_scheduler.py — focused offline scheduler tests

Acceptance / Verification:
- Unknown dependencies rejected
- Self-dependencies rejected
- Dependency cycles rejected
- Authoritative COMPLETED state releases dependents
- Unfinished dependencies prevent premature execution
- Independent tasks are batched deterministically
- Equivalent input orderings produce identical plans
- PM lifecycle authority remains unchanged
- No backend, network, credential, autonomous-write, or Git execution
  is performed by the scheduler

Evidence:
- Focused MVP-003 tests: 42 passed
- Full regression suite: 615 passed, 243 subtests passed
- git diff --check: passed
- compileall: passed
- Commit: b4f3894 — Implement MVP-003 dependency scheduler
- Branch: protocol-v1.1-migration

Result:
MVP-003 is recorded COMPLETED. The dependency graph and deterministic
batch scheduler are implemented, independently verified, and committed.
No other task state is changed by this record.

---

## MVP-BATCH-001 — Autonomous Coding MVP

Batch membership:

- MVP-001 — PM runtime (proposal path and approval gate) — COMPLETED
- MVP-002 — batch proposal and approval — COMPLETED (checkpoint 9016134)
- MVP-003 — Dependency Graph + Batch Scheduler — COMPLETED (commit b4f3894)
- MVP-004 — implementer contract — COMPLETED (checkpoint 31d1217)

MVP-001, MVP-002, MVP-003, and MVP-004 are completed members of
this batch. MVP-003 provides the dependency graph and deterministic batch
scheduling machinery for the autonomous coding workflow. MVP-005 remains
recorded COMPLETED without stated batch membership. No other MVP task is
approved or authorized by this record; new tasks must be created and
authorized by the PM.

---

## MVP-005 — OpenCode Implementer Adapter

Task: MVP-005

Name: OpenCode Implementer Adapter

State: COMPLETED

Checkpoint: a1efb01 — Complete MVP-005 OpenCode implementer adapter
(branch protocol-v1.1-migration; remote push successful)

Objective:
Record the PM-accepted completion of MVP-005, the first concrete
backend adapter for the backend-independent Implementer contract
established by MVP-004 (protocol/implementer.py). Recorded here by a
PM-authorized, record-only reconciliation; no implementation, protocol,
router, provider, or test file is changed by this record.

Delivered:
- router/opencode_implementer.py — OpenCode Implementer adapter
  (translation only, both directions: generic ImplementationTask /
  ImplementationReport ↔ the existing OpenCode/JEV execution mechanism)
- tests/test_opencode_implementer.py — focused offline MVP-005 tests

Evidence:
- Implementation checkpoint: a1efb01 (2 files changed, 1296
  insertions: router/opencode_implementer.py,
  tests/test_opencode_implementer.py), pushed to
  protocol-v1.1-migration
- Reconciliation verification (record-only):
  - python -m pytest -q → 573 passed, 243 subtests passed
  - python -m compileall -q protocol router pm providers tests app → passed
  - git diff --check → clean
  - only project/TASKS.md, project/PROJECT_STATE.md, and
    MASTER_PLAN.md modified; no implementation file changed
  - protocol/validation/ remains untouched (still untracked, unchanged)
  - no commit and no push were made by this reconciliation

Result:
MVP-005 is recorded COMPLETED in this register with checkpoint a1efb01.
MVP-001, MVP-002, MVP-003, and MVP-004 remain COMPLETED; no other
task state is changed by this record.
MVP-005's membership in MVP-BATCH-001 is not stated by any existing
record, so this entry asserts no batch membership and the MVP-BATCH-001
membership list above is left unchanged — the ambiguity is reported to
the PM rather than resolved here. The Autonomous Coding MVP as a whole
is not complete.

---

## TASK-009 — Live OpenCode E2E Integration Validation

State: READY

Dependencies:
TASK-008

Objective:
Validate the real JEV → OpenCode execution path against the repository using a controlled read-only live E2E execution.

Scope:
- Exercise the real OpenCode process through the existing JEV/orchestration path.
- Use `/home/deck/ai-router` as the repository workspace.
- Use READ_ONLY execution policy only.
- Verify that the actual OpenCode process receives the intended repository context.
- Verify that successful live execution produces a structured result/evidence record.
- Verify that repository state remains unchanged by the read-only execution.
- Preserve complete validation evidence.
- Do not modify implementation code, governance records, or frozen architecture.
- Do not enable autonomous write execution.
- Do not retry automatically after an UNKNOWN or uncertain external outcome.

Acceptance Criteria:
- A real OpenCode process is invoked through the existing JEV execution path.
- The repository path is `/home/deck/ai-router`.
- READ_ONLY policy is enforced at the execution boundary.
- The live execution completes with a captured structured result.
- Repository state before and after execution is demonstrably unchanged.
- Validation evidence identifies task, execution, model, repository, policy, result, and verification outcome.
- Failure or UNKNOWN outcomes are preserved and interpreted without silently creating a replacement execution.
- Existing test suite remains passing.
- No source implementation or authoritative lifecycle state is modified by the execution agent.

Required capability:
Live OpenCode execution, JEV/orchestration integration, repository verification, evidence capture, and failure-safe execution.

Verification:
Pre/post repository state comparison, live execution result inspection, focused integration validation, full regression suite, compile check, and PM review of the resulting evidence.

Risk:
HIGH

Budget:
Existing local environment only. No paid model/API required.

---
