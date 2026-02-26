---
description: Show planman usage guide and configuration reference
---

# Planman Help

Show the user the following help information:

---

## What is Planman?

Planman is a Claude Code plugin that evaluates your implementation plans before you approve them. It uses **OpenAI Codex CLI** as an external evaluator — when Claude presents a plan, Planman intercepts it, sends it to Codex for scoring, and rejects low-scoring plans with actionable feedback. Claude then revises and re-presents. After a configurable number of rounds, you decide.

## Prerequisites

1. **OpenAI Codex CLI**: `npm install -g @openai/codex`
2. **ChatGPT subscription** (Plus, Pro, or Team)
3. **Login once**: Run `codex` and authenticate via browser

No API keys needed — Codex uses your ChatGPT subscription.

## How It Works

1. Claude finishes responding (Stop hook fires)
2. Planman checks if the response looks like a plan (local heuristics, microseconds)
3. If it's a plan, sends it to `codex exec` for evaluation
4. Codex scores the plan on 5 criteria (completeness, correctness, sequencing, risk awareness, clarity)
5. Score >= threshold (default 7/10): Plan passes, you see an approval message
6. Score < threshold: Plan is rejected with specific feedback, Claude revises
7. After max rounds (default 3): You decide whether to proceed

## Configuration

Settings are loaded from env vars (highest priority) or `.claude/planman.json`:

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
| `timeout` | `PLANMAN_TIMEOUT` | `90` | Seconds for codex subprocess |

### Example `.claude/planman.json`

```json
{
  "threshold": 8,
  "max_rounds": 2,
  "verbose": true
}
```

## Tips

- Set `PLANMAN_THRESHOLD=10` to always reject (testing)
- Set `PLANMAN_THRESHOLD=1` to always pass (testing)
- Set `PLANMAN_MAX_ROUNDS=1` to get just one round of feedback
- Set `PLANMAN_CODEX_PATH=/nonexistent` to test fail-open behavior
- Set `PLANMAN_VERBOSE=true` to see detailed debug output

## Commands

- `/planman` — Show status and effective configuration
- `/planman-help` — This help page
