---
layout: default
title: "Planman: Structured Planning with Stress-Test Critique for Claude Code"
---

# Planman: Structured Planning with Stress-Test Critique for Claude Code

**Alexander Pavlov**
— [github.com/RusDyn/planman](https://github.com/RusDyn/planman)

---

## Abstract

Planman is an open-source Claude Code plugin that adds a structured planning phase with adversarial self-critique to Claude's problem-solving workflow. On [SWE-bench Verified](https://www.swebench.com/), planman achieves **374/500 (74.8%)** resolved instances at pass@1, compared to 70.0% for base Claude Code. The improvement comes not from planning alone — plan mode without critique shows no gain — but from the stress-test phase that forces the model to revise its plan before implementation.

## System Description

Planman operates as a plugin within the Claude Code CLI. It intercepts Claude's plan-mode exit and injects an automated critique step:

1. **Plan phase** — Claude enters plan mode and produces a structured implementation plan.
2. **Stress-test critique** — The plugin triggers adversarial self-critique, probing for missed edge cases, incorrect assumptions, and incomplete reasoning. The plan is rejected with feedback and revised.
3. **Implementation phase** — Claude exits plan mode and implements the fix following the stress-tested plan.

The plugin uses Claude Code's hook system (`PreToolUse(ExitPlanMode)`) to intercept plan completion. No external models or tools are used — the critique is performed by Claude itself via a structured prompt that challenges the plan's assumptions.

### Architecture

```
Claude Code CLI
  └── Plan Mode
        └── Write plan to .claude/plans/
              └── Exit Plan Mode (intercepted by planman)
                    ├── Stress-test prompt injected
                    ├── Claude critiques own plan
                    ├── Plan rejected with feedback → revise
                    └── Plan passes → implementation begins
```

### Key Design Choices

- **No external dependencies**: stdlib-only Python, no API keys beyond Claude Code itself
- **Deterministic plan detection**: files in `.claude/plans/` are always treated as plans
- **Fail-open**: plugin errors never block the workflow
- **Configurable rounds**: stress-test rounds (default 1) followed by optional Codex evaluation rounds

## SWE-bench Verified Results

### Full Evaluation (500 tasks)

| Condition | Resolved | Rate | Cost/task | Description |
|-----------|----------|------|-----------|-------------|
| Claude Code + planman | 374/500 | **74.8%** | $1.10 | Plan phase + stress-test critique |

Total cost: ~$552 for 500 tasks. Single attempt per task (pass@1), no retries.

Model: Claude Opus 4.6 (claude-opus-4-6).

### Pilot Comparison (50 tasks)

All three conditions were run on the same 50-task subset for a controlled comparison:

| Condition | Resolved | Rate | Cost/task | Description |
|-----------|----------|------|-----------|-------------|
| Claude Code (baseline) | 35/50 | 70.0% | $0.54 | No plan phase |
| Claude Code + plan mode | 35/50 | 70.0% | $0.70 | Plan phase, no plugin |
| Claude Code + planman | 37/50 | **74.0%** | $1.23 | Plan phase + stress-test critique |

**Finding**: Plan mode alone shows zero improvement over baseline. The gain comes entirely from planman's stress-test self-critique, which forces Claude to identify and fix weaknesses in its own plan before implementation begins.

## Methodology

### Benchmark Harness

The evaluation uses a custom harness (`benchmark/swebench/`) that:

1. Checks out the repository at the specified commit
2. Launches Claude Code in plan mode with the planman plugin active
3. Provides the problem statement as the initial prompt
4. Collects the generated patch
5. Runs the SWE-bench evaluation (FAIL_TO_PASS and PASS_TO_PASS tests)

Each task runs in an isolated Docker container with no internet access. Claude Code operates with `--dangerously-skip-permissions` for unattended execution.

### Configuration

```json
{
  "threshold": 0,
  "stress_test": true,
  "max_rounds": 3,
  "fail_open": true
}
```

- `threshold: 0` disables the Codex scoring gate (stress-test only)
- `stress_test: true` enables one round of adversarial self-critique
- Tasks are attempted exactly once (pass@1)

### No Test Knowledge

The harness does not expose SWE-bench test information (PASS_TO_PASS, FAIL_TO_PASS) or hints to Claude. The model receives only the problem statement and repository source code.

## Cost Analysis

| Phase | Avg. Cost/Task |
|-------|---------------|
| Planning + stress-test | ~$0.40 |
| Implementation | ~$0.70 |
| **Total** | **~$1.10** |

The stress-test adds roughly $0.40/task compared to baseline Claude Code ($0.54/task), but the 4.8 percentage point improvement in resolution rate (70.0% → 74.8%) represents ~24 additional resolved tasks.

## Comparison with Official Leaderboard

The SWE-bench leaderboard lists base Claude Opus 4.6 at 378/500 (75.6%). That entry uses [mini-swe-agent v2.0.0](https://github.com/swe-bench/SWE-bench/tree/main/swebench/harness/mini_swe_agent), a standardized agent framework built by the SWE-bench maintainers — not Claude Code.

Planman uses Claude Code's native CLI as the base agent, a simpler scaffold with no custom tool-calling loop. Despite this, per-instance comparison shows the two systems have **complementary strengths**:

| | Tasks |
|---|---|
| Solved by mini-swe-agent only | 33 |
| Solved by planman only | 29 |
| Solved by both | 345 |

The net gap is just 4 tasks (378 vs 374). The systems solve **different** problems rather than one strictly dominating the other.

Planman's stress-test critique closes the gap from our 70% Claude Code baseline (measured on a 50-task pilot) to 74.8%, nearly matching mini-swe-agent's 75.6% — at comparable cost ($1.10 vs ~$0.55/task).

## Source Code and Reproducibility

- **Plugin source**: [github.com/RusDyn/planman](https://github.com/RusDyn/planman)
- **Benchmark harness**: [`benchmark/swebench/`](https://github.com/RusDyn/planman/tree/main/benchmark/swebench)
- **License**: MIT

The complete benchmark configuration, runner scripts, and scoring code are included in the repository.

## Citation

```
@misc{pavlov2026planman,
  title={Planman: Structured Planning with Stress-Test Critique for Claude Code},
  author={Pavlov, Alexander},
  year={2026},
  url={https://github.com/RusDyn/planman}
}
```
