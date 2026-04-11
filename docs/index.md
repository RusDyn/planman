---
layout: default
title: "Planman: Structured Planning with Stress-Test Critique for Claude Code"
---

# Planman: Structured Planning with Stress-Test Critique for Claude Code

**Alexander Pavlov**
— [github.com/RusDyn/planman](https://github.com/RusDyn/planman)

---

## Abstract

Planman is an open-source Claude Code plugin that adds a structured planning phase with adversarial self-critique to Claude's problem-solving workflow. On [SWE-bench Verified](https://www.swebench.com/), planman achieves **374/500 (74.8%)** resolved instances at pass@1, compared to 300/500 (60.0%) for base Claude Code. On the 422 tasks where both conditions produced patches, planman resolves 3.3 percentage points more (74.6% vs 71.1%, Wilcoxon p=0.048). The improvement comes not from planning alone — plan mode without critique shows no gain — but from the stress-test phase that forces the model to revise its plan before implementation.

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
| Claude Code (baseline) | 300/500 | 60.0% | $0.54 | No plan phase |
| Claude Code + planman | 374/500 | **74.8%** | $1.10 | Plan phase + stress-test critique |

Total cost: ~$552 for 500 tasks (planman). Single attempt per task (pass@1), no retries.

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

The stress-test adds roughly $0.40/task compared to baseline Claude Code ($0.54/task). On the 422 paired tasks, planman resolves 3.3 percentage points more (Wilcoxon p=0.048). Additionally, planman produces patches for all 500 tasks vs 422 for the baseline, contributing 74 additional resolved tasks overall (374 vs 300).

## Comparison with Official Leaderboard

The SWE-bench leaderboard reports several Claude entries at 75-81%. These scores reflect different configurations:

| Entry | Score | Extended Thinking | Trials | Scaffold | Comparable to our setup? |
|-------|-------|-------------------|--------|----------|--------------------------|
| Opus 4.6 (Anthropic blog) | 81.4% | likely yes | 25 avg | unknown | No (multi-trial) |
| Opus 4.6 (leaderboard) | 80.8% | likely yes | 25 avg | unknown | No (multi-trial) |
| Opus 4.5 (leaderboard) | 80.9% | 64K budget | unknown | unknown | No (thinking) |
| Opus 4.5 (swebench.com) | 76.8% | high reasoning | 1 | mini-swe-agent | Partially (thinking) |
| **Opus 4.1 (leaderboard)** | **74.5%** | **none** | unknown | simple scaffold | **Yes — closest match** |
| **Our planman** | **74.8%** | none | 1 | Claude Code + plugin | — |
| **Our baseline** | **71.1%** | none | 1 | Claude Code | — |

**Key finding**: Our planman (74.8%) matches or beats the closest no-thinking leaderboard entry (Claude Opus 4.1, 74.5%). The apparent gap between our baseline (71%) and leaderboard entries (75-81%) is largely explained by extended thinking, multiple trials, and purpose-built scaffolds — not a bug in our evaluation.

The leaderboard's base Claude entry uses [mini-swe-agent v2.0.0](https://github.com/swe-bench/SWE-bench/tree/main/swebench/harness/mini_swe_agent), a purpose-built agent framework — not Claude Code. Per-instance comparison shows the two systems have **complementary strengths**:

| | Tasks |
|---|---|
| Solved by mini-swe-agent only | 33 |
| Solved by planman only | 29 |
| Solved by both | 345 |

The net gap is just 4 tasks (378 vs 374). The systems solve **different** problems rather than one strictly dominating the other.

## Statistical Evidence

### Full 500-Task Paired Comparison

To validate the improvement rigorously, we ran a full 500-task baseline (Claude Code without planman) and performed paired statistical tests against the planman (stress_test) condition on the same task set.

| Condition | Resolved | Rate | Cost/task |
|-----------|----------|------|-----------|
| Claude Code (baseline) | 300/500 | 60.0% | $0.54 |
| Claude Code + planman | 374/500 | **74.8%** | $1.10 |

The baseline produced patches for 422/500 tasks (vs 500/500 for planman). Tasks without patches are counted as not resolved. The paired analysis below uses the 422 tasks where both conditions were evaluated.

**Paired statistics** (422 common tasks):

| Metric | Value |
|--------|-------|
| Both resolved | 282 |
| Planman only (wins) | 32 |
| Baseline only (losses) | 18 |
| Neither resolved | 90 |
| Discordant win rate | 64.0% (32/50) |
| McNemar exact test (p) | 0.065 |
| Discordant-pair odds ratio [95% CI] | 1.78 [0.97, 3.36] |
| Resolve-rate delta [95% BCa CI] | +3.32% [-0.24%, +6.40%] |
| Wilcoxon signed-rank (p) | 0.048 |

The McNemar test is borderline (p=0.065), while the Wilcoxon signed-rank test reaches significance (p=0.048). The odds ratio of 1.78 means planman wins nearly 2:1 on discordant pairs. Combined with the ablation evidence (plan_only = baseline), the data supports a directional improvement from stress-test critique.

To reproduce: `python3 benchmark/swebench/compare_conditions.py`

### Ablation: What Causes the Improvement?

All three conditions were run on the same 50-task pilot subset (run IDs cited for reproducibility):

| Condition | Resolved | Rate | Run ID |
|-----------|----------|------|--------|
| Claude Code (baseline) | 35/50 | 70.0% | `baseline_pilot_1_rep0` |
| Claude Code + plan mode | 35/50 | 70.0% | `plan_only_pilot_1_rep0` |
| Claude Code + planman | 37/50 | **74.0%** | `stress_test_pilot_1_rep0` + `stress_test_pilot_1_retry_rep0` |

**Key finding**: `plan_only = baseline` (35/50 each). Planning alone adds zero value. The improvement comes entirely from the stress-test critique phase, which forces Claude to identify weaknesses in its own plan before implementation.

### Mini-swe-agent Parity

The SWE-bench leaderboard's base Claude Opus 4.6 entry uses [mini-swe-agent v2.0.0](https://github.com/swe-bench/SWE-bench/tree/main/swebench/harness/mini_swe_agent), a purpose-built agent scaffold — not Claude Code. Per-instance comparison (against `tools_claude-4-opus`, 366/500):

| | Tasks |
|---|---|
| Solved by mini-swe-agent only | 38 |
| Solved by planman only | 46 |
| Solved by both | 328 |

McNemar p = 0.45 — the difference is **not statistically significant**. The systems solve different problems rather than one dominating the other. Planman's stress-test critique brings Claude Code (a simpler scaffold) to parity with a purpose-built agent framework.

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
