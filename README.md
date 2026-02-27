# The Man with The Plan

<p align="center">
  <img src="assets/planman-hero.png" alt="Planman — The Man With The Plan" width="600">
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/python-3.8%2B-blue.svg" alt="Python 3.8+">
  <img src="https://img.shields.io/badge/claude--code-plugin-blueviolet.svg" alt="Claude Code Plugin">
</p>

A quality gate for AI-generated plans. This [Claude Code](https://docs.anthropic.com/en/docs/claude-code) plugin sends implementation plans to [OpenAI Codex CLI](https://github.com/openai/codex) for independent scoring, automatically rejecting low-quality plans with actionable feedback — so Claude iterates before you review.

> *"Every plan deserves a second opinion."*

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

## Quick Start

1. Install [Codex CLI](https://github.com/openai/codex):
   ```bash
   npm install -g @openai/codex
   ```
2. Authenticate: run `codex` once and log in via browser
3. Add planman to Claude Code:
   ```
   /plugin marketplace add RusDyn/planman
   /plugin install planman@planman
   ```
4. Restart Claude Code

That's it. The next time Claude exits plan mode, planman evaluates the plan and blocks with feedback if the score is below threshold (default 7/10). Run `/planman:init` to customize settings.

> *"You're four steps from better plans."*

## Prerequisites

1. **OpenAI Codex CLI**:
   ```bash
   npm install -g @openai/codex
   ```

2. **ChatGPT subscription** (Plus, Pro, or Team)

3. **Login once**: Run `codex` and authenticate via browser

4. **Claude Code** with [plugin support](https://docs.anthropic.com/en/docs/claude-code)

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
| `context` | `PLANMAN_CONTEXT` | *(empty)* | Project context injected into evaluation prompt |

When `stress_test` is enabled, `max_rounds` is automatically clamped to a minimum of 2.

Run `/planman:init` to generate `.claude/planman.jsonc` with all settings and inline documentation.

## Scoring Rubric

> *"A plan that survives scrutiny is a plan worth building."*

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

All commands use the `planman:` namespace prefix:

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

## Uninstalling

```
/plugin uninstall planman@planman
```

This removes planman's hooks and commands. Your `.claude/planman.jsonc` config file is preserved — delete it manually if no longer needed.

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

## Contributing

> *"Got a plan to make planman better?"*

To get started:

1. Fork the repo and clone locally
2. Install test runner: `pip install pytest`
3. Run tests: `python3 -m pytest tests/ -v`
4. Make your changes — the plugin has **zero pip dependencies** (stdlib only, Python 3.8+), please keep it that way
5. Open a pull request against `main`

Please open an issue first for significant changes so we can discuss the approach.

## Support

- **Bug reports & feature requests**: [GitHub Issues](https://github.com/RusDyn/planman/issues)
- **Plugin docs**: [Claude Code Plugins](https://docs.anthropic.com/en/docs/claude-code)

## License

[MIT](LICENSE)
