---
description: Show planman status and effective configuration
---

# Planman Status

Check the current planman configuration and evaluator CLI status.

## Instructions

Run these commands and report the results:

1. Check if evaluator CLIs are installed:
   `which codex && codex --version || echo "codex not installed"`
   `which claude && claude --version || echo "claude not installed"`
2. Show effective configuration by running:
   ```bash
   python3 -c "
   import json, sys, os
   root = os.environ.get('CLAUDE_PLUGIN_ROOT') or os.environ.get('CODEX_PLUGIN_ROOT') or os.getcwd()
   sys.path.insert(0, os.path.join(root, 'scripts'))
   plugin_json = os.path.join(root, '.claude-plugin', 'plugin.json')
   if not os.path.isfile(plugin_json):
       plugin_json = os.path.join(root, '.codex-plugin', 'plugin.json')
   with open(plugin_json) as f:
       ver = json.load(f)['version']
   from config import load_config
   from evaluator import detect_host, resolve_evaluator
   c = load_config(cwd=os.getcwd())
   host = detect_host({})
   evaluator = resolve_evaluator(c, host=host)
   print(f'version:    {ver}')
   print(f'enabled:    {c.enabled}')
   print(f'host:       {host} (auto-detected)')
   print(f'evaluator:  {c.evaluator} -> {evaluator}')
   print(f'threshold:  {c.threshold}/10')
   print(f'max_rounds: {c.max_rounds}')
   print(f'min_rounds: {c.min_rounds}')
   print(f'model:      {c.model or \"(evaluator default)\"}')
   print(f'codex_bin:  {c.codex_bin}')
   print(f'claude_bin: {c.claude_bin}')
   print(f'fail_open:  {c.fail_open}')
   print(f'verbose:    {c.verbose}')
   print(f'rubric:     {\"custom\" if c.rubric != __import__(\"config\").DEFAULT_RUBRIC else \"built-in\"}')
   print(f'source_verify: {c.source_verify}')
   print(f'stress_test: {c.stress_test}')
   print(f'context:     {c.context or \"(none)\"}')
   print(f'auto_answer: {c.auto_answer}')
   "
   ```
3. Check for active sessions:
   `python3 -c "import os, subprocess; root = os.environ.get('CLAUDE_PLUGIN_ROOT') or os.environ.get('CODEX_PLUGIN_ROOT') or os.getcwd(); subprocess.run(['python3', os.path.join(root, 'scripts', 'clear_state.py'), 'list'])"`

Format the output as a clean status report.
