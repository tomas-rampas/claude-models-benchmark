# claude-models-benchmark

Custom benchmark harness for comparing Anthropic Claude models (Fable 5, Opus 4.8, Sonnet 5) with a focus on **testing rationales** aligned to real-world usage and official model positioning.

## Project Purpose

Standard academic benchmarks (e.g. MMLU) do not reflect the workloads Anthropic positions Fable 5 for: long-horizon agentic coding, complex multi-phase planning, delegation to sub-agents, enterprise workflows, and security-sensitive development.

This project was built to answer:

- Where does the flagship "Fable 5" model actually outperform (or underperform) the faster Sonnet 5 and high-capability Opus 4.8?
- How do safeguard behaviors, response style, and verbosity affect measurable outcomes in an automatable harness?
- Does a purpose-built agent-session suite reveal different relative strengths than short-form or MCQ tests?

## Models Under Test

| Model            | API ID              | Anthropic Positioning (as of July 2026)                  | Role in This Benchmark |
|------------------|---------------------|----------------------------------------------------------|------------------------|
| **Fable 5**      | `claude-fable-5`    | Flagship for hardest knowledge work, long-horizon coding & agents. Can "work for days". | Primary target — expensive, high-capability, safeguard-heavy |
| **Opus 4.8**     | `claude-opus-4-8`   | High-capability GA model; recommended fallback when Fable safeguards trigger. | Strong baseline for complex work |
| **Sonnet 5**     | `claude-sonnet-5`   | Fast, balanced latest-tier model.                        | Speed/price/performance reference |

**Pricing note**: Fable 5 is premium-tier ($10 / M input, $50 / M output).

## Testing Rationales

### Why These Four Suites?

The suites were deliberately layered from simple baseline → broad knowledge → "Fable marketing claims" → "Fable intended use case".

| Suite | Tasks | Trials | API Calls | Primary Goal | Key Rationale |
|-------|-------|--------|-----------|----------------|---------------|
| Original 3 | 3 | 3 | 27 | Quick smoke test | Validate harness + basic coding + classic cognitive bias / multi-step math |
| MMLU sample | 570 | 1 | 1,710 | Broad knowledge comparison | Industry-standard academic benchmark. Sampled for runtime. Reveals biology/medicine safeguard impact on Fable. |
| Fable Strength | 60 | 3 | 540 | Tasks matching public Fable positioning | Complex coding, debugging, security hardening, agent planning, finance, refactoring. Tests where Fable *should* win. |
| Agent Sessions | 25 | 3 | 225 | Long-horizon autonomous work | Closest proxy to Fable's design center: observe→plan→act→verify loops, checkpoints, sub-agent delegation, multi-day style workflows. |

### Suite 1: Original 3 Tasks (Baseline Sanity)

**Rationale**: Start with trivial tasks that any capable model should ace. Establish that the harness, code extraction, and execution work.

- Palindrome (realistic cases including punctuation/casing)
- Bat & Ball CRT problem (classic system-1/system-2)
- Multi-step growth rate analytical problem

**Finding signal**: All models tied at ~98%. Sonnet was dramatically faster. Good control — differences later are not due to basic capability.

### Suite 2: MMLU Sample (57 subjects × 10 questions)

**Rationale**:
- Provide an apples-to-apples comparison against public leaderboards.
- Use 5-shot dev split from `cais/mmlu`.
- `max_tokens=64` (expects short letter answers).
- **Important**: deliberately run without Fallback API routing to surface Fable's documented biology & cybersecurity safeguards.

**Key design choice**: 10 questions per subject (not full ~14k) was a practicality tradeoff for ~100 min runtime.

**What it exposed**:
- Fable 5: 17.8% on Biology/Medicine (0% on anatomy, college_biology, etc.) vs 84–93% for others.
- Fable produces longer answers (41 tok/Q) vs Sonnet (18).
- This is **not** "Fable is bad at biology" — it is a harness + safeguard + format interaction.

### Suite 3: Fable Strength Suite (60 Prompts)

**Rationale**: Directly derived from Anthropic's public claims about Fable 5.

Categories and counts:
- Complex coding (12): LRU, MinStack, merge intervals, coin change, Kadane, etc. — real leetcode-style with executable harnesses.
- Debugging (12): buggy implementations of common algorithms. Tests fix quality + harness robustness.
- Security coding (8): SQLi, shell injection, path traversal, XSS, pickle, constant-time compare, filename sanitization, safe redirects.
- Agent planning (10): 5-phase tagged plans for migrations, DR, SOC2, ML platform, sharding, etc.
- Finance analysis (10): NPV, ROI, gross margin, break-even, CAGR, payback, ratios — numeric answers with tolerance.
- Refactoring + data reasoning (8): extract helpers, remove globals, CSV, logs, rolling avg.

**Evaluator design**:
- `code_harness`: exec the extracted ```python block against unit-test assertions.
- `agent_plan`: count `<phaseN>` + keyword groups → 100/65/30 partial credit.
- Security uses both pattern matching and execution.

**Known artifact**: Debugging tasks embed buggy code inside nested fences. `extract_code()` takes the *first* block, which often captures the buggy code instead of the fix. This disproportionately hurt Fable (empty/short responses on security-adjacent prompts).

### Suite 4: Long-Horizon Agent Sessions (25 Prompts) — Most Important Suite

**Rationale**: This is the suite designed to match Fable 5's stated strengths (multi-day autonomous work, planning + delegation).

All prompts use a common header:
> You are an autonomous long-horizon agent... For EACH step document: OBSERVE → PLAN → ACT → VERIFY inside `<step1>`...`</step1>`. Add at least one `<checkpoint>`... End with a clear deliverable section.

Categories:
- Agent coding (6): build Task API, ETL pipeline, JobQueue, retry decorator, etc. over 6+ steps + final executable code.
- Planning / ops (7): Platform Migration 180d, Production Incident Response, Microservices, ML Productionization, Security Hardening, etc.
- Research / ADR (4): Architecture Decision Records, Postmortems, SRE Runbooks, Competitive Roadmaps.
- Delegation (5): Parent orchestrates 3+ `<subagentN>` workers with synthesis.
- Autonomous workflows (3): Week-long refactor simulation, test coverage sprint, docs generation.

**Evaluation**:
- `eval_agent_session_rubric`: counts steps/checkpoints, verifies keywords, deliverable tags, observe/plan/act language → 100/65/30.
- `eval_agent_code_session`: rubric + last ```python block must pass harness.
- `eval_agent_delegation`: requires `<subagent*>` tags + parent coordination language.

**Why max_tokens=8192**: Long-horizon outputs routinely need it. Sonnet often hit the limit.

**Why single-shot**: The harness does not implement tool use or true multi-turn loops. This is an intentional limitation documented below.

## Methodology Choices & Their Rationales

| Decision | Rationale |
|----------|-----------|
| 3 trials per task | Measure variance and reduce impact of single bad draws (e.g. one Fable 30% outlier on incident response). |
| `temperature=None` (never sent) | Required for Fable 5 / Sonnet 5 / recent Opus. Sending it causes errors. |
| No Fallback API configuration | Surfaces raw safeguard behavior on bio/cyber prompts. Realistic for many direct API users. |
| First ` ```python ` block (most suites) vs last (agent) | Simple extraction. Agent traces often contain multiple blocks; debugging tasks suffer from this. |
| Partial credit rubrics (100/65/30) | Planning and agent work are not binary. Weak traces still have value. |
| Binary harness execution for code | Objective correctness. No partial credit on unit tests. |
| Single-shot prompts | Fast, reproducible, automatable. Does **not** exercise Fable's multi-turn / computer-use strengths. |

## Running

```bash
export ANTHROPIC_API_KEY="sk-ant-..."

# Using the project's venv python (recommended)
./bin/python3 anthropic_benchmark.py                    # original 3 tasks

# MMLU sample (quick)
./bin/python3 anthropic_benchmark.py --mmlu --mmlu-max-per-subject 10

# Fable-aligned suite (60 prompts)
./bin/python3 anthropic_benchmark.py --fable-strengths

# Long-horizon agent sessions (the most relevant for Fable 5)
./bin/python3 anthropic_benchmark.py --agent-sessions --resume
```

Results are saved to JSON (`benchmark_results_v3.json`, `benchmark_agent_sessions.json`). A detailed human-readable analysis is in `BENCHMARK_REPORT.md`.

Requirements: see `requirements.txt` (`anthropic>=0.40.0`, `tabulate`, `datasets`). The `bin/` directory contains the activated virtualenv used for all runs.

## Key Findings (Summary)

See the full `BENCHMARK_REPORT.md` for details and tables.

- **Macro average**: Opus 4.8 (87.7%) ≈ Sonnet 5 (87.5%) > Fable 5 (78.6%)
- On **agent sessions** (the most relevant suite): essentially tied (Sonnet 87.3%, Fable 86.9%).
- Fable wins or ties on delegation, agent planning, and some complex coding.
- Largest gaps come from:
  - Short MCQ format (MMLU)
  - Biology/medicine (safeguards)
  - Debugging tasks (empty responses + first-block extraction)
- Fable is slower and more verbose — advantages in short-form harnesses become liabilities.

**Bottom line from the report**: Sonnet "wins" this benchmark largely because the harness favors concise, single-shot, non-safeguarded responses. On the workload Fable was built for, the models are very close.

## Limitations (Explicitly Acknowledged)

- No multi-turn conversation loops or tool calling.
- No Fallback API routing for Fable.
- `extract_code()` first-block heuristic hurts debugging tasks.
- MMLU results were not persisted separately (overwritten by later runs).
- Binary scoring on most code tasks (harsh on near-correct implementations).
- Does not measure cost per correct answer, throughput, or long-context document work.
- Small sample sizes on some categories.

These limitations are by design for a focused, reproducible CLI benchmark. They also explain many of the observed differences.

## License

See `LICENSE`.

---

*Generated for the claude-models-benchmark project (July 2026). Results are harness-specific and not official Anthropic benchmarks.*