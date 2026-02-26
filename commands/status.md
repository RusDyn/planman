---
description: Show planman status and effective configuration
---

# Planman Status

Check the current planman configuration and codex CLI status.

## Instructions

Run these commands and report the results:

1. Check if codex is installed: `which codex && codex --version || echo "codex not installed"`
2. Show effective configuration by running:
   ```bash
   python3 -c "
   import sys, os; sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
   from config import load_config
   c = load_config(cwd=os.getcwd())
   print(f'enabled:    {c.enabled}')
   print(f'threshold:  {c.threshold}/10')
   print(f'max_rounds: {c.max_rounds}')
   print(f'model:      {c.model or \"(codex default)\"}')
   print(f'fail_open:  {c.fail_open}')
   print(f'codex_path: {c.codex_path}')
   print(f'verbose:    {c.verbose}')
   print(f'timeout:    {c.timeout}s')
   print(f'rubric:     {\"custom\" if c.rubric != __import__(\"config\").DEFAULT_RUBRIC else \"built-in\"}')
   print(f'stress_test: {c.stress_test}')
   "
   ```
3. Check for active sessions: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/clear_state.py list`

Format the output as a clean status report.
