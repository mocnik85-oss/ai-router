# TASK-004 Execution Authorization

Protocol Version: V1.1
Project ID: ai-router
Project Version: v1.1-migration

Task ID: TASK-004
Task Version: 1
Requirement Version: v1.1

Execution ID: EXEC-TASK-004-001
Handoff ID: HANDOFF-TASK-004-001
Correlation ID: CORR-TASK-004-001
Idempotency Key: TASK-004-v1-exec-001

Task State: IMPLEMENTING
Handoff State: IN_PROGRESS
Execution State: RUNNING

Authorized by: Project Manager

Authorization Basis:
TASK-004 is dependency-satisfied because TASK-002 is COMPLETED.
TASK-004 is the next authorized implementation task.

Objective:
Enforce the V1.1 read-only execution policy at the actual execution boundary.

Scope:
- Inspect the existing JEV/OpenCode execution path.
- Identify where read-only policy is currently validated and where actual provider execution occurs.
- Ensure the execution boundary receives and enforces the applicable read-only policy.
- Preserve the existing JEV/router architecture.
- Add focused tests proving prohibited write-capable behavior is blocked at the execution boundary.
- Preserve compatible existing behavior.

Acceptance Criteria:
- Read-only policy is enforced at the actual execution boundary.
- A provider cannot execute a prohibited write-capable operation when the task is governed as read-only.
- Policy enforcement is not merely an upstream validation check.
- Existing permitted read-only behavior continues to work.
- Focused TASK-004 tests pass.
- Full regression suite passes.
- Python compilation passes.
- No unrelated task or protocol changes are introduced.
- No paid model/API is introduced.

Dependencies:
TASK-002

Required Capability:
Python architecture, execution-boundary security, policy enforcement, testing.

Verification:
- Focused TASK-004 tests.
- Full regression suite.
- Python compilation.
- PM inspection of the actual enforcement boundary.

Risk: HIGH

Budget:
Existing local environment only. No paid model/API.

Execution Lease:
LEASE-TASK-004-001

Lease Rule:
Only one active execution lease may exist for TASK-004 version 1.
