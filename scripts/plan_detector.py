"""Weighted heuristic plan detection — pure local, no external calls.

Signals and weights:
  permission_mode == "plan"    +5
  Plan header patterns         +3
  Numbered steps (>= 3)        +3
  Plan preamble phrases        +2
  Section headers (>= 3)       +2
  Action-verb bullets (>= 3)   +2
  File path references (>= 3)  +1

Threshold to trigger evaluation: >= 6
"""

import json
import os
import re
import sys

# --- Compiled patterns ---

_PLAN_HEADERS = re.compile(
    r"^#{1,3}\s+"
    r"(?:Implementation\s+)?Plan"
    r"|^#{1,3}\s+Approach"
    r"|^#{1,3}\s+Strategy"
    r"|^#{1,3}\s+Proposed\s+(?:Solution|Changes)"
    r"|^#{1,3}\s+Steps"
    r"|^#{1,3}\s+Action\s+Items"
    r"|^#{1,3}\s+Implementation\s+Steps"
    r"|^#{1,3}\s+Execution\s+Plan"
    r"|^#{1,3}\s+Migration\s+Plan"
    r"|^#{1,3}\s+Rollout\s+Plan",
    re.MULTILINE | re.IGNORECASE,
)

_NUMBERED_STEPS = re.compile(
    r"^\s*(?:\d+[.)]\s|Step\s+\d+[:.]\s)",
    re.MULTILINE | re.IGNORECASE,
)

_PREAMBLE_PHRASES = re.compile(
    r"(?:Here(?:'s| is) (?:my |the )?plan)"
    r"|(?:I(?:'ll| will) (?:proceed|start) (?:by|with))"
    r"|(?:The approach (?:is|will be))"
    r"|(?:Let me outline)"
    r"|(?:Here(?:'s| is) (?:my |the )?approach)"
    r"|(?:I propose (?:the following|to))"
    r"|(?:My plan is to)",
    re.IGNORECASE,
)

_SECTION_HEADERS = re.compile(r"^#{2,3}\s+\S", re.MULTILINE)

_ACTION_VERBS = re.compile(
    r"^\s*[-*]\s+(?:Create|Add|Implement|Update|Modify|Remove|Delete|Refactor|"
    r"Extract|Move|Rename|Configure|Set up|Install|Deploy|Test|Write|Build|"
    r"Fix|Migrate|Replace|Extend|Integrate)\b",
    re.MULTILINE | re.IGNORECASE,
)

_FILE_PATHS = re.compile(
    r"(?:`[a-zA-Z0-9_./-]+\.[a-zA-Z]{1,5}`)"  # backtick-wrapped
    r"|(?:\b[a-zA-Z0-9_.-]+/[a-zA-Z0-9_./-]+\.[a-zA-Z]{1,5}\b)",  # bare path
)

DETECTION_THRESHOLD = 6


def compute_plan_score(text, permission_mode=None):
    """Compute weighted plan detection score.

    Returns (score, signals) where signals is a dict of signal_name → points.
    """
    signals = {}

    # Signal 1: permission_mode
    if permission_mode == "plan":
        signals["permission_mode"] = 5

    # Signal 2: plan headers
    if _PLAN_HEADERS.search(text):
        signals["plan_header"] = 3

    # Signal 3: numbered steps
    step_count = len(_NUMBERED_STEPS.findall(text))
    if step_count >= 3:
        signals["numbered_steps"] = 3

    # Signal 4: preamble phrases
    if _PREAMBLE_PHRASES.search(text):
        signals["preamble_phrase"] = 2

    # Signal 5: section headers
    header_count = len(_SECTION_HEADERS.findall(text))
    if header_count >= 3:
        signals["section_headers"] = 2

    # Signal 6: action-verb bullets
    verb_count = len(_ACTION_VERBS.findall(text))
    if verb_count >= 3:
        signals["action_verbs"] = 2

    # Signal 7: file path references
    path_count = len(_FILE_PATHS.findall(text))
    if path_count >= 3:
        signals["file_paths"] = 1

    total = sum(signals.values())
    return total, signals


def is_plan(text, permission_mode=None):
    """Return True if text looks like a plan (score >= threshold)."""
    score, _ = compute_plan_score(text, permission_mode)
    return score >= DETECTION_THRESHOLD


def extract_last_assistant_text(transcript_path):
    """Read JSONL transcript and return the last assistant message text.

    Claude Code transcripts are JSONL with objects containing 'role' and
    'content' fields. Content can be a string or list of content blocks.
    """
    last_text = None
    try:
        file_size = os.path.getsize(transcript_path)
        if file_size > 50 * 1024 * 1024:  # 50MB
            return ""
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if entry.get("role") != "assistant":
                    continue

                content = entry.get("content", "")
                if isinstance(content, str):
                    last_text = content
                elif isinstance(content, list):
                    # Extract text from content blocks
                    parts = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            parts.append(block.get("text", ""))
                        elif isinstance(block, str):
                            parts.append(block)
                    if parts:
                        last_text = "\n".join(parts)
    except (OSError, IOError):
        pass

    return last_text or ""


if __name__ == "__main__":
    # Quick CLI test: pipe text to stdin
    text = sys.stdin.read()
    score, signals = compute_plan_score(text)
    print(f"Score: {score} (threshold: {DETECTION_THRESHOLD})")
    print(f"Is plan: {score >= DETECTION_THRESHOLD}")
    for signal, points in sorted(signals.items(), key=lambda x: -x[1]):
        print(f"  {signal}: +{points}")
