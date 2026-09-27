# TASK-010 Execution Authorization

Protocol Version: V1.1
Project ID: ai-router
Project Version: v1.1-migration

Task ID: TASK-010
Task Version: 1
Requirement Version: v1.1

Execution ID: EXEC-TASK-010-001
Handoff ID: HANDOFF-TASK-010-001
Correlation ID: CORR-TASK-010-001
Idempotency Key: TASK-010-v1-exec-001
Lease ID: LEASE-TASK-010-001

Task State: READY
Handoff State: READY
Execution State: NOT_STARTED

Authorized by: Project Manager
Authorization Basis: TASK-008 COMPLETED; TASK-009 execution reconciled as FAILURE -> REROUTE; direct OpenCode smoke test succeeded; TASK-010 READY; PM explicitly authorized reroute/recovery task.

Objective:
Diagnose and correct the demonstrated JEV -> OpenCode integration timeout while preserving Protocol V1.1 governance, READ_ONLY enforcement, mechanical orchestration, and provider separation.

Execution policy:
READ_ONLY during diagnostic/live validation work unless a narrowly scoped implementation change is required by the task and separately verified before live validation.

Constraints:
- No automatic retry/fallback.
- No --auto execution.
- No paid model/API.
- No governance redesign.
- No silent replacement execution.
- Preserve PM authority and lease controls.
- Repository state must be verified before and after live validation.

Required evidence:
- Root-cause diagnosis.
- Implementation diff, if any.
- Focused test results.
- Full regression results.
- Compile result.
- Controlled live E2E result.
- Pre/post repository state.
- Independent review result.
