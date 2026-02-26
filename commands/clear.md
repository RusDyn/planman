---
description: Clear planman session state (reset evaluation rounds)
---
Clear all planman session state files by running:
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/clear_state.py`

## What Gets Cleared

- **Session state files** (`planman-*.json` in the system temp directory) — tracks round count, last score, previous feedback, and plan file path
- **Plan marker files** (`planman-plan-*.json`) — records which plan file was last written

## When to Use

- After manually editing a plan file and wanting a fresh evaluation
- When the round counter is stuck or stale
- When switching between projects and wanting to reset
- If planman seems to be using stale feedback from a previous evaluation
