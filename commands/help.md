---
description: Show planman usage guide and configuration reference
---

# Planman Help

Show the user the following help information:

---

## What is Planman?

Planman evaluates AI coding-agent implementation plans before you approve them. By default it uses cross-agent review: Claude Code plans are reviewed by Codex, and Codex plans are reviewed by Claude Code. Low-scoring plans are rejected with actionable feedback. After a configurable number of rounds, you decide.

## Prerequisites

1. **OpenAI Codex CLI** for Codex review: `npm install -g @openai/codex`
2. **Claude Code CLI** for Claude review
3. Authenticate the evaluator CLI you intend to use

No API keys are needed for Codex review — Codex uses your ChatGPT subscription. Claude review uses your local Claude Code authentication.

## How It Works

1. The host agent produces a plan
2. Planman detects the host automatically
3. Planman sends the plan to the resolved evaluator (`auto`, `codex`, or `claude`)
4. The evaluator scores the plan on 5 criteria (completeness, correctness, sequencing, risk awareness, clarity)
5. **Round 1**: Mandatory review — plan always gets scored feedback, regardless of score
6. **Round 2+**: Score >= threshold (default 7/10) → plan passes. Below → rejected with feedback, the host agent revises
7. After max rounds (default 3): You decide whether to proceed

Claude Code files in `.claude/plans/` are deterministically treated as plans. Codex-hosted sessions evaluate only clearly plan-like content and fail open if no plan is found.

## Configuration

Settings are loaded from env vars (highest priority), `.planman.jsonc`, or legacy `.claude/planman.jsonc`:

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `threshold` | `PLANMAN_THRESHOLD` | `7` | Minimum score (1-10) to pass |
| `max_rounds` | `PLANMAN_MAX_ROUNDS` | `3` | Rounds before you decide |
| `min_rounds` | `PLANMAN_MIN_ROUNDS` | `0` | Minimum rounds before plan can pass |
| `model` | `PLANMAN_MODEL` | *(evaluator default)* | Override evaluator model |
| `evaluator` | `PLANMAN_EVALUATOR` | `auto` | `auto`, `codex`, or `claude` |
| `codex_bin` | `PLANMAN_CODEX_BIN` | `codex` | Codex CLI binary/path |
| `claude_bin` | `PLANMAN_CLAUDE_BIN` | `claude` | Claude Code CLI binary/path |
| `fail_open` | `PLANMAN_FAIL_OPEN` | `true` | Pass if evaluator fails |
| `enabled` | `PLANMAN_ENABLED` | `true` | Master switch |
| `custom_rubric` | `PLANMAN_RUBRIC` | *(built-in)* | Custom evaluation rubric |
| `verbose` | `PLANMAN_VERBOSE` | `false` | Debug output to stderr |
| `source_verify` | `PLANMAN_SOURCE_VERIFY` | `true` | Evaluator verifies plan against actual source files |
| `stress_test` | `PLANMAN_STRESS_TEST` | `false` | Stress-test rounds (`false`/`true`/number N) |
| `context` | `PLANMAN_CONTEXT` | *(empty)* | Project context for evaluator |
| `plan_dirs` | `PLANMAN_PLAN_DIRS` | `[]` | Additional plan directories to monitor |
| `exec_patterns` | `PLANMAN_EXEC_PATTERNS` | `[]` | Bash command regex patterns that trigger evaluation |
| `skill_patterns` | `PLANMAN_SKILL_PATTERNS` | `[]` | Skill name regex patterns that trigger evaluation |

### Quick Start

Run `/planman:init` in Claude Code to create `.planman.jsonc` with all settings and descriptions. In Codex, create the same file manually or use `PLANMAN_*` environment variables.

### Example `.planman.jsonc`

```jsonc
{
  // Minimum score to pass (default: 7)
  "threshold": 8,
  "max_rounds": 2,
  "verbose": true
}
```

`stress_test` accepts `false` (off), `true` (1 round), or a number N (N stress-test rounds). Stress-test rounds skip the evaluator and auto-reject with the stress-test prompt. External evaluation begins at round N+1.

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

These are Claude Code slash commands. Codex uses the installed Stop hook plus `.planman.jsonc` or `PLANMAN_*` environment variables.

- `/planman:status` — Show status and effective configuration
- `/planman:help` — This help page
- `/planman:init` — Create `.planman.jsonc` with all defaults
- `/planman:clear` — Clear session state (reset evaluation rounds)
