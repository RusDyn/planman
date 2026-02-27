---
description: Show planman usage guide and configuration reference
---

# Planman Help

Show the user the following help information:

---

## What is Planman?

Planman is a Claude Code plugin that evaluates your implementation plans before you approve them. It uses **OpenAI Codex CLI** as an external evaluator — when Claude exits plan mode, Planman intercepts the ExitPlanMode call, sends the plan to Codex for scoring, and rejects low-scoring plans with actionable feedback. Claude then revises and re-presents. After a configurable number of rounds, you decide.

## Prerequisites

1. **OpenAI Codex CLI**: `npm install -g @openai/codex`
2. **ChatGPT subscription** (Plus, Pro, or Team)
3. **Login once**: Run `codex` and authenticate via browser

No API keys needed — Codex uses your ChatGPT subscription.

## How It Works

1. Claude writes a plan to `.claude/plans/` (PostToolUse(Write) records the path)
2. Claude calls ExitPlanMode to present the plan
3. Planman intercepts ExitPlanMode and sends the plan to `codex exec` for evaluation
4. Codex scores the plan on 5 criteria (completeness, correctness, sequencing, risk awareness, clarity)
5. **Round 1**: Mandatory review — plan always gets scored feedback, regardless of score
6. **Round 2+**: Score >= threshold (default 7/10) → plan passes. Below → rejected with feedback, Claude revises
7. After max rounds (default 3): You decide whether to proceed

**Plan-mode only.** Files in `.claude/plans/` are deterministically treated as plans — no LLM-based detection.

## Configuration

Settings are loaded from env vars (highest priority) or `.claude/planman.jsonc`:

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `threshold` | `PLANMAN_THRESHOLD` | `7` | Minimum score (1-10) to pass |
| `max_rounds` | `PLANMAN_MAX_ROUNDS` | `3` | Rounds before you decide |
| `model` | `PLANMAN_MODEL` | *(codex default)* | Override Codex model |
| `fail_open` | `PLANMAN_FAIL_OPEN` | `true` | Pass if Codex fails |
| `enabled` | `PLANMAN_ENABLED` | `true` | Master switch |
| `custom_rubric` | `PLANMAN_RUBRIC` | *(built-in)* | Custom evaluation rubric |
| `codex_path` | `PLANMAN_CODEX_PATH` | `codex` | Path to codex binary |
| `verbose` | `PLANMAN_VERBOSE` | `false` | Debug output to stderr |
| `timeout` | `PLANMAN_TIMEOUT` | `120` | Seconds for codex subprocess |
| `source_verify` | `PLANMAN_SOURCE_VERIFY` | `true` | Codex verifies plan against actual source files |
| `stress_test` | `PLANMAN_STRESS_TEST` | `false` | Auto-reject first plan with stress-test prompt |
| `stress_test_prompt` | `PLANMAN_STRESS_TEST_PROMPT` | *(built-in)* | Custom first-round rejection message |
| `context` | `PLANMAN_CONTEXT` | *(empty)* | Project context for evaluator |

### Quick Start

Run `/planman:init` to create `.claude/planman.jsonc` with all settings and descriptions.

### Example `.claude/planman.jsonc`

```jsonc
{
  // Minimum score to pass (default: 7)
  "threshold": 8,
  "max_rounds": 2,
  "verbose": true
}
```

When `stress_test` is enabled, `max_rounds` is automatically clamped to a minimum of 2.

## Tips

- Set `PLANMAN_THRESHOLD=10` to always reject (testing)
- Set `PLANMAN_THRESHOLD=1` to always pass (testing)
- Set `PLANMAN_MAX_ROUNDS=1` to get just one round of feedback
- Set `PLANMAN_CODEX_PATH=/nonexistent` to test fail-open behavior
- Set `PLANMAN_VERBOSE=true` to see detailed debug output

## Commands

- `/planman:status` — Show status and effective configuration
- `/planman:help` — This help page
- `/planman:init` — Create `.claude/planman.jsonc` with all defaults
- `/planman:clear` — Clear session state (reset evaluation rounds)
