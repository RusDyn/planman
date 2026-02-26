"""Configuration loader for planman.

Loads settings from two sources (env vars override file):
  1. .claude/planman.json (project-level)
  2. PLANMAN_* environment variables (highest priority)
"""

import json
import os

DEFAULT_RUBRIC = """\
Score the plan on these 5 criteria (0-2 each, 10 max):

1. **Completeness** (0-2): Does the plan address all stated requirements? Are there gaps?
2. **Correctness** (0-2): Is the technical approach sound? Any flaws or misunderstandings?
3. **Sequencing** (0-2): Are steps ordered logically? Are dependencies respected?
4. **Risk Awareness** (0-2): Does the plan identify edge cases, failure modes, or risks?
5. **Clarity** (0-2): Are steps specific and actionable? Could a developer follow them?

The overall score MUST equal the sum of the 5 breakdown scores.
Be strict — a score of 7+ means the plan is ready to execute as-is.\
"""

DEFAULTS = {
    "threshold": 7,
    "max_rounds": 3,
    "model": "",
    "fail_open": True,
    "enabled": True,
    "custom_rubric": "",
    "codex_path": "codex",
    "verbose": False,
    "timeout": 90,
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


def _validate_codex_path(path):
    """Reject paths containing '..' to prevent directory traversal."""
    if ".." in path:
        return DEFAULTS["codex_path"]
    return path


class Config:
    """Planman configuration."""

    __slots__ = (
        "threshold",
        "max_rounds",
        "model",
        "fail_open",
        "enabled",
        "rubric",
        "codex_path",
        "verbose",
        "timeout",
    )

    def __init__(self, **kwargs):
        self.threshold = kwargs.get("threshold", DEFAULTS["threshold"])
        self.max_rounds = kwargs.get("max_rounds", DEFAULTS["max_rounds"])
        self.model = kwargs.get("model", DEFAULTS["model"])
        self.fail_open = kwargs.get("fail_open", DEFAULTS["fail_open"])
        self.enabled = kwargs.get("enabled", DEFAULTS["enabled"])
        self.rubric = kwargs.get("rubric", "") or DEFAULT_RUBRIC
        self.codex_path = kwargs.get("codex_path", DEFAULTS["codex_path"])
        self.verbose = kwargs.get("verbose", DEFAULTS["verbose"])
        self.timeout = kwargs.get("timeout", DEFAULTS["timeout"])


def _load_file_config():
    """Load .claude/planman.json if it exists."""
    path = os.path.join(".claude", "planman.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _load_env_overrides():
    """Load PLANMAN_* environment variable overrides."""
    overrides = {}
    env_map = {
        "PLANMAN_THRESHOLD": ("threshold", _coerce_int),
        "PLANMAN_MAX_ROUNDS": ("max_rounds", _coerce_int),
        "PLANMAN_MODEL": ("model", str),
        "PLANMAN_FAIL_OPEN": ("fail_open", _coerce_bool),
        "PLANMAN_ENABLED": ("enabled", _coerce_bool),
        "PLANMAN_RUBRIC": ("custom_rubric", str),
        "PLANMAN_CODEX_PATH": ("codex_path", str),
        "PLANMAN_VERBOSE": ("verbose", _coerce_bool),
        "PLANMAN_TIMEOUT": ("timeout", _coerce_int),
    }
    for env_var, (key, coerce) in env_map.items():
        val = os.environ.get(env_var)
        if val is not None:
            if coerce in (_coerce_int, _coerce_bool):
                overrides[key] = coerce(val, key)
            else:
                overrides[key] = coerce(val)
    return overrides


def load_config():
    """Load config: defaults < file < env vars."""
    merged = dict(DEFAULTS)

    # Layer 1: file config
    file_cfg = _load_file_config()
    for key in DEFAULTS:
        if key in file_cfg:
            merged[key] = file_cfg[key]

    # Remap custom_rubric → rubric
    if "custom_rubric" in file_cfg:
        merged["custom_rubric"] = file_cfg["custom_rubric"]

    # Layer 2: env overrides (highest priority)
    env_cfg = _load_env_overrides()
    merged.update(env_cfg)

    # Clamp numeric ranges
    merged["threshold"] = max(0, min(10, int(merged["threshold"])))
    merged["max_rounds"] = max(1, min(100, int(merged["max_rounds"])))
    merged["timeout"] = max(1, min(600, int(merged["timeout"])))

    # Validate codex_path
    merged["codex_path"] = _validate_codex_path(merged["codex_path"])

    # Build Config, mapping custom_rubric to rubric
    return Config(
        threshold=merged["threshold"],
        max_rounds=merged["max_rounds"],
        model=merged["model"],
        fail_open=merged["fail_open"],
        enabled=merged["enabled"],
        rubric=merged.get("custom_rubric", ""),
        codex_path=merged["codex_path"],
        verbose=merged["verbose"],
        timeout=merged["timeout"],
    )
