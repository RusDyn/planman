---
description: Create .claude/planman.jsonc with commented defaults
---

# Planman Init

Create a starter `.claude/planman.jsonc` with all available settings and descriptions.

## Instructions

Run this command:

```bash
python3 -c "
import sys, os
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')

jsonc_path = os.path.join('.claude', 'planman.jsonc')
json_path = os.path.join('.claude', 'planman.json')
if os.path.exists(jsonc_path):
    print(f'Already exists: {jsonc_path}')
    print('Delete it first if you want to regenerate.')
    sys.exit(0)
if os.path.exists(json_path):
    print(f'Found existing {json_path} — rename or delete it first.')
    print('planman now uses .jsonc (supports // comments).')
    sys.exit(0)

content = '''// Planman configuration
// Docs: /planman:help | All settings are optional — defaults shown below
{
  // Minimum score (0-10) to pass
  \"threshold\": 7,
  // Evaluation rounds before you decide (1-100)
  \"max_rounds\": 3,
  // Override Codex model (empty = codex default)
  \"model\": \"\",
  // Pass through if Codex fails
  \"fail_open\": true,
  // Master switch
  \"enabled\": true,
  // Custom evaluation rubric (empty = built-in)
  \"custom_rubric\": \"\",
  // Path to codex binary
  \"codex_path\": \"codex\",
  // Debug output to stderr + log file
  \"verbose\": false,
  // Seconds for codex subprocess (1-600)
  \"timeout\": 120,
  // Codex verifies plan against actual source files
  \"source_verify\": true,
  // Auto-reject first plan for deep revision (skips Codex on round 1)
  \"stress_test\": false,
  // Custom first-round rejection message (empty = built-in)
  \"stress_test_prompt\": \"\",
  // Project context for the evaluator (e.g. \"Python CLI tool, no web framework\")
  \"context\": \"\"
}
'''

os.makedirs('.claude', exist_ok=True)
with open(jsonc_path, 'w') as f:
    f.write(content)
print(f'Created {jsonc_path}')
print('Edit the values you want to change. Run /planman:status to verify.')
"
```

Report the result to the user. If the file was created, mention they can run `/planman` to verify the effective configuration.
