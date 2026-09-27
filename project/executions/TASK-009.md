# TASK-009 Execution Authorization

Protocol Version: V1.1
Project ID: ai-router
Project Version: v1.1-migration

Task ID: TASK-009
Task Version: 1
Requirement Version: v1.1

Execution ID: EXEC-TASK-009-001
Handoff ID: HANDOFF-TASK-009-001
Correlation ID: CORR-TASK-009-001
Idempotency Key: TASK-009-v1-exec-001
Lease ID: LEASE-TASK-009-001

Task State: READY
Handoff State: READY
Execution State: NOT_STARTED

Authorized by: Project Manager
Authorization Basis: TASK-008 COMPLETED; TASK-009 READY; dependency satisfied.

Objective:
Validate the real JEV → OpenCode execution path against the repository using a controlled read-only live E2E execution.

Scope:
- Real OpenCode process through the existing JEV/orchestration path.
- Repository workspace: /home/deck/ai-router
- READ_ONLY execution policy only.
- Capture structured execution and validation evidence.
- Verify repository state remains unchanged.
- Preserve failure/UNKNOWN evidence.
- No autonomous write execution.
- No implementation or governance changes.

Acceptance Criteria:
- Real OpenCode process invoked through JEV.
- Correct repository path used.
- READ_ONLY boundary enforced.
- Structured live result captured.
- Repository state unchanged.
- Evidence is complete and traceable.
- Failure/UNKNOWN outcomes are handled without silent duplicate execution.
- Full regression and compilation remain passing.

Dependencies:
TASK-008

Required capability:
Live OpenCode execution, JEV/orchestration integration, repository verification, evidence capture, and failure-safe execution.

Verification:
Pre/post repository state comparison, live execution result inspection, focused integration validation, full regression suite, compile check, and PM review.

Risk:
HIGH

Budget:
Existing local environment only. No paid model/API required.

Execution Lease:
LEASE-TASK-009-001

Lease Rule:
Only one active execution lease may exist for TASK-009 version 1.
