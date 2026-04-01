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
| `min_rounds` | `PLANMAN_MIN_ROUNDS` | `0` | Minimum rounds before plan can pass |
| `model` | `PLANMAN_MODEL` | *(codex default)* | Override Codex model |
| `fail_open` | `PLANMAN_FAIL_OPEN` | `true` | Pass if Codex fails |
| `enabled` | `PLANMAN_ENABLED` | `true` | Master switch |
| `custom_rubric` | `PLANMAN_RUBRIC` | *(built-in)* | Custom evaluation rubric |
| `verbose` | `PLANMAN_VERBOSE` | `false` | Debug output to stderr |
| `source_verify` | `PLANMAN_SOURCE_VERIFY` | `true` | Codex verifies plan against actual source files |
| `stress_test` | `PLANMAN_STRESS_TEST` | `false` | Stress-test rounds (`false`/`true`/number N) |
| `context` | `PLANMAN_CONTEXT` | *(empty)* | Project context for evaluator |
| `plan_dirs` | `PLANMAN_PLAN_DIRS` | `[]` | Additional plan directories to monitor |
| `exec_patterns` | `PLANMAN_EXEC_PATTERNS` | `[]` | Bash command regex patterns that trigger evaluation |
| `skill_patterns` | `PLANMAN_SKILL_PATTERNS` | `[]` | Skill name regex patterns that trigger evaluation |

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

`stress_test` accepts `false` (off), `true` (1 round), or a number N (N stress-test rounds). Stress-test rounds skip Codex and auto-reject with the stress-test prompt. Codex evaluation begins at round N+1.

## Tips

- Set `PLANMAN_THRESHOLD=10` to always reject (testing)
- Set `PLANMAN_THRESHOLD=1` to always pass (testing)
- Set `PLANMAN_MAX_ROUNDS=1` to get just one round of feedback
- Set `PLANMAN_VERBOSE=true` to see detailed debug output

## Third-Party Plugin Support (e.g., OMC)

Planman can gate execution for third-party planning tools like [oh-my-claudecode](https://github.com/yeachan-heo/oh-my-claudecode) that write plans to custom directories and launch execution via Bash commands or Skill invocations.

### OMC Configuration Example

```jsonc
{
  "plan_dirs": [".omc/plans"],
  "exec_patterns": ["omc\\s+(team|ralphthon)"],
  "skill_patterns": ["oh-my-claudecode:(ralph|autopilot|ultrawork|ralplan)"]
}
```

- `plan_dirs`: Directories to monitor for plan files (in addition to `.claude/plans/`)
- `exec_patterns`: Regex patterns matching Bash commands that should trigger plan evaluation before execution
- `skill_patterns`: Regex patterns matching Skill names that should trigger plan evaluation

**Limitation**: OMC's keyword-triggered execution (typing "ralph" without `/` prefix) bypasses tool-level hooks and cannot be automatically gated. Use `/ralph` (Skill invocation) for gated execution.

Env vars accept comma-separated values or JSON arrays: `PLANMAN_EXEC_PATTERNS='["pat1","pat2"]'`

## Commands

- `/planman:status` — Show status and effective configuration
- `/planman:help` — This help page
- `/planman:init` — Create `.claude/planman.jsonc` with all defaults
- `/planman:clear` — Clear session state (reset evaluation rounds)
