---
description: Create .planman.jsonc with commented defaults
---

# Planman Init

Create a starter `.planman.jsonc` with all available settings and descriptions.

## Instructions

Run this command:

```bash
python3 -c "
import sys, os
plugin_root = os.environ.get('CLAUDE_PLUGIN_ROOT') or os.environ.get('CODEX_PLUGIN_ROOT') or os.getcwd()
sys.path.insert(0, os.path.join(plugin_root, 'scripts'))

jsonc_path = '.planman.jsonc'
json_path = '.planman.json'
legacy_jsonc_path = os.path.join('.claude', 'planman.jsonc')
legacy_json_path = os.path.join('.claude', 'planman.json')
if os.path.exists(jsonc_path):
    print(f'Already exists: {jsonc_path}')
    print('Delete it first if you want to regenerate.')
    sys.exit(0)
if os.path.exists(json_path):
    print(f'Found existing {json_path} — rename or delete it first.')
    print('planman now uses .jsonc (supports // comments).')
    sys.exit(0)
if os.path.exists(legacy_jsonc_path) or os.path.exists(legacy_json_path):
    print('Found existing legacy .claude/planman config.')
    print('Keeping it. Delete or move it first if you want to regenerate .planman.jsonc.')
    sys.exit(0)

content = '''// Planman configuration
// Docs: /planman:help | All settings are optional — defaults shown below
{
  // Minimum score (0-10) to pass
  \"threshold\": 7,
  // Evaluation rounds before you decide (1-100)
  \"max_rounds\": 3,
  // Minimum rounds before a plan can pass (0 = no minimum)
  \"min_rounds\": 0,
  // Override evaluator model (empty = evaluator default)
  \"model\": \"\",
  // Evaluator provider (auto = Codex reviews Claude, Claude reviews Codex)
  \"evaluator\": \"auto\",
  // Codex CLI binary/path
  \"codex_bin\": \"codex\",
  // Claude Code CLI binary/path
  \"claude_bin\": \"claude\",
  // Pass through if the evaluator fails
  \"fail_open\": true,
  // Master switch
  \"enabled\": true,
  // Custom evaluation rubric (empty = built-in)
  \"custom_rubric\": \"\",
  // Debug output to stderr + log file
  \"verbose\": false,
  // Evaluator verifies plan against actual source files
  \"source_verify\": true,
  // Stress-test rounds before Codex evaluation (false=off, true=1, or number N)
  \"stress_test\": false,
  // Project context for the evaluator (e.g. \"Python CLI tool, no web framework\")
  \"context\": \"\",
  // Auto-answer obvious clarifying questions from project files (experimental)
  \"auto_answer\": false,
  // Additional plan directories to monitor (e.g., [\".omc/plans\"])
  \"plan_dirs\": [],
  // Bash command regex patterns that trigger plan evaluation before execution
  // (e.g., [\"omc\\\\s+(team|ralphthon)\"] for oh-my-claudecode team mode)
  \"exec_patterns\": [],
  // Skill name regex patterns that trigger plan evaluation
  // (e.g., [\"oh-my-claudecode:(ralph|autopilot)\"] for OMC skill invocations)
  \"skill_patterns\": []
}
'''

with open(jsonc_path, 'w') as f:
    f.write(content)
print(f'Created {jsonc_path}')
print('Edit the values you want to change. Run /planman:status to verify.')
"
```

Report the result to the user. If the file was created, mention they can run `/planman:status` to verify the effective configuration.
