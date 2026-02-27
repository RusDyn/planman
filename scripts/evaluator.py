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


def build_prompt(plan_text, rubric, previous_feedback=None, round_number=1,
                  context=None, source_verify=True):
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
        prompt += (
            "\n## Source Verification\n\n"
            "You have read-only access to the project filesystem (cwd = project root).\n"
            "When evaluating correctness and completeness:\n"
            "1. If the plan references specific files, read them with `cat <path>` or search with `rg`\n"
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


def evaluate_plan(plan_text, config, previous_feedback=None, round_number=1, cwd=None):
    """Evaluate a plan via codex exec with structured output.

    Returns (result_dict, error_string). On success error_string is None.
    On failure result_dict is None and error_string describes the problem.
    """
    if not check_codex_installed(config.codex_path):
        return None, "codex CLI not found. Install: npm install -g @openai/codex"

    prompt = build_prompt(
        plan_text, config.rubric, previous_feedback, round_number,
        context=config.context,
        source_verify=getattr(config, "source_verify", True),
    )

    _MAX_PROMPT_SIZE = 2_000_000  # 2MB hard cap
    if len(prompt) > _MAX_PROMPT_SIZE:
        return None, f"prompt too large ({len(prompt) // 1024}KB > 2MB). Reduce max_rounds or plan size."

    # Scale timeout for large prompts (>500KB get 1.5x, capped below hook timeout)
    effective_timeout = config.timeout
    if len(prompt) > 500_000:
        effective_timeout = min(int(config.timeout * 1.5), 110)

    schema_path = os.path.join(PLUGIN_ROOT, "schemas", "evaluation.json")

    if not os.path.isfile(schema_path):
        return None, f"schema file not found: {schema_path}. Check CLAUDE_PLUGIN_ROOT."

    cmd = [
        config.codex_path,
        "exec", "-",                          # Read prompt from stdin
        "--output-schema", schema_path,
        "--sandbox", "read-only",
        "--skip-git-repo-check",
        "--ephemeral",                        # Don't persist session files
    ]
    if config.model:
        cmd.extend(["-m", config.model])

    try:
        result = subprocess.run(
            cmd,
            input=prompt,                     # Pass prompt via stdin
            capture_output=True,
            text=True,
            timeout=effective_timeout,
            cwd=cwd or os.getcwd(),
        )
    except subprocess.TimeoutExpired:
        return None, f"codex timed out ({effective_timeout}s). Increase: PLANMAN_TIMEOUT={config.timeout + 30}"
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
