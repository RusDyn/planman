"""Planman PreToolUse hook — evaluates plan when Claude calls ExitPlanMode.

This is the PRIMARY plan evaluation path. It fires once when Claude is
done writing the plan and ready to present it to the user. The plan file
path is discovered via the session marker left by PostToolUse(Write).

PreToolUse input (stdin JSON):
  tool_name: "ExitPlanMode"
  tool_input: {...}
  session_id: str
  cwd: str (project directory)
  ...

PreToolUse output (stdout JSON):
  {"decision":"block","reason":"..."} → Claude revises and tries again
  {} or no output → ExitPlanMode proceeds normally
"""

import glob
import json
import os
import sys
import time

# Add scripts directory to path for sibling imports
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from config import load_config
from evaluator import check_codex_installed
from hook_utils import MARKER_TEMPLATE, log, run_evaluation, safe_session_id
from path_utils import normalize_path


def _output_block(reason, system_message=None):
    """Output a block decision and exit."""
    result = {"decision": "block"}
    if reason:
        result["reason"] = reason
    if system_message:
        result["systemMessage"] = system_message
    json.dump(result, sys.stdout, ensure_ascii=True)
    sys.exit(0)


def _output_allow(system_message=None):
    """Output an allow decision (empty or with system message) and exit."""
    if system_message:
        json.dump({"systemMessage": system_message}, sys.stdout, ensure_ascii=True)
    sys.exit(0)


def _is_plan_filename(basename):
    """Return True if basename looks like an actual plan file (not metadata)."""
    lower = basename.lower()
    if lower.startswith("."):
        return False
    skip_prefixes = ("readme", "template", "sample", "example", "backup")
    for prefix in skip_prefixes:
        if lower.startswith(prefix):
            return False
    return True


def _read_marker_metadata(session_id):
    """Read marker file and return (normalized_path_or_None, timestamp_float).

    Returns (None, 0) for: missing file, corrupt JSON, missing keys,
    non-numeric timestamp, future timestamp (clamped to 0).
    """
    safe_id = safe_session_id(session_id)
    marker_path = MARKER_TEMPLATE.format(session_id=safe_id)
    try:
        with open(marker_path, "r", encoding="utf-8") as f:
            marker = json.load(f)
    except (OSError, json.JSONDecodeError):
        return (None, 0)

    if not isinstance(marker, dict):
        return (None, 0)

    plan_path = marker.get("plan_file_path")
    if not plan_path or not isinstance(plan_path, str):
        return (None, 0)

    ts = marker.get("timestamp")
    if ts is None:
        return (None, 0)
    try:
        ts = float(ts)
    except (ValueError, TypeError):
        return (None, 0)

    # Future timestamp → clamp to 0 (marker still trusted if file exists)
    if ts > time.time():
        ts = 0

    return (normalize_path(plan_path), ts)


def _read_plan_text(path):
    """Read plan file, return (text, skip_reason).

    text is None when the file is empty/oversized/unreadable.
    skip_reason is set only when the file was found but explicitly rejected.
    """
    _MAX_PLAN_SIZE = 1_000_000  # 1 MB
    try:
        size = os.path.getsize(path)
        if size > _MAX_PLAN_SIZE:
            return None, f"Plan file too large (>{_MAX_PLAN_SIZE // 1_000_000} MB): {path}"
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        return (text, None) if text.strip() else (None, None)
    except OSError:
        return None, None


def _scan_plan_dirs(cwd, project_local_only=False):
    """Scan .claude/plans/ for most recently modified .md plan file.

    When project_local_only=True, only scans {cwd}/.claude/plans/.
    Otherwise also scans ~/.claude/plans/ (if different from project-local).
    Returns the path of the best candidate or None.
    """
    home_plans = os.path.expanduser("~/.claude/plans")
    cwd_plans = os.path.join(cwd, ".claude", "plans") if cwd else None

    scan_dirs = []
    if cwd_plans and os.path.isdir(cwd_plans):
        scan_dirs.append(cwd_plans)

    if not project_local_only:
        if os.path.isdir(home_plans) and (
            not cwd_plans
            or os.path.realpath(home_plans) != os.path.realpath(cwd_plans)
        ):
            scan_dirs.append(home_plans)

    md_files = []
    for plans_dir in scan_dirs:
        for f in glob.glob(os.path.join(plans_dir, "*.md")):
            if _is_plan_filename(os.path.basename(f)):
                md_files.append(f)

    if not md_files:
        return None

    return max(md_files, key=os.path.getmtime)


def _find_plan_file(session_id, cwd, config):
    """Find the plan file path via session marker or fallback scan.

    Returns (plan_file_path, plan_text, skip_reason) where skip_reason
    is a human-readable message when the plan was found but rejected
    (e.g. oversized), or None on success / when no plan exists at all.
    """
    _MARKER_TTL = 7200  # 2 hours
    _STALENESS_TOLERANCE = 2  # seconds — compensates for coarse-mtime filesystems

    # ── Gate: debug marker-only mode (internal escape hatch) ──
    if os.environ.get("_PLANMAN_DEBUG_MARKER_ONLY"):
        marker_path, _ = _read_marker_metadata(session_id)
        if marker_path and os.path.isfile(marker_path):
            text, skip = _read_plan_text(marker_path)
            if text:
                return (marker_path, text, None)
            return (None, None, skip)
        return (None, None, None)

    # ── Step 1: Try marker (authoritative when fresh) ──
    marker_plan_path, marker_ts = _read_marker_metadata(session_id)
    now = time.time()
    expired = marker_ts > 0 and (now - marker_ts) > (_MARKER_TTL + _STALENESS_TOLERANCE)

    if marker_plan_path and os.path.isfile(marker_plan_path) and not expired:
        # Marker is fresh + file exists → authoritative, return immediately
        text, skip = _read_plan_text(marker_plan_path)
        if text:
            log(
                f"plan detection: source=marker, path={marker_plan_path}, "
                f"session={safe_session_id(session_id)}",
                config, cwd,
            )
            return (marker_plan_path, text, None)
        if skip:
            return (None, None, skip)

    # ── Step 2: Scan fallback (marker missing/expired/file deleted) ──
    # Scope: project-local {cwd}/.claude/plans/ FIRST.
    # Only broaden to ~/.claude/plans/ if project-local yields nothing.
    scan_path = _scan_plan_dirs(cwd, project_local_only=True)
    if not scan_path:
        scan_path = _scan_plan_dirs(cwd, project_local_only=False)

    reason = (
        "marker_expired" if expired
        else "marker_file_deleted" if marker_plan_path
        else "no_marker"
    )
    if scan_path:
        text, skip = _read_plan_text(scan_path)
        if text:
            log(
                f"plan detection: source=scan_fallback({reason}), "
                f"path={scan_path}, session={safe_session_id(session_id)}",
                config, cwd,
            )
            return (scan_path, text, None)
        if skip:
            return (None, None, skip)

    return (None, None, None)


def _main():
    # Read hook input from stdin
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""

    hook_input = {}
    if raw.strip():
        try:
            hook_input = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"[planman] warning: malformed hook input: {e}", file=sys.stderr)

    # Extract context early so log() can write to project-local file
    cwd = hook_input.get("cwd")

    # Load config (pass cwd so file config resolves from project root, not hook cwd)
    config = load_config(cwd=cwd)

    if not config.enabled:
        log("disabled via config", config, cwd)
        _output_allow()
        return

    if not check_codex_installed():
        log("codex CLI not installed — passing through", config, cwd)
        _output_allow()
        return

    session_id = hook_input.get("session_id", "default")

    # Find the plan file
    plan_path, plan_text, skip_reason = _find_plan_file(session_id, cwd, config)

    if not plan_text:
        if skip_reason:
            log(f"plan skipped: {skip_reason}", config, cwd)
            _output_allow(system_message=f"Planman: {skip_reason}")
        else:
            log("no plan file found — blocking ExitPlanMode", config, cwd)
            _output_block(
                reason="No plan file found. Write your plan to the plan file before calling ExitPlanMode.",
            )
        return

    log(f"evaluating plan from {plan_path} (session={session_id})", config, cwd)

    # Run evaluation
    result = run_evaluation(
        plan_text, session_id, config, cwd=cwd, plan_path=plan_path
    )

    action = result["action"]
    reason = result.get("reason")
    sys_msg = result.get("system_message")

    if action == "block":
        _output_block(reason=reason, system_message=sys_msg)
        return
    elif action == "pass":
        _output_allow(system_message=sys_msg)
        return
    else:
        # "skip" — not detected as a plan (unlikely for .claude/plans/ files)
        _output_allow(system_message=sys_msg)
        return


def main():
    try:
        _main()
    except Exception as e:
        print(f"[planman] FATAL: {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
