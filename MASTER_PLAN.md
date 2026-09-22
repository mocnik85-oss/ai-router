# AI Router / JEV — Current Master Plan

- **Project:** Steam Deck personal AI coding/orchestration system
- **Platform:** SteamOS / Steam Deck
- **Project version:** v1.1-migration
- **Protocol:** Interrogator AI-Assisted Project Lifecycle Protocol V1.1
- **Branch:** `protocol-v1.1-migration`
- **Status:** Active development

> **This document is the authoritative human-readable project plan.**
> It supersedes earlier AI-router plans where they conflict with this document.
>
> Authoritative *lifecycle state* (task states, handoffs, executions) does
> **not** live here: it lives in `project/TASKS.md`,
> `project/PROJECT_STATE.md`, and `project/executions/`. This document
> describes architecture, implemented capability, and direction. Claims
> about what is implemented are verified against the repository, not
> against intention.

---

## 1. Objective

Build a secure, maintainable AI orchestration system for the Steam Deck that can:

- Use OpenRouter models
- Prefer free/low-cost models
- Dynamically discover available models
- Use paid models only when explicitly permitted
- Govern authoritative project/task state under Protocol V1.1
- Use JEV as the deterministic **dispatch core** inside a mechanical orchestrator
- Interpret execution results through an explicit **PM interpretation**
  boundary before any authoritative state change
- Use OpenCode as the current execution backend
- Route tasks between models/providers
- Retry and fall back intelligently *(not yet implemented — see §13)*
- Securely store credentials
- Accept text and eventually voice input
- Optionally provide TTS
- Work with local coding projects
- Test and verify its own work
- Survive normal SteamOS updates as far as practical

The system must remain modular and provider-independent.

---

# 2. Core Architecture

The V1.1 architecture separates four responsibilities:

**Governance ≠ PM interpretation ≠ mechanical orchestration ≠ provider execution**

```text
 ┌────────────────────── GOVERNANCE (authoritative) ──────────────────────┐
 │ protocol/artifacts.py    project / task / handoff / execution artifacts│
 │ protocol/lease.py        exclusive lease, UNKNOWN recovery,            │
 │                           PM-authorized replacement                    │
 │ project/TASKS.md, project/PROJECT_STATE.md, project/executions/        │
 │                           PM-held authoritative records                │
 └──────────────────────────────▲─────────────────────────────────────────┘
                                │ the Project Manager alone applies
                                │ decisions and transitions state
 ┌──────────────────── PM INTERPRETATION ─────────────────────────────────┐
 │ protocol/results.py       structured result + evidence chain           │
 │ protocol/interpretation.py  ExecutionResult → PMDecision               │
 │                            (never mutates authoritative state)         │
 └──────────────────────────────▲─────────────────────────────────────────┘
                                │ raw execution result (never authoritative)
 ┌──────────────── MECHANICAL ORCHESTRATION / DISPATCH ───────────────────┐
 │ router/orchestration.py   Orchestrator: lease enforcement, dispatch,   │
 │                           transport (DispatchRecord)                   │
 │ router/jev.py             JEV dispatch core (policy gate, model        │
 │                           selection, backend invocation)               │
 │ router/policy.py          shared ExecutionPolicy contract              │
 │ router/free_controller.py deterministic free-model scoring             │
 │ router/model_resolver.py  OpenRouter ID → routable backend ID          │
 └──────────────────────────────▲─────────────────────────────────────────┘
                                │ provider calls, raw provider output
 ┌──────────────────── SPECIALIST / PROVIDER EXECUTION ───────────────────┐
 │ providers/opencode.py     OpenCode adapter + read-only boundary check  │
 │ providers/openrouter.py   OpenRouter client, model discovery           │
 │ providers/codex.py        Codex adapter (deferred)                     │
 │ providers/credentials.py  credential abstraction / backends            │
 └────────────────────────────────────────────────────────────────────────┘
```

V1.1 invariants, each enforced by dedicated tests
(`tests/test_separation_boundaries.py`, `tests/test_read_only_boundary.py`):

- **The orchestrator is mechanical.** `router/orchestration.py` enforces the
  execution lease, transports authorized context, dispatches, and returns a
  frozen `DispatchRecord`. It never classifies a result, never produces a
  decision, and never forges or mutates an authoritative artifact. It does
  not import `protocol.interpretation` at all.
- **Providers do not mutate authoritative state.** `providers/*` import no
  `protocol` module and hold no authoritative artifacts. A provider returns
  raw execution information only (exit code, events, stdout/stderr).
- **Raw execution results never become authoritative lifecycle state
  directly.** They pass through `protocol.interpretation.PMInterpreter`,
  which produces a frozen `PMDecision` (COMPLETE / REROUTE / BLOCKED /
  NEEDS_USER / REJECTED). Applying a decision to task/project state remains
  the Project Manager's responsibility; even the interpretation layer never
  performs a state transition.
- **Governance never depends on execution code.** `protocol/*` imports only
  the standard library plus other `protocol` modules — never `router`,
  `providers`, or `app`.
- **Existing JEV/OpenCode functionality is retained and refactored, not
  discarded.** JEV keeps model routing, the policy gate, and provider
  invocation; V1.1 added the boundaries around it.

Verified dependency direction (checked against the actual import graph):

- `protocol/*` imports only the standard library and other `protocol`
  modules — never `router`, `providers`, or `app`.
- `providers/*` import no `protocol` module, so a provider cannot reach
  governance state. `providers/opencode.py` imports only the shared
  `router.policy` contract so both enforcement points use one definition.
- `router/jev.py`, `router/policy.py`, `router/free_controller.py`, and
  `router/model_resolver.py` import no `protocol` module — the dispatch
  core cannot reach governance state either.
- Only `router/orchestration.py` imports `protocol`, because it
  transports governance artifacts upward. It does not import
  `protocol.interpretation`, so it can never produce a decision.

---

# 3. Provider Separation

## OpenRouter

Used for:

- Free models
- Low-cost models
- Model discovery
- Controller models
- General AI tasks

Authentication:

**OpenRouter API key** via the credential abstraction.

Providers are replaceable: model *discovery* (OpenRouter catalogue) is kept
separate from model *execution* (OpenCode IDs) by
`router/model_resolver.py`.

---

## OpenAI API

Separate provider.

Only use it when legitimate API credentials are available.

**ChatGPT Go authentication must not be treated as OpenAI API authentication.**

Never attempt to reuse:

- Browser cookies
- ChatGPT session credentials
- Subscription tokens
- Private authentication data

for API access.

---

## ChatGPT / Codex

ChatGPT subscription access and Codex CLI remain separate from the application's API providers.

Codex is an optional execution/coding capability.

Codex integration is currently deferred until the user's Codex allowance becomes available again. No JEV functionality should depend on Codex being available.

---

# 4. JEV

JEV (`router/jev.py`) is the **deterministic dispatch core** of the
mechanical orchestration layer. It is deliberately not a decision-maker and
not a second PM.

## What JEV does today (implemented, verified)

1. Validate the governing `ExecutionPolicy` (upstream read-only gate).
2. Obtain the model catalogue (injected or fetched from OpenRouter).
3. Ask `FreeModelController` for a free model meeting `ModelRequirements`.
4. Translate the catalogue ID to a routable backend ID (`ModelResolver`).
5. Invoke the selected execution backend (`OpenCodeClient.run`) with the
   governing policy attached.
6. Return a structured **raw** result (`JEVResult`: success flag, model,
   exit code, text, events, error).
7. Classify success/failure *of the execution only* — as raw state, never as
   a task/project outcome.

## What JEV must never do

- Interpret a result into a task/project decision
- Decide whether a task is complete, rerouted, blocked, or needs the user
- Construct, mutate, or transition authoritative artifacts
- Gain retries, paid routing, or unrestricted authority implicitly

Decisions live one layer up:

```text
PM authorizes execution (artifacts + exclusive lease)
   ↓
Orchestrator.dispatch            mechanical
   ↓
JEV                              policy gate → model selection → backend
   ↓
JEVResult                        raw execution result (never authoritative)
   ↓
PMInterpreter → PMDecision       interpretation boundary
   ↓
Project Manager                  applies the authoritative transition
```

OpenCode is the current preferred execution backend.

JEV must not become tightly coupled to a single model or provider: the
model catalogue, controller, resolver, and execution client are all
injectable, and the dispatch core imports no governance model.

Codex remains an optional future backend and is not required for the current
implementation.

---

# 5. Strategic Approach

The preferred development strategy is:

```text
Free/low-cost controller
        ↓
   JEV dispatch core
        ↓
     OpenCode
        ↓
Implementation
        ↓
Tests
        ↓
Verification
        ↓
PM interpretation → authoritative state
```

Rather than manually implementing the entire system ourselves, establish a
capable low-cost/free controller and allow it to assist with implementation.

The controller must operate within explicit security and cost boundaries,
and all of its output is treated as raw execution result until interpreted.

Codex remains a future optional execution backend:

```text
   JEV dispatch
        ↓
    Codex CLI
        ↓
Implementation
```

---

# 6. Current Project

Repository:

```text
~/ai-router/
```

Current structure (verified):

```text
~/ai-router/
├── .git/
├── .gitignore
├── MASTER_PLAN.md          this document
├── README.md
├── app/                    configuration, validation, secret redaction
├── pm/                     PM runtime: proposal path, approval gate,
│                           implementation handoff gate (MVP-001)
├── protocol/               V1.1 governance: artifacts, lease,
│                           results/evidence, PM interpretation
├── project/                PM records: PROJECT_STATE.md, TASKS.md,
│                           executions/ (TASK-002 … TASK-008)
├── providers/              OpenRouter, OpenCode, Codex adapters,
│                           credential store
├── router/                 JEV dispatch core, execution policy,
│                           free controller, model resolver,
│                           mechanical orchestration
├── tests/                  offline unit and boundary tests
├── tts/                    reserved — no implementation yet
└── voice/                  reserved — no implementation yet
```

Local-only directories (gitignored): `.venv/`, `.pytest_cache/`,
`__pycache__/`.

Configuration:

```text
~/.config/ai-router/
```

Cache:

```text
~/.cache/ai-router/
~/.cache/ai-router/models/
~/.cache/ai-router/tmp/
```

---

# 7. Completed

## Phase A — Foundation

**STATUS: COMPLETE**

Verified:

- SteamOS environment
- Python 3.13
- Git
- curl
- jq
- ffmpeg
- PipeWire
- Microphone
- User-space paths
- Project repository
- Git isolation from unrelated repositories

No unnecessary system modifications.

---

## Phase B — Configuration & Security

**STATUS: ALMOST COMPLETE** (the completed items below are verified in code
and tests; no completion criterion has been defined for the remainder)

Implemented:

- TOML configuration (`app/config.py`)
- Configuration validation
- Safe defaults
- Free-only mode (`free_only`)
- Paid-routing flag (`paid_routing_enabled`)
- Retry configuration (`retries` — configuration only; no retry loop exists yet)
- Timeout configuration
- Voice configuration (`voice_enabled`, `stt_*`, `tts_*` — keys only, no
  voice/TTS implementation)
- Credential abstraction (`CredentialStore`)
- Memory credential backend
- KWallet backend
- Secret redaction (`app/redaction.py`)
- Credential-like configuration keys are rejected
- Unit tests

---

## Phase C — OpenRouter

**STATUS: COMPLETE**

Implemented:

- OpenRouter client (`providers/openrouter.py`)
- Credential-store integration
- Public model discovery (`models()`)
- Dynamic free-model discovery (`free_models()`)
- Model metadata normalization (`OpenRouterModel`)
- Capability detection (`supports_tools`, `supports_vision`, reasoning)
- Error classification (auth / rate-limit / timeout / connection / server /
  model / response errors)
- Offline unit tests

The controller must not hard-code today's free-model catalogue because availability is dynamic.

---

## Phase D — Free Model Controller

**STATUS: COMPLETE**

Implemented (`router/free_controller.py`):

- Deterministic free-model selection
- Capability-aware scoring
- Tool requirements
- Vision requirements
- Reasoning preference
- Minimum context requirements
- Router-model exclusion
- Unit tests

The controller is intentionally deterministic at this stage.

---

## Phase E — Execution Backends

**STATUS: PARTIALLY COMPLETE**

### OpenCode

**STATUS: COMPLETE**

Implemented (`providers/opencode.py`):

- OpenCode CLI adapter
- Explicit model selection
- JSON event-stream parsing
- Structured execution result
- Timeout handling
- Process error handling
- **Read-only enforcement at the provider execution boundary**
  (`_enforce_read_only`, fail-closed for unknown policy objects) —
  independent of JEV's upstream validation
- Offline unit tests

Current preferred execution backend.

The adapter does **not** grant OpenCode unrestricted authority.

OpenCode permissions are not treated as a complete security boundary. The
read-only `ExecutionPolicy` is enforced independently of the model's
natural-language instructions, at two points that share one contract
(`router/policy.py`): JEV's upstream validation and the provider boundary.
Broader sandboxing remains future work (§13).

### Codex

**STATUS: DEFERRED**

A Codex adapter exists and has been tested (`providers/codex.py`,
`tests/test_codex.py`), but integration is paused until Codex allowance
becomes available.

JEV must not depend on Codex.

---

## Phase F — V1.1 Governance & Separation (migration)

**STATUS: IMPLEMENTED IN THE REPOSITORY**

The V1.1 authority model is implemented as separate layers, reusing the
existing JEV/OpenCode code rather than replacing it.

| Capability | Module | Tests |
|---|---|---|
| Artifact model: project/task/handoff/execution identity, exact V1.1 lifecycle states, required-field validation (TASK-002) | `protocol/artifacts.py` | `tests/test_artifacts.py` |
| Execution lease: at most one active lease per task/version, expiry → reconciliation, `UNKNOWN` recovery, PM-authorized replacement, duplicate-execution refusal (TASK-003) | `protocol/lease.py` | `tests/test_lease.py` |
| Read-only execution boundary enforced at the actual provider boundary, verifiable before/after execution (TASK-004) | `providers/opencode.py`, `router/policy.py` | `tests/test_read_only_boundary.py` |
| Result and evidence model: `ResultStatus`, `EvidenceRecord`, `ExecutionResult`, requirement → acceptance criterion → verification method → evidence → result traceability; SUCCESS cannot claim completion without evidence (TASK-005) | `protocol/results.py` | `tests/test_results.py` |
| PM interpretation layer: raw result → `PMDecision`, completion gated on acceptance evidence, SUCCESS/PARTIAL/FAILURE/BLOCKED/NEEDS_USER distinguishable, no state mutation (TASK-006) | `protocol/interpretation.py` | `tests/test_interpretation.py` |
| Mechanical orchestration separated from governance and execution; JEV retained as dispatch core; providers return execution information only (TASK-007) | `router/orchestration.py`, `router/jev.py` | `tests/test_separation_boundaries.py` |

Governance artifacts, lease, results, and interpretation depend only on the
standard library, so they are testable independently of execution code.

**Register-state note:** `project/TASKS.md` is the authoritative task
register. At the time of this reconciliation it records TASK-001, TASK-002,
TASK-003, TASK-004, and TASK-006 as `COMPLETED`, and TASK-005, TASK-007, and
TASK-008 as `IMPLEMENTING`, even though the implementation for TASK-005 and
TASK-007 is present and verified in the repository. The register — not this
document — is authoritative for lifecycle state; the divergence is reported
to the PM rather than resolved here.

---

# 8. Current Test Baseline

Current verified baseline (offline; no network or paid model involved):

```text
python -m pytest -q
414 passed, 243 subtests passed
```

Additional checks completed:

```text
python -m compileall -q app providers router protocol pm tests
git diff --check
```

Repository state at the time of this reconciliation:

```text
HEAD: ff6d49b Implement V1.1 execution lease recovery
```

The TASK-004 through TASK-007 source changes, the TASK-004 through
TASK-008 execution records, and this document are present in the working
tree but **not yet committed**; committing is a PM action under V1.1, so
this document records the fact instead of creating a checkpoint.

The repository checkpoint containing the original execution adapters is:

```text
dea78a0 Add OpenCode and Codex execution adapters
```

---

# 9. JEV v0.1 — Implemented vs Remaining

JEV v0.1 was specified as a **deterministic policy/orchestration layer**,
not another LLM. Its original responsibility list, reconciled against the
repository:

1. Receive a task — **implemented** (`JEVTask`).
2. Determine basic task requirements — **implemented**
   (`ModelRequirements`).
3. Ask the model controller for an appropriate model — **implemented**
   (`FreeModelController` + `ModelResolver`).
4. Select an execution backend — **implemented** for OpenCode; Codex
   deferred.
5. Construct an explicit execution policy — **implemented**
   (`ExecutionPolicy`, `READ_ONLY`).
6. Invoke the selected backend — **implemented** (`OpenCodeClient.run`,
   with the policy enforced at the boundary).
7. Collect structured results — **implemented** (`JEVResult`,
   `OpenCodeResult`, `DispatchRecord`).
8. Classify success/failure — **implemented for the execution only**
   (raw state); classification into task/project outcomes is the PM
   interpretation layer's job.
9. Decide whether retry/fallback is permitted — **not implemented**; JEV
   performs no retries and no fallback (§13).
10. Return the result to the caller — **implemented**
    (`Orchestrator.dispatch` → `DispatchRecord`).

The original caveat that "the exact JEV schema should be designed during
implementation rather than prematurely hard-coded" is now resolved: the
concrete `JEVTask` / `JEVResult` / `DispatchRecord` schemas exist.

Current execution stance, all verified:

- `read_only` execution only. This is the only mode that exists.
- The other planned modes — `plan`, `modify`, `test`, `commit` — are
  **not implemented**; any write-capable policy is rejected.
- No unrestricted `--auto` mode.
- No automatic Git commits.
- No unrestricted shell authority.
- No automatic paid-model routing.

---

# 10. JEV Execution Policy

**Implemented.** The policy contract lives in `router/policy.py` and covers:

```text
read
edit
shell
network
git
commit
workspace
timeout
max_attempts
```

The policy is enforced **outside the model's natural-language instructions**,
at two enforcement points sharing one definition so they cannot drift:

1. Upstream: `router.jev.JEV._validate_policy` refuses write-capable
   policies before dispatch.
2. At the execution boundary: `providers.opencode.OpenCodeClient.run`
   re-checks the governing policy immediately before the OpenCode process
   would be spawned, and fails closed for unknown/non-policy objects — this
   check survives bypassing the upstream validation.

Dedicated security tests exist (`tests/test_read_only_boundary.py`), and
repository state can be verified before and after execution.

Integration-test status, stated accurately:

- Offline, subprocess-monkeypatched boundary and pipeline tests exist and
  pass: read-only inspection flows end-to-end through JEV without touching
  the repository.
- A **live** run against a real OpenCode process has not been verified in
  this repository and remains future work (§13).

---

# 11. Security Principles

- Credentials never belong in project configuration.
- Secrets must be stored through the credential abstraction.
- ChatGPT subscription authentication must never be repurposed as API authentication.
- Providers must remain replaceable.
- Execution permissions must be explicit.
- Workspace boundaries must be explicit.
- Dangerous actions require a higher authorization level.
- Git commits require explicit policy/approval.
- Paid routing requires explicit configuration.
- Model availability must be discovered dynamically.
- External agent permissions are not considered a complete sandbox.
- Prefer reversible operations.
- Test before enabling additional authority.
- Providers never mutate authoritative project/task state.
- The orchestrator is mechanical: no decisions, no interpretation, no
  forged artifacts.
- Raw execution state/results never become authoritative lifecycle state
  without passing through PM interpretation.
- At most one active execution lease exists per task/version; an expired or
  `UNKNOWN` execution must be reconciled, and a replacement requires
  explicit PM authorization — a duplicate execution is never created
  silently.
- Governance code must not depend on execution code, and execution code
  must not import governance — enforced by tests, not by convention.

---

# 12. Development Workflow

For each milestone:

```text
Plan (PM)
 ↓
Authorize execution (artifacts + lease)
 ↓
Implement small change
 ↓
Run focused tests
 ↓
Run full test suite
 ↓
Verify behavior
 ↓
Inspect git diff
 ↓
PM interprets the result (protocol.interpretation) and applies any
authoritative state change or commit
```

Implementation work does not commit and does not change authoritative
lifecycle state; both are PM actions under V1.1.

Persistent architectural decisions should be recorded in this document.

The system should evolve incrementally rather than through a single large implementation.

---

# 13. Future Work

**Consistency with the task register:** `project/TASKS.md` is the
authoritative task register and currently contains TASK-001 through
TASK-008 (all `COMPLETED`) plus MVP-001, MVP-002, and MVP-004 (all
`COMPLETED`) grouped in MVP-BATCH-001.
MVP-003 (Dependency Graph + Batch Scheduler) is part of the approved
Autonomous Coding MVP plan and is also grouped in MVP-BATCH-001, but it
is `PLANNED` — not activated, not in the approved execution set, and
not a dependency or prerequisite of MVP-002 or MVP-004. The register,
not this document, is authoritative for lifecycle state. Nothing below
is scheduled, authorized, or represented as a task. New tasks must be
created and authorized by the PM; the presence of an item here does not
start work.

Deferred items, none of them implemented today:

- Retry/fallback decision-making (configuration keys exist; no retry loop)
- Codex integration (adapter exists, integration deferred)
- Paid model routing
- Advanced fallback strategies
- Stronger sandboxing beyond the read-only execution policy
- Execution modes beyond `read_only` (`plan`, `modify`, `test`, `commit`)
- A live end-to-end integration test against a real OpenCode process
- Voice input
- Speech-to-text
- TTS (configuration keys exist; no implementation)
- A general user-facing CLI (only the PM runtime CLI exists today:
  `python -m pm` propose/show/approve — MVP-001)
- Persistent programmatic task history (document records exist under
  `project/`, but no subsystem)
- More execution backends
- Advanced model-driven decision making for JEV
- Autonomous multi-step workflows

The immediate goal remains a reliable, testable:

**governance → mechanical orchestration → JEV → OpenRouter → OpenCode →
raw result → PM interpretation**

loop.
