"""Codex CLI evaluation via subprocess.

Calls `codex exec` with `--output-schema` for structured JSON scoring.
No API keys required — uses ChatGPT subscription auth via the codex CLI.
"""

import json
import os
import shutil
import subprocess
import sys

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT", "") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)

_codex_available = None  # cached result


def check_codex_installed(codex_path="codex"):
    """Check if codex CLI is installed. Result is cached."""
    global _codex_available
    if _codex_available is not None:
        return _codex_available
    _codex_available = shutil.which(codex_path) is not None
    return _codex_available


def reset_codex_cache():
    """Reset the cached codex availability check (for testing)."""
    global _codex_available
    _codex_available = None


def build_prompt(plan_text, rubric, previous_feedback=None, round_number=1):
    """Build the evaluation prompt for codex exec."""
    prompt = (
        "You are a senior software architect reviewing an implementation plan.\n\n"
        "Evaluate the following implementation plan using the rubric.\n\n"
        f"{rubric}\n\n"
        "## Feedback Guidelines\n\n"
        "- Prioritize issues: list critical problems first, minor improvements last\n"
        "- Be specific: reference exact steps by number\n"
        "- Be actionable: say what to change, not just what's wrong\n\n"
        f"## Plan to Evaluate (Round {round_number})\n\n"
        f"{plan_text}\n"
    )
    if previous_feedback:
        prompt += (
            f"\n## Previous Feedback (Round {round_number - 1})\n\n"
            f"{previous_feedback}\n\n"
            "Assess: Which feedback items were addressed? Which were ignored? "
            "Focus new feedback on remaining and newly discovered issues.\n"
        )
    return prompt


def evaluate_plan(plan_text, config, previous_feedback=None, round_number=1, cwd=None):
    """Evaluate a plan via codex exec with structured output.

    Returns (result_dict, error_string). On success error_string is None.
    On failure result_dict is None and error_string describes the problem.
    """
    if not check_codex_installed(config.codex_path):
        return None, "codex CLI not found. Install: npm install -g @openai/codex"

    prompt = build_prompt(plan_text, config.rubric, previous_feedback, round_number)
    schema_path = os.path.join(PLUGIN_ROOT, "schemas", "evaluation.json")

    if not os.path.isfile(schema_path):
        return None, f"schema file not found: {schema_path}. Check CLAUDE_PLUGIN_ROOT."

    cmd = [
        config.codex_path,
        "exec",
        prompt,
        "--output-schema", schema_path,
        "--sandbox", "read-only",
        "--skip-git-repo-check",
    ]
    if config.model:
        cmd.extend(["-m", config.model])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=config.timeout,
            cwd=cwd or os.getcwd(),
        )
    except subprocess.TimeoutExpired:
        return None, f"codex timed out ({config.timeout}s). Increase: PLANMAN_TIMEOUT={config.timeout + 30}"
    except FileNotFoundError:
        reset_codex_cache()
        return None, f"codex not found at '{config.codex_path}'. Install: npm install -g @openai/codex, or set PLANMAN_CODEX_PATH"
    except OSError as e:
        return None, f"failed to run codex: {e}"

    if config.verbose:
        print(f"[planman] codex exit code: {result.returncode}", file=sys.stderr)
        if result.stderr:
            print(f"[planman] codex stderr: {result.stderr[:2000]}", file=sys.stderr)

    if result.returncode != 0:
        stderr_snippet = (result.stderr or "")[:1000]
        return None, f"codex exec failed (exit {result.returncode}): {stderr_snippet}"

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

    return data, None
