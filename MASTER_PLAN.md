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
- Control Codex CLI when appropriate
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
        OpenRouter     Codex CLI    Other tools/
        models         execution    providers
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

Codex is an execution/coding capability.

---

# 4. JEV

JEV is intended to become the primary **decision/orchestration layer**.

JEV should eventually determine:

- Task type
- Appropriate model
- Provider
- Whether Codex should be used
- Whether a free model is sufficient
- Whether a stronger model is justified
- Retry/fallback strategy
- Verification requirements
- Whether human approval is required

Conceptually:

```text
User task
    ↓
JEV
    ↓
Decision
    ├── Model
    ├── Provider
    ├── Codex?
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
      Codex CLI
          ↓
Implementation
          ↓
Tests
          ↓
Verification
```

Rather than manually implementing the entire system ourselves, establish a capable low-cost/free controller and allow it to assist with implementation.

The controller must operate within explicit security and cost boundaries.

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

# 8. Phase B — Configuration & Security

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

Current unit-test status:

```text
18/18 passing
```

Python compilation:

```text
PASS
```

---

# 9. KWallet

KWallet is the preferred local credential backend on this Steam Deck.

Verified:

```text
KWallet enabled       YES
KWallet daemon        RUNNING
Wallet                kdewallet
Folder                ai-router
D-Bus access          WORKING
Write                 VERIFIED
Read                  VERIFIED
```

A fake credential was successfully written and read.

The remaining implementation task is to use the confirmed KWallet method:

```text
org.kde.KWallet.removeEntry(...)
```

Then:

1. Delete the fake credential
2. Verify deletion
3. Add mocked KWallet tests
4. Ensure normal unit tests never touch the real wallet

No test credentials should remain in KWallet.

---

# 10. Immediate Next Step

Finish the credential lifecycle.

Required operations:

```text
set()
get()
delete()
```

using the actual KWallet D-Bus interface.

Then run:

```text
Python compile
Unit tests
Live KWallet cleanup test
Full test suite
git diff --check
```

Only after this passes should Phase B be committed.

---

# 11. OpenRouter Client

Build the smallest useful OpenRouter client.

Requirements:

- API key retrieved through credential store
- No credentials in source
- No credentials in logs
- No credentials in Git
- Configurable model
- Configurable timeout
- Clear error classification
- JSON response parsing
- Model availability handling
- No accidental paid requests

Initial target:

```bash
ai "Hello"
```

Expected behavior:

```text
AI response
```

Diagnostic information should eventually be available:

```text
Provider: OpenRouter
Model: <model>
Cost mode: FREE
```

without exposing credentials.

---

# 12. Free Model Discovery

OpenRouter's free-model catalogue is dynamic.

Do not permanently hard-code today's free models.

Discovery should consider:

- Availability
- Context length
- Coding/tool capability
- Reliability
- Latency
- Free/paid status
- Model limitations

The system must tolerate models disappearing or changing.

---

# 13. Free Controller

Establish a free/low-cost model capable of acting as the initial controller.

Its role is to:

- Interpret tasks
- Help select tools/models
- Assist with implementation
- Potentially drive Codex
- Operate under cost and security restrictions

The controller should not have unrestricted authority.

---

# 14. JEV Integration

Integrate JEV after basic model access works.

JEV should receive structured information such as:

```text
task
constraints
available models
cost policy
execution capabilities
project context
```

and produce a structured decision such as:

```text
task_type
selected_model
provider
use_codex
execution_plan
fallback_policy
verification_required
```

Exact fields may change during implementation.

---

# 15. Codex Integration

Codex CLI is an execution capability.

Target workflow:

```text
User
  ↓
JEV
  ↓
Controller
  ↓
Codex CLI
  ↓
Inspect project
  ↓
Implement
  ↓
Run tests
  ↓
Report
  ↓
Verification
```

Rules:

- Work only in intended projects
- Do not modify unrelated repositories
- Do not expose secrets
- Review changes
- Run tests
- Review Git diff
- Avoid destructive operations
- Do not silently spend money

---

# 16. Router

The router will eventually handle:

## Provider selection

```text
OpenRouter
OpenAI API
Codex/local execution
Other providers if added
```

## Model selection

Based on:

- Task
- User preference
- Free-only policy
- Availability
- Capability
- Cost
- Reliability

## Fallback

Fallback conditions may include:

```text
Rate limit
Timeout
Connection failure
Server error
Model unavailable
Provider outage
```

Do not blindly retry:

```text
Invalid credentials
Invalid configuration
Permanent authorization failures
```

---

# 17. Cost Safety

Default configuration:

```text
free_only = true
paid_routing_enabled = false
```

The system must clearly distinguish:

```text
FREE
LOW COST
PAID
UNKNOWN
```

Paid routing requires explicit configuration.

Local application settings cannot guarantee provider-side spending limits.

Provider billing controls remain authoritative.

---

# 18. CLI

Target interface:

```bash
ai "Explain quantum computing simply"

ai --model MODEL "Explain Proton on Steam Deck"

ai --free-only "..."

ai --status

ai --config

ai --list-models

ai --chat

cat file.txt | ai
```

Later:

```bash
ai --voice
ai --voice --speak
```

The text CLI must remain fully usable without voice.

---

# 19. Voice

Voice comes after the text/AI core works.

Target architecture:

```text
Microphone
    ↓
PipeWire
    ↓
Recorder
    ↓
STT
    ↓
JEV
    ↓
Model / Codex
```

Prefer local STT where practical.

Do not permanently store raw recordings unless explicitly requested.

---

# 20. TTS

TTS is optional.

Target:

```bash
ai --voice --speak
```

Priority:

```text
Text input
    ↓
AI
    ↓
Voice input
    ↓
TTS
```

TTS must not delay the core system.

---

# 21. Testing

## Offline tests

Must not consume API quota.

Test:

- Configuration
- CLI parsing
- Routing
- Model selection
- Redaction
- Error handling
- Credential abstraction

## Live tests

Clearly marked because they may consume API quota.

Examples:

- OpenRouter request
- Model availability
- JEV integration
- Codex integration
- End-to-end workflow

Live tests must never silently run as part of ordinary unit tests.

---

# 22. Security

Never:

- Put API keys in source
- Put secrets in Git
- Print credentials
- Log Authorization headers
- Store browser session cookies
- Circumvent subscription/API restrictions
- Store raw recordings unnecessarily
- Modify unrelated repositories
- Require unnecessary root privileges

Preferred credential path:

```text
KWallet
   ↓
Credential abstraction
   ↓
Provider client
```

---

# 23. SteamOS

Keep the application primarily in user space.

Preferred:

```text
~/.local/bin/
~/.config/ai-router/
~/.cache/ai-router/
~/ai-router/
```

Avoid unnecessary modifications to:

```text
/usr
/etc
system partitions
immutable SteamOS components
```

The final system should be recoverable after normal SteamOS updates.

---

# 24. Git Discipline

Before milestones:

```bash
git status
git diff --check
git diff
```

Then commit.

Milestones:

```text
Phase A  Foundation
Phase B  Configuration/security
Phase C  OpenRouter client
Phase D  Free controller
Phase E  JEV integration
Phase F  Codex orchestration
Phase G  Routing/fallback
Phase H  CLI polish
Phase I  Voice
Phase J  Integration testing
Phase K  SteamOS hardening
```

---

# 25. Development Method

Do not dump the entire implementation onto the Deck.

Use:

```text
Plan
  ↓
Explain
  ↓
Implement small piece
  ↓
Test
  ↓
Inspect
  ↓
Commit
  ↓
Next piece
```

If something fails:

```text
Stop
  ↓
Diagnose
  ↓
Fix
  ↓
Retest
```

Do not build new layers on top of broken foundations.

---

# 26. Current Status

```text
Foundation
████████████████████ 100%

Configuration/security
███████████████████░  ~90%

OpenRouter client
░░░░░░░░░░░░░░░░░░░░   0%

Free controller
░░░░░░░░░░░░░░░░░░░░   0%

JEV
░░░░░░░░░░░░░░░░░░░░   0%

Codex orchestration
░░░░░░░░░░░░░░░░░░░░   0%

Voice
░░░░░░░░░░░░░░░░░░░░   0%
```

These are stage indicators, not an overall percentage-complete estimate.

---

# 27. Immediate Action List

## NOW

1. Implement KWallet `removeEntry`
2. Delete fake credential
3. Verify deletion
4. Add mocked credential-store tests
5. Run full test suite
6. Run `git diff --check`
7. Review changes
8. Commit Phase B

## NEXT

9. Implement minimal OpenRouter client
10. Store OpenRouter credential through KWallet
11. Make one controlled live request
12. Verify free-model handling
13. Implement dynamic free-model discovery
14. Establish free controller

## THEN

15. Integrate JEV
16. Give JEV controlled Codex access
17. Implement routing/fallback
18. Build CLI UX
19. Add voice
20. Add optional TTS
21. Perform full integration/security testing
22. Harden for SteamOS updates
23. Document installation/recovery/uninstall

---

# 28. Definition of Done

The system should eventually support a workflow like:

```bash
ai "Build me a small Python utility that does X."
```

and be able to:

```text
Understand task
      ↓
JEV decides strategy
      ↓
Select appropriate model
      ↓
Respect cost policy
      ↓
Delegate coding to Codex when appropriate
      ↓
Modify intended project
      ↓
Run tests
      ↓
Verify result
      ↓
Report concise result
```

while maintaining:

- Secure credentials
- Provider independence
- Free/paid safeguards
- Model fallback
- Test coverage
- Git discipline
- SteamOS resilience
- Human approval where appropriate

---

# 29. Persistent Project Rule

**This document is the authoritative master plan.**

If the architecture or workflow changes materially, update this document.

Major architectural changes should be marked:

**PERSIST**
