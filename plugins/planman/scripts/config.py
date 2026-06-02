"""Configuration loader for planman.

Loads settings from two sources (env vars override file):
  1. .planman.jsonc or .claude/planman.jsonc (project-level, supports // comments)
  2. PLANMAN_* environment variables (highest priority)
"""

import json
import os
import re

DEFAULT_RUBRIC = """\
Score the plan on these 5 criteria (0-2 each, 10 max):

1. **Completeness** (0-2): Does the plan address all stated requirements with no critical gaps?
2. **Correctness** (0-2): Is the technical approach sound? Any flaws or misunderstandings?
3. **Sequencing** (0-2): Are steps ordered logically? Are dependencies respected?
4. **Risk Awareness** (0-2): Does the plan address risks proportionate to the task's scope? Simple tasks need minimal risk coverage.
5. **Clarity** (0-2): Are steps specific and actionable? Could a developer follow them without ambiguity?

The overall score MUST equal the sum of the 5 breakdown scores.
A score of 7+ means the plan is ready to execute. Prefer simple, focused plans — do NOT penalize for omitting rollback plans, exhaustive risk analysis, or verification strategies unless the task specifically requires them.\
"""

DEFAULT_STRESS_TEST_PROMPT = """\
Stress-test this plan. Spawn 2-3 research agents to examine it in parallel from \
different angles (correctness, edge cases, feasibility, current best practices). \
Have agents use web search to validate approaches and identify known pitfalls. \
Cross-reference findings — web research may surface false positives or outdated \
practices, so keep only what's well-supported. Then make targeted fixes to the \
1-2 most critical weaknesses. Focus on value; don't add complexity for its own sake.\
"""

DEFAULTS = {
    "threshold": 7,
    "max_rounds": 3,
    "min_rounds": 0,
    "model": "",
    "evaluator": "auto",
    "codex_bin": "codex",
    "claude_bin": "claude",
    "fail_open": True,
    "enabled": True,
    "custom_rubric": "",
    "verbose": False,
    "stress_test": 0,
    "context": "",
    "source_verify": True,
    "auto_answer": False,
    "stress_test_prompt": "",
    "plan_dirs": [],
    "exec_patterns": [],
    "skill_patterns": [],
}

_BOOL_TRUTHY = {"true", "1", "yes", "on"}
_BOOL_FALSY = {"false", "0", "no", "off"}


def _coerce_bool(value, key="fail_open"):
    """Coerce a string to bool, falling back to default for the given key."""
    if isinstance(value, bool):
        return value
    s = str(value).lower().strip()
    if s in _BOOL_TRUTHY:
        return True
    if s in _BOOL_FALSY:
        return False
    return DEFAULTS.get(key, True)


def _coerce_int(value, key):
    """Coerce a string to int, falling back to default."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return DEFAULTS.get(key, 0)


def _coerce_stress_test(value, key="stress_test"):
    """Coerce stress_test: False/false→0, True/true→1, number→int."""
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return max(0, value)
    s = str(value).lower().strip()
    if s in _BOOL_TRUTHY:
        return 1
    if s in _BOOL_FALSY:
        return 0
    try:
        return max(0, int(s))
    except (ValueError, TypeError):
        return DEFAULTS.get(key, 0)


def _coerce_list(value, key):
    """Coerce to list[str]. Accepts JSON arrays, comma-separated strings."""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        # Try JSON array first (handles commas in regex patterns)
        if s.startswith("["):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            except (json.JSONDecodeError, ValueError):
                pass
        # Fall back to comma-separated
        return [item.strip() for item in s.split(",") if item.strip()]
    return DEFAULTS.get(key, [])


class Config:
    """Planman configuration."""

    __slots__ = (
        "threshold",
        "max_rounds",
        "min_rounds",
        "model",
        "evaluator",
        "codex_bin",
        "claude_bin",
        "fail_open",
        "enabled",
        "rubric",
        "verbose",
        "stress_test",
        "context",
        "source_verify",
        "auto_answer",
        "stress_test_prompt",
        "plan_dirs",
        "exec_patterns",
        "skill_patterns",
    )

    def __init__(self, **kwargs):
        self.threshold = kwargs.get("threshold", DEFAULTS["threshold"])
        self.max_rounds = kwargs.get("max_rounds", DEFAULTS["max_rounds"])
        self.min_rounds = kwargs.get("min_rounds", DEFAULTS["min_rounds"])
        self.model = kwargs.get("model", DEFAULTS["model"])
        self.evaluator = kwargs.get("evaluator", DEFAULTS["evaluator"])
        self.codex_bin = kwargs.get("codex_bin", DEFAULTS["codex_bin"])
        self.claude_bin = kwargs.get("claude_bin", DEFAULTS["claude_bin"])
        self.fail_open = kwargs.get("fail_open", DEFAULTS["fail_open"])
        self.enabled = kwargs.get("enabled", DEFAULTS["enabled"])
        self.rubric = kwargs.get("rubric", "") or DEFAULT_RUBRIC
        self.verbose = kwargs.get("verbose", DEFAULTS["verbose"])
        self.stress_test = kwargs.get("stress_test", DEFAULTS["stress_test"])
        self.context = kwargs.get("context", "")
        self.source_verify = kwargs.get("source_verify", DEFAULTS["source_verify"])
        self.auto_answer = kwargs.get("auto_answer", DEFAULTS["auto_answer"])
        self.stress_test_prompt = kwargs.get("stress_test_prompt", "") or DEFAULT_STRESS_TEST_PROMPT
        self.plan_dirs = kwargs.get("plan_dirs", DEFAULTS["plan_dirs"])
        self.exec_patterns = kwargs.get("exec_patterns", DEFAULTS["exec_patterns"])
        self.skill_patterns = kwargs.get("skill_patterns", DEFAULTS["skill_patterns"])


def _strip_jsonc_comments(text):
    """Strip // line comments from JSONC text, preserving strings."""
    return re.sub(
        r'("(?:[^"\\]|\\.)*")|//[^\n]*',
        lambda m: m.group(1) if m.group(1) else "",
        text,
    )


def _load_file_config(cwd=None):
    """Load project planman config if it exists."""
    base = cwd or "."
    candidate_paths = (
        os.path.join(base, ".planman.jsonc"),
        os.path.join(base, ".planman.json"),
        os.path.join(base, ".claude", "planman.jsonc"),
        os.path.join(base, ".claude", "planman.json"),
    )
    path = next((p for p in candidate_paths if os.path.isfile(p)), None)
    if path is None:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        data = json.loads(_strip_jsonc_comments(raw))
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError, ValueError):
        pass
    return {}


def _load_env_overrides():
    """Load PLANMAN_* environment variable overrides."""
    overrides = {}
    env_map = {
        "PLANMAN_THRESHOLD": ("threshold", _coerce_int),
        "PLANMAN_MAX_ROUNDS": ("max_rounds", _coerce_int),
        "PLANMAN_MIN_ROUNDS": ("min_rounds", _coerce_int),
        "PLANMAN_MODEL": ("model", str),
        "PLANMAN_EVALUATOR": ("evaluator", str),
        "PLANMAN_CODEX_BIN": ("codex_bin", str),
        "PLANMAN_CLAUDE_BIN": ("claude_bin", str),
        "PLANMAN_FAIL_OPEN": ("fail_open", _coerce_bool),
        "PLANMAN_ENABLED": ("enabled", _coerce_bool),
        "PLANMAN_RUBRIC": ("custom_rubric", str),
        "PLANMAN_VERBOSE": ("verbose", _coerce_bool),
        "PLANMAN_STRESS_TEST": ("stress_test", _coerce_stress_test),
        "PLANMAN_CONTEXT": ("context", str),
        "PLANMAN_SOURCE_VERIFY": ("source_verify", _coerce_bool),
        "PLANMAN_AUTO_ANSWER": ("auto_answer", _coerce_bool),
        "PLANMAN_PLAN_DIRS": ("plan_dirs", _coerce_list),
        "PLANMAN_EXEC_PATTERNS": ("exec_patterns", _coerce_list),
        "PLANMAN_SKILL_PATTERNS": ("skill_patterns", _coerce_list),
    }
    for env_var, (key, coerce) in env_map.items():
        val = os.environ.get(env_var)
        if val is not None:
            if coerce in (_coerce_int, _coerce_bool, _coerce_stress_test, _coerce_list):
                overrides[key] = coerce(val, key)
            else:
                overrides[key] = coerce(val)
    return overrides


def load_config(cwd=None):
    """Load config: defaults < file < env vars."""
    merged = dict(DEFAULTS)

    # Layer 1: file config
    file_cfg = _load_file_config(cwd=cwd)
    for key in DEFAULTS:
        if key in file_cfg:
            merged[key] = file_cfg[key]

    # Remap custom_rubric → rubric
    if "custom_rubric" in file_cfg:
        merged["custom_rubric"] = file_cfg["custom_rubric"]

    # Layer 2: env overrides (highest priority)
    env_cfg = _load_env_overrides()
    merged.update(env_cfg)

    # Clamp numeric ranges (safe coercion — invalid strings fall back to defaults)
    merged["threshold"] = max(0, min(10, _coerce_int(merged["threshold"], "threshold")))
    merged["max_rounds"] = max(1, min(100, _coerce_int(merged["max_rounds"], "max_rounds")))
    merged["min_rounds"] = max(0, min(100, _coerce_int(merged["min_rounds"], "min_rounds")))

    # Coerce stress_test to int (False→0, True→1, number→int)
    merged["stress_test"] = _coerce_stress_test(merged["stress_test"], "stress_test")

    evaluator = str(merged.get("evaluator", "auto")).lower().strip()
    if evaluator not in ("auto", "codex", "claude"):
        evaluator = DEFAULTS["evaluator"]
    merged["evaluator"] = evaluator

    # Coerce list fields
    merged["plan_dirs"] = _coerce_list(merged.get("plan_dirs", []), "plan_dirs")
    merged["exec_patterns"] = _coerce_list(merged.get("exec_patterns", []), "exec_patterns")
    merged["skill_patterns"] = _coerce_list(merged.get("skill_patterns", []), "skill_patterns")

    # Build Config, mapping custom_rubric to rubric
    return Config(
        threshold=merged["threshold"],
        max_rounds=merged["max_rounds"],
        min_rounds=merged["min_rounds"],
        model=merged["model"],
        evaluator=merged["evaluator"],
        codex_bin=merged.get("codex_bin", "codex") or "codex",
        claude_bin=merged.get("claude_bin", "claude") or "claude",
        fail_open=merged["fail_open"],
        enabled=merged["enabled"],
        rubric=merged.get("custom_rubric", ""),
        verbose=merged["verbose"],
        stress_test=merged["stress_test"],
        context=merged.get("context", ""),
        source_verify=_coerce_bool(merged.get("source_verify", True), "source_verify"),
        auto_answer=_coerce_bool(merged.get("auto_answer", False), "auto_answer"),
        stress_test_prompt=merged.get("stress_test_prompt", ""),
        plan_dirs=merged["plan_dirs"],
        exec_patterns=merged["exec_patterns"],
        skill_patterns=merged["skill_patterns"],
    )
