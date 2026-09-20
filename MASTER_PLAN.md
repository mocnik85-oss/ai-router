# AI Router / JEV — Current Master Plan

**Project:** Steam Deck personal AI coding/orchestration system  
**Platform:** SteamOS / Steam Deck  
**Status:** Active development

> **This document is the authoritative project plan.**
> It supersedes earlier AI-router plans where they conflict with this document.

---

## 1. Objective

Build a secure, maintainable AI orchestration system for the Steam Deck that can:

- Use OpenRouter models
- Prefer free/low-cost models
- Dynamically discover available models
- Use paid models only when explicitly permitted
- Use JEV as a decision/orchestration layer
- Use OpenCode as the current execution backend
- Route tasks between models/providers
- Retry and fall back intelligently
- Securely store credentials
- Accept text and eventually voice input
- Optionally provide TTS
- Work with local coding projects
- Test and verify its own work
- Survive normal SteamOS updates as far as practical

The system must remain modular and provider-independent.

---

# 2. Core Architecture

The fundamental separation is:

**Decision-making ≠ model access ≠ execution**

```text
                        USER
                         │
                    text / voice
                         │
                         ▼
                ┌───────────────┐
                │      JEV      │
                │ Decision /    │
                │ Orchestration │
                └───────┬───────┘
                        │
           ┌────────────┼────────────┐
           ▼            ▼            ▼
      OpenRouter     OpenCode     Other tools/
       models        execution     providers
           │            │
           │            ▼
           │        Local project
           │
           ▼
      Free / paid
      model pool
```

JEV must not become tightly coupled to a single model or provider.

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

**OpenRouter API key**

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

JEV is intended to become the primary **decision/orchestration layer**.

JEV should eventually determine:

- Task type
- Appropriate model
- Provider
- Whether an execution agent should be used
- Whether OpenCode should be used
- Whether Codex should be used when available
- Whether a free model is sufficient
- Whether a stronger model is justified
- Retry/fallback strategy
- Verification requirements
- Whether human approval is required

OpenCode is the current preferred execution backend.

Codex remains an optional future backend and is not required for the current implementation.

Conceptually:

```text
User task
   ↓
JEV
   ↓
Decision
   ├── Model
   ├── Provider
   ├── Execution agent
   ├── Execution plan
   ├── Fallback
   └── Verification
```

The exact JEV schema should be designed during implementation rather than prematurely hard-coded.

---

# 5. Strategic Approach

The preferred development strategy is:

```text
Free/low-cost controller
        ↓
       JEV
        ↓
     OpenCode
        ↓
Implementation
        ↓
Tests
        ↓
Verification
```

Rather than manually implementing the entire system ourselves, establish a capable low-cost/free controller and allow it to assist with implementation.

The controller must operate within explicit security and cost boundaries.

Codex remains a future optional execution backend:

```text
       JEV
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

Current structure:

```text
~/ai-router/
├── .git/
├── .gitignore
├── README.md
├── app/
├── providers/
├── router/
├── tests/
├── tts/
└── voice/
```

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

**STATUS: ALMOST COMPLETE**

Implemented:

- TOML configuration
- Configuration validation
- Safe defaults
- Free-only mode
- Paid-routing flag
- Retry configuration
- Timeout configuration
- Voice configuration
- Credential abstraction
- Memory credential backend
- KWallet backend
- Secret redaction
- Unit tests

---

## Phase C — OpenRouter

**STATUS: COMPLETE**

Implemented:

- OpenRouter client
- Credential-store integration
- Public model discovery
- Dynamic free-model discovery
- Model metadata normalization
- Capability detection
- Error classification
- Offline unit tests

The controller must not hard-code today's free-model catalogue because availability is dynamic.

---

## Phase D — Free Model Controller

**STATUS: COMPLETE**

Implemented:

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

Implemented:

- OpenCode CLI adapter
- Explicit model selection
- JSON event-stream parsing
- Structured execution result
- Timeout handling
- Process error handling
- Offline unit tests

Current preferred execution backend.

The adapter does **not** grant OpenCode unrestricted authority.

OpenCode permissions are not treated as a complete security boundary. JEV must eventually enforce execution policy independently.

### Codex

**STATUS: DEFERRED**

A Codex adapter exists and has been tested, but integration is paused until Codex allowance becomes available.

JEV must not depend on Codex.

---

# 8. Current Test Baseline

Current verified baseline:

```text
55 tests passed
```

Additional checks completed:

```text
python -m compileall -q app providers router tests
git diff --check
```

The repository checkpoint containing the execution adapters is:

```text
dea78a0 Add OpenCode and Codex execution adapters
```

---

# 9. Next Phase — JEV v0.1

JEV v0.1 should initially be a **deterministic policy/orchestration layer**, not another LLM.

Initial responsibilities:

1. Receive a task.
2. Determine basic task requirements.
3. Ask the model controller for an appropriate model.
4. Select an execution backend.
5. Construct an explicit execution policy.
6. Invoke the selected backend.
7. Collect structured results.
8. Classify success/failure.
9. Decide whether retry/fallback is permitted.
10. Return the result to the caller.

Initial execution modes should be conservative:

- `read_only`
- `plan`
- `modify`
- `test`
- `commit`

JEV v0.1 should begin with **read-only execution only**.

No unrestricted `--auto` mode.

No automatic Git commits.

No unrestricted shell authority.

No automatic paid-model routing.

---

# 10. JEV Execution Policy

The execution policy should eventually control at least:

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

The policy must be enforced outside the model's natural-language instructions.

The first real integration test should be:

```text
User task
   ↓
JEV
   ↓
Free model selection
   ↓
OpenCode
   ↓
Read-only repository inspection
   ↓
Structured JSON result
   ↓
JEV
```

The test must verify that the agent can inspect the repository without modifying it.

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

---

# 12. Development Workflow

For each milestone:

```text
Plan
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
Commit
```

Persistent architectural decisions should be recorded in this document.

The system should evolve incrementally rather than through a single large implementation.

---

# 13. Future Work

Deferred until the core JEV loop is stable:

- Codex integration
- Paid model routing
- Advanced fallback strategies
- Stronger sandboxing
- Voice input
- Speech-to-text
- TTS
- Persistent task history
- More execution backends
- Advanced JEV model-driven decision making
- Autonomous multi-step workflows

The immediate goal is a reliable, testable:

**JEV → OpenRouter → OpenCode → verification**

loop.
