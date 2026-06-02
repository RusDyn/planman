"""Plan evaluation via coding-agent CLI subprocesses.

Calls Codex or Claude Code with structured JSON scoring.
"""

import json
import os
import shutil
import subprocess
import sys

PLUGIN_ROOT = (
    os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    or os.environ.get("CODEX_PLUGIN_ROOT", "")
    or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

_CODEX_BIN = "codex"
_CLAUDE_BIN = "claude"

_codex_available = None  # cached result
_claude_available = None  # cached result

# Known codex error patterns → actionable messages
_KNOWN_ERRORS = [
    ("usage limit", "ChatGPT usage limit reached. Upgrade at https://chatgpt.com/explore/pro or wait for reset."),
    ("rate limit", "Rate limited by OpenAI. Wait a few minutes and retry."),
    ("authentication", "Codex authentication failed. Run `codex auth` to re-authenticate."),
    ("could not connect", "Network error connecting to OpenAI. Check internet connection."),
    ("context length exceeded", "Plan + rubric too large for model context. Reduce plan size."),
    ("model not found", "Configured model not available. Check PLANMAN_MODEL setting."),
]


def _extract_codex_error(stderr):
    """Extract actionable error from codex stderr, or return tail snippet."""
    if not stderr:
        return "no stderr output"
    lower = stderr.lower()
    for pattern, message in _KNOWN_ERRORS:
        if pattern in lower:
            return message
    # Fallback: return tail where actual errors live (after banner + prompt echo)
    return f"...{stderr[-1500:]}"


def check_codex_installed(codex_path=None):
    """Check if codex CLI is installed. Result is cached."""
    global _codex_available
    if codex_path is None and _codex_available is not None:
        return _codex_available
    available = shutil.which(codex_path or _CODEX_BIN) is not None
    if codex_path is None:
        _codex_available = available
    return available


def check_claude_installed(claude_path=None):
    """Check if Claude Code CLI is installed. Result is cached."""
    global _claude_available
    if claude_path is None and _claude_available is not None:
        return _claude_available
    available = shutil.which(claude_path or _CLAUDE_BIN) is not None
    if claude_path is None:
        _claude_available = available
    return available


def reset_codex_cache():
    """Reset the cached codex availability check (for testing)."""
    global _codex_available
    _codex_available = None


def reset_claude_cache():
    """Reset the cached Claude CLI availability check (for testing)."""
    global _claude_available
    _claude_available = None


def detect_host(hook_input=None):
    """Infer the host agent from hook/runtime context."""
    if os.environ.get("CLAUDE_PLUGIN_ROOT"):
        return "claude"
    if os.environ.get("CODEX_HOME") or os.environ.get("CODEX_SANDBOX"):
        return "codex"
    if isinstance(hook_input, dict):
        tool_name = hook_input.get("tool_name")
        if tool_name == "ExitPlanMode" or "CLAUDE_PLUGIN_ROOT" in hook_input:
            return "claude"
        event = str(hook_input.get("hook_event_name") or hook_input.get("hookEventName") or "")
        if event in ("Stop", "UserPromptSubmit", "SessionStart"):
            return "codex"
        if "codex" in str(hook_input.get("source", "")).lower():
            return "codex"
    return "claude"


def resolve_evaluator(config, host="claude"):
    """Resolve configured evaluator provider for a detected host."""
    evaluator = getattr(config, "evaluator", "auto") or "auto"
    if evaluator != "auto":
        return evaluator
    return "claude" if host == "codex" else "codex"


def check_evaluator_installed(config, host="claude"):
    """Check whether the resolved evaluator CLI is installed."""
    provider = resolve_evaluator(config, host=host)
    if provider == "claude":
        return check_claude_installed(getattr(config, "claude_bin", None) or _CLAUDE_BIN)
    return check_codex_installed(getattr(config, "codex_bin", None) or _CODEX_BIN)


def build_prompt(plan_text, rubric, previous_feedback=None, round_number=1,
                  context=None, source_verify=True, provider="codex"):
    """Build the evaluation prompt for codex exec."""
    context_section = ""
    if context:
        context_section = f"## Project Context\n\n{context}\n\n"

    prompt = (
        "You are a senior software architect reviewing an implementation plan.\n\n"
        f"{context_section}"
        "Evaluate the following implementation plan using the rubric.\n\n"
        f"{rubric}\n\n"
        "## Feedback Guidelines\n\n"
        "- Prioritize issues: list critical problems first, minor improvements last\n"
        "- Be specific: reference exact steps by number\n"
        "- Be actionable: say what to change, not just what's wrong\n\n"
        f"## Plan to Evaluate (Round {round_number})\n\n"
        f"{plan_text}\n"
    )
    if source_verify:
        read_hint = "`cat <path>` or search with `rg`"
        if provider == "claude":
            read_hint = "read/search tools"
        prompt += (
            "\n## Source Verification\n\n"
            "You have read-only access to the project filesystem (cwd = project root).\n"
            "When evaluating correctness and completeness:\n"
            f"1. If the plan references specific files, verify them with {read_hint}\n"
            "2. Verify that APIs, function signatures, and module structures mentioned in the plan exist\n"
            "3. Check that the plan's assumptions about the codebase are accurate\n"
            "4. Note discrepancies between the plan and actual code as correctness issues\n"
            "5. Keep file reads focused — verify key claims, don't read the entire codebase\n"
        )
    if previous_feedback:
        prompt += (
            f"\n## Previous Feedback (Round {round_number - 1})\n\n"
            f"{previous_feedback}\n\n"
            "Assess: Which feedback items were addressed? Which were ignored? "
            "Focus new feedback on remaining and newly discovered issues.\n"
        )
    return prompt


def _schema_path():
    path = os.path.join(PLUGIN_ROOT, "schemas", "evaluation.json")
    if not os.path.isfile(path):
        return None, f"schema file not found: {path}. Check plugin root environment."
    return path, None


def _subprocess_env():
    env = os.environ.copy()
    env["_PLANMAN_EVALUATOR"] = "1"
    return env


def evaluate_plan(plan_text, config, previous_feedback=None, round_number=1, cwd=None, host="claude"):
    """Evaluate a plan with the configured provider and structured output.

    Returns (result_dict, error_string). On success error_string is None.
    On failure result_dict is None and error_string describes the problem.
    """
    provider = resolve_evaluator(config, host=host)
    if provider == "claude":
        return evaluate_plan_claude(plan_text, config, previous_feedback, round_number, cwd)
    return evaluate_plan_codex(plan_text, config, previous_feedback, round_number, cwd)


def evaluate_plan_codex(plan_text, config, previous_feedback=None, round_number=1, cwd=None):
    """Evaluate a plan via codex exec with structured output."""
    codex_bin = getattr(config, "codex_bin", None) or _CODEX_BIN
    if not check_codex_installed(codex_bin):
        return None, f"codex CLI not found. Install: npm install -g @openai/codex"

    prompt = build_prompt(
        plan_text, config.rubric, previous_feedback, round_number,
        context=config.context,
        source_verify=getattr(config, "source_verify", True),
        provider="codex",
    )

    _MAX_PROMPT_SIZE = 2_000_000  # 2MB hard cap
    if len(prompt) > _MAX_PROMPT_SIZE:
        return None, f"prompt too large ({len(prompt) // 1024}KB > 2MB). Reduce max_rounds or plan size."

    effective_timeout = 570  # 600s hook timeout − 30s margin

    schema_path, schema_error = _schema_path()
    if schema_error:
        return None, schema_error

    cmd = [
        codex_bin,
        "exec", "-",                          # Read prompt from stdin
        "--output-schema", schema_path,
        "--sandbox", "read-only",
        "--skip-git-repo-check",
        "--ephemeral",                        # Don't persist session files
        "--disable", "hooks",
    ]
    if config.model:
        cmd.extend(["-m", config.model])

    try:
        result = subprocess.run(
            cmd,
            input=prompt,                     # Pass prompt via stdin
            capture_output=True,
            text=True,
            errors='replace',                 # prevent UnicodeDecodeError on bad codex output
            timeout=effective_timeout,
            cwd=cwd or os.getcwd(),
            env=_subprocess_env(),
        )
    except subprocess.TimeoutExpired:
        return None, f"codex timed out ({effective_timeout}s). Try a shorter plan or check codex CLI health."
    except FileNotFoundError:
        reset_codex_cache()
        return None, "codex not found. Install: npm install -g @openai/codex"
    except (OSError, UnicodeDecodeError) as e:
        return None, f"failed to run codex: {e}"

    if config.verbose:
        print(f"[planman] codex exit code: {result.returncode}", file=sys.stderr)
        if result.stderr:
            verbose_limit = 4000 if result.returncode != 0 else 2000
            print(f"[planman] codex stderr (last {verbose_limit}): {result.stderr[-verbose_limit:]}", file=sys.stderr)

    if result.returncode != 0:
        error_detail = _extract_codex_error(result.stderr)
        return None, f"codex exec failed (exit {result.returncode}): {error_detail}"

    return parse_codex_output(result.stdout)


def evaluate_plan_claude(plan_text, config, previous_feedback=None, round_number=1, cwd=None):
    """Evaluate a plan via Claude Code print mode with structured output."""
    claude_bin = getattr(config, "claude_bin", None) or _CLAUDE_BIN
    if not check_claude_installed(claude_bin):
        return None, "claude CLI not found. Install and authenticate Claude Code."

    prompt = build_prompt(
        plan_text, config.rubric, previous_feedback, round_number,
        context=config.context,
        source_verify=getattr(config, "source_verify", True),
        provider="claude",
    )

    _MAX_PROMPT_SIZE = 2_000_000
    if len(prompt) > _MAX_PROMPT_SIZE:
        return None, f"prompt too large ({len(prompt) // 1024}KB > 2MB). Reduce max_rounds or plan size."

    schema_path, schema_error = _schema_path()
    if schema_error:
        return None, schema_error

    try:
        with open(schema_path, "r", encoding="utf-8") as f:
            schema_text = f.read()
    except OSError as e:
        return None, f"schema file unreadable: {e}"

    cmd = [
        claude_bin,
        "-p",
        "--output-format", "json",
        "--json-schema", schema_text,
        "--no-session-persistence",
        "--tools", "Read,Grep,Glob",
    ]
    if config.model:
        cmd.extend(["--model", config.model])

    effective_timeout = 570
    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=effective_timeout,
            cwd=cwd or os.getcwd(),
            env=_subprocess_env(),
        )
    except subprocess.TimeoutExpired:
        return None, f"claude timed out ({effective_timeout}s). Try a shorter plan or check Claude Code CLI health."
    except FileNotFoundError:
        reset_claude_cache()
        return None, "claude not found. Install and authenticate Claude Code."
    except (OSError, UnicodeDecodeError) as e:
        return None, f"failed to run claude: {e}"

    if config.verbose:
        print(f"[planman] claude exit code: {result.returncode}", file=sys.stderr)
        if result.stderr:
            verbose_limit = 4000 if result.returncode != 0 else 2000
            print(f"[planman] claude stderr (last {verbose_limit}): {result.stderr[-verbose_limit:]}", file=sys.stderr)

    if result.returncode != 0:
        return None, f"claude -p failed (exit {result.returncode}): {_extract_codex_error(result.stderr)}"

    return parse_codex_output(result.stdout)


def parse_codex_output(stdout):
    """Parse structured JSON from codex exec stdout.

    Returns (result_dict, error_string).
    """
    if not stdout or not stdout.strip():
        return None, "codex returned empty output"

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        return None, f"codex returned malformed output. Set PLANMAN_VERBOSE=true for details."

    # Claude's JSON mode can wrap the final structured response.
    if isinstance(data, dict) and "result" in data and not any(k in data for k in ("score", "breakdown")):
        wrapped = data.get("result")
        if isinstance(wrapped, str):
            try:
                data = json.loads(wrapped)
            except json.JSONDecodeError:
                return None, "claude returned malformed structured result"
        elif isinstance(wrapped, dict):
            data = wrapped

    # Validate required fields
    if not isinstance(data, dict):
        return None, "codex output is not a JSON object"

    required = ("score", "breakdown", "weaknesses", "suggestions", "strengths")
    missing = [k for k in required if k not in data]
    if missing:
        return None, f"codex output missing fields: {', '.join(missing)}"

    score = data.get("score")
    if not isinstance(score, int) or score < 1 or score > 10:
        return None, f"invalid score: {score} (must be integer 1-10)"

    breakdown = data.get("breakdown", {})
    for key in ("completeness", "correctness", "sequencing", "risk_awareness", "clarity"):
        val = breakdown.get(key)
        if not isinstance(val, int) or val < 0 or val > 2:
            return None, f"invalid breakdown.{key}: {val} (must be integer 0-2)"

    # Validate score equals breakdown sum
    expected_sum = sum(breakdown.get(k, 0) for k in
        ("completeness", "correctness", "sequencing", "risk_awareness", "clarity"))
    if score != expected_sum:
        return None, f"score mismatch: score={score} but breakdown sum={expected_sum}"

    # Validate array contents
    if not data.get("strengths"):
        return None, "no strengths listed"
    if score < 10 and not data.get("weaknesses"):
        return None, "score < 10 but no weaknesses listed"

    return data, None
