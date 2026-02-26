# planman

An external AI plan evaluator plugin for [Claude Code](https://docs.anthropic.com/en/docs/claude-code).

When Claude presents an implementation plan, planman intercepts it, sends it to [OpenAI Codex CLI](https://github.com/openai/codex) for scoring, and rejects low-scoring plans with actionable feedback. Claude revises and re-presents. After a configurable number of rounds, you decide.

**No API keys required** — uses your ChatGPT subscription via the `codex` CLI.

## How It Works

Planman uses **two hooks** in a plan-mode-only architecture:

```
PostToolUse(Write) — records plan file path when Claude writes to .claude/plans/
  │
  ▼
PreToolUse(ExitPlanMode) — evaluates plan via codex when Claude exits plan mode
  │
  ├── Round 1: mandatory review — plan always gets scored feedback
  │     │
  │     ▼
  │   Claude revises plan based on feedback
  │     │
  │     ▼
  ├── Round 2+: passes if score >= threshold; rejected with feedback otherwise
  │     │
  │     ▼
  └── Max rounds exceeded → you decide whether to proceed
```

**Deterministic:** Files in `.claude/plans/` are always treated as plans — no LLM-based plan detection.

## Prerequisites

1. **OpenAI Codex CLI**:
   ```bash
   npm install -g @openai/codex
   ```

2. **ChatGPT subscription** (Plus, Pro, or Team)

3. **Login once**: Run `codex` and authenticate via browser

## Installation

### From GitHub

```bash
# Add the marketplace
/plugin marketplace add RusDyn/planman

# Install
/plugin install planman@planman
```

### Local Development

```bash
# Add the local directory as a marketplace
/plugin marketplace add /path/to/planman

# Install
/plugin install planman@planman
```

Then restart Claude Code.

## Configuration

Settings are loaded from env vars (highest priority) or `.claude/planman.jsonc`:

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `threshold` | `PLANMAN_THRESHOLD` | `7` | Minimum score (0-10) to pass; 0 = pass all |
| `max_rounds` | `PLANMAN_MAX_ROUNDS` | `3` | Evaluation rounds before you decide (1-100) |
| `model` | `PLANMAN_MODEL` | *(codex default)* | Override Codex model (`-m` flag) |
| `fail_open` | `PLANMAN_FAIL_OPEN` | `true` | Pass through if Codex fails |
| `enabled` | `PLANMAN_ENABLED` | `true` | Master switch |
| `custom_rubric` | `PLANMAN_RUBRIC` | *(built-in)* | Custom evaluation rubric |
| `codex_path` | `PLANMAN_CODEX_PATH` | `codex` | Path to codex binary (rejects `..` paths) |
| `verbose` | `PLANMAN_VERBOSE` | `false` | Debug output to stderr |
| `timeout` | `PLANMAN_TIMEOUT` | `90` | Seconds for codex subprocess (1-600; keep ≤ 90s, hook timeout is 120s) |
| `stress_test` | `PLANMAN_STRESS_TEST` | `false` | Auto-reject first plan with stress-test prompt (skips Codex on round 1) |
| `stress_test_prompt` | `PLANMAN_STRESS_TEST_PROMPT` | *(built-in)* | Custom first-round rejection message |

When `stress_test` is enabled, `max_rounds` is automatically clamped to a minimum of 2.

### Quick Start

```
/planman:init
```

Creates `.claude/planman.jsonc` with all available settings and their defaults. Edit the values you want to change — omitted or empty string values use built-in defaults.

## Scoring Rubric

Plans are scored on 5 criteria (0-2 each, 10 max):

| Criteria | 0 | 1 | 2 |
|----------|---|---|---|
| **Completeness** | Missing major pieces | Partial coverage | Addresses all requirements |
| **Correctness** | Technically flawed | Mostly correct | Sound approach |
| **Sequencing** | Broken dependencies | Mostly ordered | Logical step order |
| **Risk Awareness** | Ignores risks | Some awareness | Identifies edge cases |
| **Clarity** | Vague steps | Adequate detail | Precise and actionable |

### Custom Rubrics

Override the built-in rubric for domain-specific evaluation:

```bash
export PLANMAN_RUBRIC="Score the plan focusing on security implications, test coverage, and backwards compatibility. Be strict about migration safety."
```

Or in `.claude/planman.jsonc`:

```json
{
  "custom_rubric": "Score the plan focusing on..."
}
```

## Commands

| Command | Description |
|---------|-------------|
| `/planman:status` | Show status, codex version, effective config |
| `/planman:help` | Full usage guide |
| `/planman:init` | Create `.claude/planman.jsonc` with all defaults |
| `/planman:clear` | Clear session state (reset evaluation rounds) |

## Multi-Round Behavior

- **Round 1**: Mandatory review — plan always gets scored feedback, regardless of score
- **Round 2+**: Passes if score >= threshold; rejected with feedback otherwise
- **New plan file detected**: If Claude switches to a different plan file, round counter resets
- **Max rounds exceeded**: Plan blocks with a note — you decide whether to proceed

## Zero Friction Design

- **No API keys** — uses ChatGPT subscription via `codex` CLI
- **No pip dependencies** — stdlib only (Python 3.8+)
- **Fail-open by default** — Codex errors never block your workflow
- **Auto-detect codex** — if `codex` isn't installed, hook silently passes through
- **Plan-mode only** — deterministic detection via `.claude/plans/` path

## State Files

Session state is stored in the system temp directory (run `python3 -c "import tempfile; print(tempfile.gettempdir())"` to find it).
- Tracks round count, last score, and previous feedback
- Cleared when a plan passes evaluation or via `/planman:clear`
- Sessions are considered stale after 30 minutes of inactivity
- Safe to delete manually

## Plugin Structure

- `.claude-plugin/marketplace.json` — marketplace registry (used by `/plugin marketplace add`)
- `.claude-plugin/plugin.json` — plugin definition (hooks, commands, schemas)
- `hooks/hooks.json` — two hooks: PostToolUse(Write) + PreToolUse(ExitPlanMode)
- `scripts/` — hook implementation (Python, stdlib only)
  - `post_tool_hook.py` — records plan file path
  - `pre_exit_plan_hook.py` — evaluates plan via codex
  - `hook_utils.py` — shared evaluation logic
  - `evaluator.py` — codex subprocess wrapper
  - `state.py` — multi-round session state
  - `config.py` — configuration loader
  - `clear_state.py` — session cleanup utility
  - `run_hook.py` — hook entry point
- `schemas/` — JSON output schema for codex structured output
- `commands/` — slash commands (`/planman:status`, `/planman:help`, `/planman:init`, `/planman:clear`)

## Troubleshooting

### "Nothing happens" when Claude presents a plan

1. **Check planman is installed**: Run `/planman:status` — it should show status and config
2. **Enable verbose mode**: Set `PLANMAN_VERBOSE=true` in your env or `.claude/planman.jsonc`
3. **Check threshold**: A threshold of `0` disables evaluation. Set `PLANMAN_THRESHOLD=1` for testing

### I set `PLANMAN_VERBOSE=true` but see no output

Planman logs to stderr, which Claude Code only shows in its own verbose mode (`Ctrl+O`). With `PLANMAN_VERBOSE=true`, planman also emits a `systemMessage` visible in the chat (e.g., "Planman: not a plan (score 2/6)") so you can confirm it's running without `Ctrl+O`.

### Windows: "python3 not found"

On Windows, Python may be registered as `python` instead of `python3`.
Fix: create an alias, add `python3` to your PATH, or ensure Git for Windows includes Python.

### Manual testing

Test the PostToolUse hook (plan file tracking) by piping JSON to stdin:

```bash
echo '{"tool_name":"Write","tool_input":{"file_path":"/home/user/.claude/plans/test.md","content":"## Plan\n1. Step 1\n2. Step 2\n3. Step 3"},"session_id":"test"}' | python3 /path/to/planman/scripts/post_tool_hook.py
```

Replace `/path/to/planman` with the installed plugin path (check `/hooks` output for the exact location).

## Testing

```bash
# Run all tests
python3 -m pytest tests/ -v

# Or with unittest
python3 -m unittest discover tests/ -v
```

## License

MIT
