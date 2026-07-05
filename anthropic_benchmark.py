#!/usr/bin/env python3
"""
Anthropic Model Benchmark v3 - Final Stable Version (July 2026)
===============================================================

This is the complete, fixed version that works with the newest Claude models:
- claude-fable-5
- claude-opus-4-8
- claude-sonnet-5

Key fixes included:
- Never sends `temperature` parameter (deprecated on new models)
- Properly handles ThinkingBlock in responses
- More realistic palindrome tests
- Clear error classification
- Rich metrics (latency, tokens/s, success rate, etc.)
- Multiple trials for statistical reliability
- Full MMLU evaluation (all 57 subjects, 5-shot) via HuggingFace cais/mmlu
- Fable 5 strength suite: complex coding, debugging, security, agent planning, finance
- Long-horizon agent session suite: 25 multi-step agentic prompts

Usage:
    export ANTHROPIC_API_KEY="sk-ant-..."
    python3 anthropic_benchmark.py                    # original 3 tasks
    python3 anthropic_benchmark.py --mmlu             # MMLU for all models
    python3 anthropic_benchmark.py --fable-strengths  # 60 prompts where Fable 5 should excel
    python3 anthropic_benchmark.py --agent-sessions  # 25 long-horizon agent session tests
    python3 anthropic_benchmark.py --all              # original tasks + MMLU
    python3 anthropic_benchmark.py --mmlu --mmlu-max-per-subject 10  # quick MMLU sample
"""

import argparse
import os
import time
import json
import re
import statistics
from typing import List, Dict, Any, Optional, Tuple
from anthropic import Anthropic
from tabulate import tabulate

from fable_strength_tasks import build_fable_strength_tasks, evaluate_fable_task
from agent_session_tasks import build_agent_session_tasks, evaluate_agent_session_task

try:
    from datasets import get_dataset_config_names, load_dataset
except ImportError:
    get_dataset_config_names = None  # type: ignore
    load_dataset = None  # type: ignore

MMLU_EXCLUDED_CONFIGS = {"all", "auxiliary_train"}
MMLU_CHOICE_LABELS = ["A", "B", "C", "D"]


def _exec_candidate_code(code: str) -> Dict[str, Any]:
    """Execute model-produced Python in a single shared namespace."""
    namespace: Dict[str, Any] = {"__builtins__": __builtins__}
    exec(code, namespace, namespace)
    return namespace


def extract_code(output: str) -> str:
    """Extract Python code from markdown block."""
    match = re.search(r'```python\s*(.*?)```', output, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else output.strip()


def test_palindrome_function(code: str) -> bool:
    """
    Test is_palindrome function with realistic cases.
    Newer models usually implement normalized versions (ignore case/spaces/punct).
    """
    try:
        namespace = _exec_candidate_code(code)
        func = namespace.get('is_palindrome')
        if not callable(func):
            return False

        # Realistic test cases (most good implementations pass these)
        tests = [
            ("", True),
            ("radar", True),
            ("hello", False),
            ("A man a plan a canal Panama", True),
            ("Was it a car or a cat I saw?", True),
            ("No lemon, no melon", True),
            ("hello world", False),
        ]
        return all(func(s) == expected for s, expected in tests)
    except Exception:
        return False


def test_lru_cache_implementation(code: str) -> bool:
    """Test a complete LRUCache class implementation."""
    try:
        namespace = _exec_candidate_code(code)
        lru_cls = namespace.get("LRUCache")
        if lru_cls is None:
            return False

        cache = lru_cls(2)
        cache.put(1, 1)
        cache.put(2, 2)
        if cache.get(1) != 1:
            return False
        cache.put(3, 3)  # evicts key 2
        if cache.get(2) != -1:
            return False
        cache.put(4, 4)  # evicts key 1
        if cache.get(1) != -1 or cache.get(3) != 3 or cache.get(4) != 4:
            return False

        cache2 = lru_cls(1)
        cache2.put(2, 1)
        cache2.get(2)
        cache2.put(3, 2)
        return cache2.get(2) == -1 and cache2.get(3) == 2
    except Exception:
        return False


def test_binary_search_fix(code: str) -> bool:
    """Test fixed binary_search against edge cases."""
    try:
        namespace = _exec_candidate_code(code)
        func = namespace.get("binary_search")
        if not callable(func):
            return False

        cases = [
            (([1, 2, 3, 4, 5], 1), 0),
            (([1, 2, 3, 4, 5], 5), 4),
            (([1, 2, 3, 4, 5], 3), 2),
            (([1, 2, 3, 4, 5], 6), -1),
            (([5], 5), 0),
            (([5], 2), -1),
            (([], 1), -1),
            (([1, 3, 5, 7], 2), -1),
        ]
        return all(func(arr, target) == expected for (arr, target), expected in cases)
    except Exception:
        return False


def test_sql_injection_fix(code: str) -> bool:
    """Test that get_user uses safe parameterization and blocks injection."""
    try:
        class FakeCursor:
            def __init__(self):
                self.last_query = ""
                self.last_params: Any = None

            def execute(self, query, params=None):
                self.last_query = query
                self.last_params = params

            def fetchone(self):
                if self.last_params and "1" in str(self.last_params[0]):
                    return {"id": 1, "name": "alice"}
                return None

        class FakeConn:
            def __init__(self):
                self._cursor = FakeCursor()

            def cursor(self):
                return self._cursor

        namespace = _exec_candidate_code(code)
        get_user = namespace.get("get_user")
        if not callable(get_user):
            return False

        conn = FakeConn()
        result = get_user(conn, 1)
        if not result or result.get("name") != "alice":
            return False

        cur = conn.cursor()
        if cur.last_params is None:
            return False
        if "{" in cur.last_query or "}" in cur.last_query:
            return False

        # Malicious input must be passed as data, not interpolated into SQL text.
        get_user(conn, "1 OR 1=1")
        cur = conn.cursor()
        normalized = cur.last_query.upper().replace(" ", "")
        if "OR1=1" in normalized or "'1OR1=1'" in normalized:
            return False
        return cur.last_params is not None
    except Exception:
        return False


def evaluate_agent_plan(output: str) -> Dict[str, Any]:
    """Score multi-phase migration plan quality."""
    text = output.lower()
    phase_count = len(re.findall(r"<phase\s*\d*>", text, re.IGNORECASE))
    if phase_count == 0:
        phase_count = len(re.findall(r"(?:^|\n)\s*(?:phase|stage)\s*\d+[:.)]", text, re.I))

    has_data = any(k in text for k in ["database", "data migration", "schema", "postgres", "sql"])
    has_rollout = any(k in text for k in ["rollback", "canary", "feature flag", "staged", "incremental"])
    has_testing = any(k in text for k in ["test", "monitor", "observability", "slo", "load"])
    has_deliverable = any(k in text for k in ["deliverable", "milestone", "checkpoint", "success criteria"])

    checks = [phase_count >= 5, has_data, has_rollout, has_testing, has_deliverable]
    passed = sum(checks)

    if passed >= 4:
        return {"score": 100.0, "note": f"Strong plan ({phase_count} phases, {passed}/5 criteria)", "correct": True}
    if passed >= 2:
        return {"score": 65.0, "note": f"Partial plan ({phase_count} phases, {passed}/5 criteria)", "correct": False}
    return {"score": 30.0, "note": f"Weak plan ({phase_count} phases, {passed}/5 criteria)", "correct": False}


def evaluate_npv_answer(output: str) -> Dict[str, Any]:
    """NPV of [-100, 30, 40, 50, 60] at 10% ≈ 38.88."""
    match = re.search(r"<answer>\s*([-+]?\d*\.?\d+)\s*</answer>", output, re.I)
    if not match:
        nums = re.findall(r"[-+]?\d+\.?\d*", output)
        value = float(nums[-1]) if nums else None
    else:
        value = float(match.group(1))

    if value is None:
        return {"score": 0.0, "note": "No numeric answer found", "correct": False}

    correct = abs(value - 38.88) < 1.5
    return {
        "score": 100.0 if correct else 0.0,
        "note": f"{'Correct' if correct else 'Wrong'} (got {value:.2f}, expected ~38.88)",
        "correct": correct,
    }


def get_mmlu_subjects() -> List[str]:
    """Return all 57 MMLU subject configs from HuggingFace cais/mmlu."""
    if get_dataset_config_names is None:
        raise ImportError(
            "The 'datasets' package is required for MMLU. "
            "Install it with: pip install datasets"
        )
    return sorted(
        c for c in get_dataset_config_names("cais/mmlu")
        if c not in MMLU_EXCLUDED_CONFIGS
    )


def format_mmlu_subject_name(subject: str) -> str:
    """Turn 'high_school_physics' into 'high school physics'."""
    return subject.replace("_", " ")


def format_mmlu_question(
    question: str, choices: List[str], include_answer: bool = False, answer_idx: int = 0
) -> str:
    """Format a single MMLU multiple-choice question."""
    lines = [f"Question: {question}"]
    for label, choice in zip(MMLU_CHOICE_LABELS, choices):
        lines.append(f"{label}. {choice}")
    if include_answer:
        lines.append(f"Answer: {MMLU_CHOICE_LABELS[answer_idx]}")
    else:
        lines.append("Answer:")
    return "\n".join(lines)


def build_mmlu_prompt(
    subject: str,
    question: str,
    choices: List[str],
    few_shot_examples: List[Dict[str, Any]],
) -> str:
    """Build a standard 5-shot MMLU prompt."""
    subject_name = format_mmlu_subject_name(subject)
    header = (
        f"The following are multiple choice questions (with answers) "
        f"about {subject_name}.\n"
    )
    parts = [header]
    for ex in few_shot_examples:
        parts.append(
            format_mmlu_question(
                ex["question"], ex["choices"], include_answer=True, answer_idx=ex["answer"]
            )
        )
        parts.append("")
    parts.append(format_mmlu_question(question, choices, include_answer=False))
    return "\n".join(parts)


def parse_mmlu_answer(output: str) -> Optional[str]:
    """
    Extract the predicted choice letter (A-D) from model output.
    Prefers explicit 'Answer:' patterns, then falls back to the last lone letter.
    """
    text = output.strip()
    if not text:
        return None

    explicit_patterns = [
        r"(?:the\s+)?(?:final\s+)?answer\s*(?:is|:)\s*\(?([A-Da-d])\)?",
        r"(?:choice|option)\s*(?:is|:)\s*\(?([A-Da-d])\)?",
        r"^([A-Da-d])\s*[\.\):\-]",
    ]
    for pattern in explicit_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
        if matches:
            return matches[-1].upper()

    letter_matches = re.findall(r"\b([A-Da-d])\b", text)
    if letter_matches:
        return letter_matches[-1].upper()

    return None


def evaluate_mmlu_answer(output: str, correct_idx: int) -> Dict[str, Any]:
    """Score a single MMLU response."""
    predicted = parse_mmlu_answer(output)
    correct_label = MMLU_CHOICE_LABELS[correct_idx]
    is_correct = predicted == correct_label
    if is_correct:
        note = f"Correct ({correct_label})"
    elif predicted:
        note = f"Wrong (got {predicted}, expected {correct_label})"
    else:
        note = f"Unparseable (expected {correct_label})"
    return {
        "score": 100.0 if is_correct else 0.0,
        "note": note,
        "correct": is_correct,
        "predicted": predicted,
        "expected": correct_label,
    }


class AnthropicBenchmark:
    def __init__(self, api_key: str):
        self.client = Anthropic(api_key=api_key)
        self.models: Dict[str, str] = {
            "Fable 5 (frontier)": "claude-fable-5",
            "Opus 4.8": "claude-opus-4-8",
            "Sonnet 5 (latest)": "claude-sonnet-5",
        }

    def query_model(
        self,
        model_id: str,
        prompt: str,
        temperature: Optional[float] = None,
        max_tokens: int = 1200,
    ) -> Dict[str, Any]:
        """
        Call Anthropic API.
        - Never sends temperature (deprecated on Fable 5 / Sonnet 5 / recent Opus)
        - Handles ThinkingBlock responses correctly
        """
        start = time.time()
        try:
            call_params = {
                "model": model_id,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}]
            }

            # Only include temperature if explicitly requested
            if temperature is not None:
                call_params["temperature"] = temperature

            resp = self.client.messages.create(**call_params)

            # Extract text, skipping ThinkingBlock and other non-text blocks
            output_parts = []
            for block in resp.content or []:
                if hasattr(block, "text"):
                    output_parts.append(block.text)

            output = "".join(output_parts).strip()

            latency = round(time.time() - start, 3)
            usage = getattr(resp, "usage", None)

            return {
                "output": output,
                "latency": latency,
                "input_tokens": getattr(usage, "input_tokens", 0) if usage else 0,
                "output_tokens": getattr(usage, "output_tokens", 0) if usage else 0,
                "error": None,
                "error_type": None
            }

        except Exception as e:
            latency = round(time.time() - start, 3)
            err_str = str(e)
            error_type = "unknown"
            if "temperature" in err_str.lower():
                error_type = "temperature_deprecated"
            elif "ThinkingBlock" in err_str:
                error_type = "thinking_block"

            return {
                "output": f"ERROR: {err_str}",
                "latency": latency,
                "input_tokens": 0,
                "output_tokens": 0,
                "error": err_str,
                "error_type": error_type
            }

    def evaluate(
        self,
        task: Dict[str, Any],
        output: str,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Task-aware evaluation."""
        if error:
            return {"score": 0.0, "note": f"API Error", "correct": False}

        if task.get("evaluator"):
            if task.get("suite") == "agent_sessions":
                return evaluate_agent_session_task(output, task)
            return evaluate_fable_task(output, task)

        task_name = task["name"]
        out_lower = output.lower()

        if task_name == "Coding Task (Palindrome)":
            code = extract_code(output)
            passed = test_palindrome_function(code)
            return {
                "score": 100.0 if passed else 0.0,
                "note": "All tests passed" if passed else "Failed tests or no valid function",
                "correct": passed
            }

        elif "Math + Chain-of-Thought" in task_name:
            correct = any(x in out_lower for x in ["0.05", ".05", "5 cents", "five cents"])
            if correct:
                score = 100.0
                note = "Correct answer (0.05)"
            elif any(kw in out_lower for kw in ["step", "therefore", "reason"]):
                score = 60.0
                note = "Partial reasoning"
            else:
                score = 30.0
                note = "Weak"
            return {"score": score, "note": note, "correct": correct}

        elif "Multi-step Analytical" in task_name:
            correct = any(x in out_lower for x in ["720", "seven hundred twenty"])
            if correct:
                score = 95.0
                note = "Correct (720)"
            elif any(kw in out_lower for kw in ["step", "240", "80", "grows", "rate"]):
                score = 60.0
                note = "Partial reasoning"
            else:
                score = 30.0
                note = "Weak"
            return {"score": score, "note": note, "correct": correct}

        return {"score": 50.0, "note": "N/A", "correct": False}

    def run(
        self,
        tasks: List[Dict],
        temperature: Optional[float] = None,
        n_trials: int = 3,
        verbose: bool = True,
        resume_cache: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Tuple[List[Dict], List[Dict]]:
        """Run benchmark with multiple trials.

        resume_cache: optional dict keyed by 'task|model|trial' -> prior trial detail
        for trials that completed without API error (skip re-running those).
        """
        summary_rows: List[Dict] = []
        detailed_log: List[Dict] = []
        resume_cache = resume_cache or {}

        for task in tasks:
            task_name = task["name"]
            prompt = task["prompt"]

            if verbose:
                print(f"\n{'='*70}")
                print(f"=== {task_name} ===")
                print(f"Prompt preview: {prompt[:100]}...")

            for model_name, model_id in self.models.items():
                if verbose:
                    print(f"\n → {model_name} ({model_id}) × {n_trials} trials")

                trial_scores, trial_latencies, trial_out_tokens = [], [], []
                trial_success = 0
                trial_details = []

                max_tokens = task.get("max_tokens", 1200)

                for trial_idx in range(n_trials):
                    cache_key = f"{task_name}|{model_name}|{trial_idx + 1}"
                    cached = resume_cache.get(cache_key)

                    if cached and cached.get("error") is None:
                        detail = dict(cached)
                        detail["task"] = task_name
                        trial_scores.append(detail["score"])
                        trial_latencies.append(detail["latency_s"])
                        trial_out_tokens.append(detail.get("output_tokens", 0))
                        trial_success += 1
                        if verbose:
                            print(
                                f"   Trial {trial_idx+1}: ↺ cached "
                                f"score={detail['score']:.0f}% | "
                                f"lat={detail['latency_s']:.2f}s | "
                                f"out_tok={detail.get('output_tokens',0)} | "
                                f"{detail.get('note','')[:45]}"
                            )
                    else:
                        res = self.query_model(
                            model_id, prompt, temperature, max_tokens=max_tokens
                        )
                        eval_res = self.evaluate(task, res["output"], res.get("error"))

                        trial_scores.append(eval_res["score"])
                        trial_latencies.append(res["latency"])
                        trial_out_tokens.append(res.get("output_tokens", 0))

                        if res.get("error") is None:
                            trial_success += 1

                        detail = {
                            "trial": trial_idx + 1,
                            "task": task_name,
                            "model": model_name,
                            "score": eval_res["score"],
                            "latency_s": res["latency"],
                            "output_tokens": res.get("output_tokens", 0),
                            "input_tokens": res.get("input_tokens", 0),
                            "correct": eval_res.get("correct", False),
                            "note": eval_res.get("note", ""),
                            "error": res.get("error"),
                            "error_type": res.get("error_type"),
                        }
                        if verbose:
                            status = "✓" if res.get("error") is None else "✗"
                            print(
                                f"   Trial {trial_idx+1}: {status} "
                                f"score={eval_res['score']:.0f}% | "
                                f"lat={res['latency']:.2f}s | "
                                f"out_tok={res.get('output_tokens',0)} | "
                                f"{eval_res['note'][:45]}"
                            )

                    trial_details.append(detail)
                    detailed_log.append(detail)

                n = len(trial_scores)
                mean_score = round(statistics.mean(trial_scores), 1) if n > 0 else 0
                std_score = round(statistics.stdev(trial_scores), 1) if n > 1 else 0
                mean_lat = round(statistics.mean(trial_latencies), 3) if n > 0 else 0
                std_lat = round(statistics.stdev(trial_latencies), 3) if n > 1 else 0
                mean_tokens = round(statistics.mean(trial_out_tokens), 0) if n > 0 else 0
                success_rate = round(100 * trial_success / n_trials, 1) if n_trials > 0 else 0
                tokens_per_sec = round(mean_tokens / mean_lat, 1) if mean_lat > 0.1 else 0.0

                row = {
                    "task": task_name,
                    "model": model_name,
                    "model_id": model_id,
                    "n_trials": n_trials,
                    "mean_score": mean_score,
                    "std_score": std_score,
                    "mean_latency_s": mean_lat,
                    "std_latency_s": std_lat,
                    "mean_output_tokens": int(mean_tokens),
                    "tokens_per_sec": tokens_per_sec,
                    "success_rate_pct": success_rate,
                }
                summary_rows.append(row)

                if verbose:
                    print(
                        f"   SUMMARY → Mean Score: {mean_score}% (±{std_score}) | "
                        f"Latency: {mean_lat}s (±{std_lat}) | "
                        f"Tok/s: {tokens_per_sec} | Success: {success_rate}%"
                    )

        return summary_rows, detailed_log

    def run_mmlu(
        self,
        n_shots: int = 5,
        max_per_subject: Optional[int] = None,
        subjects: Optional[List[str]] = None,
        temperature: Optional[float] = None,
        save_questions: bool = False,
        verbose: bool = True,
    ) -> Tuple[List[Dict], Dict[str, Any]]:
        """
        Run full MMLU evaluation for every model.

        Uses the cais/mmlu test split with n-shot examples from the dev split.
        """
        subject_list = subjects or get_mmlu_subjects()
        if verbose:
            print(f"\n{'='*70}")
            print("=== MMLU (all subjects) ===")
            print(
                f"Subjects: {len(subject_list)} | Shots: {n_shots} | "
                f"Max per subject: {max_per_subject or 'all'}"
            )
            if max_per_subject is None:
                print(
                    "⚠️  Full MMLU is ~14k questions per model (~42k API calls for "
                    "3 models). Use --mmlu-max-per-subject to sample."
                )

        summary_rows: List[Dict] = []
        by_subject: Dict[str, Dict[str, Dict[str, Any]]] = {}
        question_log: Optional[List[Dict]] = [] if save_questions else None

        for model_name, model_id in self.models.items():
            if verbose:
                print(f"\n → {model_name} ({model_id})")

            model_correct = 0
            model_total = 0
            model_latencies: List[float] = []
            model_out_tokens: List[int] = []
            model_success = 0
            by_subject[model_id] = {}

            for subject in subject_list:
                dev_ds = load_dataset("cais/mmlu", subject, split="dev")
                test_ds = load_dataset("cais/mmlu", subject, split="test")

                few_shot = [dev_ds[i] for i in range(min(n_shots, len(dev_ds)))]
                test_items = list(test_ds)
                if max_per_subject is not None:
                    test_items = test_items[:max_per_subject]

                subject_correct = 0
                subject_latencies: List[float] = []

                for q_idx, item in enumerate(test_items):
                    prompt = build_mmlu_prompt(
                        subject,
                        item["question"],
                        item["choices"],
                        few_shot,
                    )
                    res = self.query_model(
                        model_id,
                        prompt,
                        temperature,
                        max_tokens=64,
                    )
                    eval_res = evaluate_mmlu_answer(res["output"], item["answer"])

                    subject_correct += int(eval_res["correct"])
                    model_correct += int(eval_res["correct"])
                    model_total += 1
                    model_latencies.append(res["latency"])
                    model_out_tokens.append(res.get("output_tokens", 0))
                    subject_latencies.append(res["latency"])
                    if res.get("error") is None:
                        model_success += 1

                    if question_log is not None:
                        question_log.append({
                            "model": model_name,
                            "model_id": model_id,
                            "subject": subject,
                            "question_idx": q_idx,
                            "correct": eval_res["correct"],
                            "predicted": eval_res["predicted"],
                            "expected": eval_res["expected"],
                            "latency_s": res["latency"],
                            "output_tokens": res.get("output_tokens", 0),
                            "error": res.get("error"),
                        })

                subject_total = len(test_items)
                subject_accuracy = (
                    round(100 * subject_correct / subject_total, 1)
                    if subject_total else 0.0
                )
                by_subject[model_id][subject] = {
                    "correct": subject_correct,
                    "total": subject_total,
                    "accuracy_pct": subject_accuracy,
                    "mean_latency_s": (
                        round(statistics.mean(subject_latencies), 3)
                        if subject_latencies else 0.0
                    ),
                }

                if verbose:
                    print(
                        f"   {subject}: {subject_accuracy:.1f}% "
                        f"({subject_correct}/{subject_total})"
                    )

            mean_score = (
                round(100 * model_correct / model_total, 1) if model_total else 0.0
            )
            mean_lat = (
                round(statistics.mean(model_latencies), 3) if model_latencies else 0.0
            )
            std_lat = (
                round(statistics.stdev(model_latencies), 3)
                if len(model_latencies) > 1 else 0.0
            )
            mean_tokens = (
                round(statistics.mean(model_out_tokens), 0) if model_out_tokens else 0
            )
            success_rate = (
                round(100 * model_success / model_total, 1) if model_total else 0.0
            )
            tokens_per_sec = round(mean_tokens / mean_lat, 1) if mean_lat > 0.1 else 0.0

            row = {
                "task": "MMLU (all subjects)",
                "model": model_name,
                "model_id": model_id,
                "n_trials": model_total,
                "mean_score": mean_score,
                "std_score": 0.0,
                "mean_latency_s": mean_lat,
                "std_latency_s": std_lat,
                "mean_output_tokens": int(mean_tokens),
                "tokens_per_sec": tokens_per_sec,
                "success_rate_pct": success_rate,
                "mmlu_correct": model_correct,
                "mmlu_total": model_total,
            }
            summary_rows.append(row)

            if verbose:
                print(
                    f"   SUMMARY → Accuracy: {mean_score}% "
                    f"({model_correct}/{model_total}) | "
                    f"Latency: {mean_lat}s/question | "
                    f"Tok/s: {tokens_per_sec} | Success: {success_rate}%"
                )

        mmlu_payload: Dict[str, Any] = {
            "n_shots": n_shots,
            "max_per_subject": max_per_subject,
            "subjects": subject_list,
            "summary": summary_rows,
            "by_subject": by_subject,
        }
        if question_log is not None:
            mmlu_payload["questions"] = question_log
        return summary_rows, mmlu_payload

    def save_results(
        self,
        summary_rows: List[Dict],
        detailed_log: List[Dict],
        *,
        temperature: Optional[float],
        n_trials: int,
        mmlu: Optional[Dict[str, Any]] = None,
        agent_sessions: Optional[Dict[str, Any]] = None,
        suites_run: Optional[List[str]] = None,
        output_path: str = "benchmark_results_v3.json",
        verbose: bool = True,
    ) -> None:
        """Persist benchmark results to JSON."""
        payload: Dict[str, Any] = {
            "metadata": {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "temperature": temperature,
                "n_trials": n_trials,
                "models": list(self.models.keys()),
                "suites_run": suites_run or [],
            },
            "summary": summary_rows,
            "detailed_trials": detailed_log,
        }
        if mmlu is not None:
            payload["mmlu"] = mmlu
        if agent_sessions is not None:
            payload["agent_sessions"] = agent_sessions

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        if verbose:
            print(f"\n✅ Results saved to {output_path}")

    def print_summary_table(self, summary_rows: List[Dict]):
        headers = [
            "Task", "Model", "Mean Score (%)", "±Std", "Mean Lat (s)", "±Std",
            "Out Tokens", "Tok/s", "Success %"
        ]
        table_data = []
        for r in summary_rows:
            table_data.append([
                r["task"][:32] + ("..." if len(r["task"]) > 32 else ""),
                r["model"],
                r["mean_score"], r["std_score"],
                r["mean_latency_s"], r["std_latency_s"],
                r["mean_output_tokens"],
                r["tokens_per_sec"],
                r["success_rate_pct"]
            ])
        print("\n" + tabulate(table_data, headers=headers, tablefmt="grid", floatfmt=".1f"))


# ================== TASKS ==================
tasks = [
    {
        "name": "Coding Task (Palindrome)",
        "prompt": """Write a correct Python function `def is_palindrome(s: str) -> bool:`.
The function should ignore spaces, punctuation, and case.
Include the function in a ```python block."""
    },
    {
        "name": "Math + Chain-of-Thought Reasoning",
        "prompt": """Solve step by step. Put final answer in <answer>NUMBER</answer>.
A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the ball. How much does the ball cost?"""
    },
    {
        "name": "Multi-step Analytical Reasoning",
        "prompt": """Think step by step. A company has 3x as many employees as it did 2 years ago.
If it now has 240 employees and grows at the same rate, how many will it have in 2 more years?
Explain clearly."""
    }
]


fable_strength_tasks = build_fable_strength_tasks()
agent_session_tasks = build_agent_session_tasks()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark Claude models on custom tasks and/or MMLU."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--mmlu",
        action="store_true",
        help="Run MMLU evaluation for all models (all 57 subjects).",
    )
    mode.add_argument(
        "--all",
        action="store_true",
        help="Run both the original 3 tasks and full MMLU.",
    )
    parser.add_argument(
        "--fable-strengths",
        action="store_true",
        help=(
            "Run the Fable 5 strength suite (50+ prompts): complex coding, debugging, "
            "security, agent planning, finance, and refactoring."
        ),
    )
    parser.add_argument(
        "--agent-sessions",
        action="store_true",
        help=(
            "Run 25 long-horizon agent session tests (multi-step observe/plan/act/verify, "
            "delegation, checkpoints)."
        ),
    )
    parser.add_argument(
        "--mmlu-shots",
        type=int,
        default=5,
        help="Number of few-shot examples per MMLU question (default: 5).",
    )
    parser.add_argument(
        "--mmlu-max-per-subject",
        type=int,
        default=None,
        help="Limit MMLU test questions per subject (default: all).",
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=3,
        help="Trials per original task (default: 3).",
    )
    parser.add_argument(
        "--output",
        default="benchmark_results_v3.json",
        help="Output JSON path (default: benchmark_results_v3.json).",
    )
    parser.add_argument(
        "--mmlu-save-questions",
        action="store_true",
        help="Include per-question MMLU logs in JSON (large for full runs).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from existing --output JSON; skip trials that completed without API errors.",
    )
    return parser.parse_args()


def load_resume_cache(
    output_path: str,
    tasks: Optional[List[Dict]] = None,
    models: Optional[Dict[str, str]] = None,
    n_trials: int = 3,
) -> Dict[str, Dict[str, Any]]:
    """Load prior successful trials from a results JSON file."""
    if not os.path.isfile(output_path):
        return {}
    with open(output_path, encoding="utf-8") as f:
        data = json.load(f)
    cache: Dict[str, Dict[str, Any]] = {}
    details = data.get("detailed_trials", [])

    for detail in details:
        if detail.get("error") is not None:
            continue
        task = detail.get("task")
        model = detail.get("model")
        trial = detail.get("trial")
        if task and model and trial:
            cache[f"{task}|{model}|{trial}"] = detail

    # Legacy results without per-trial task names: reconstruct from run order
    if not cache and details and tasks and models:
        model_names = list(models.keys())
        idx = 0
        for task in tasks:
            task_name = task["name"]
            for model_name in model_names:
                for trial in range(1, n_trials + 1):
                    if idx >= len(details):
                        break
                    d = details[idx]
                    idx += 1
                    if d.get("error") is None:
                        entry = dict(d)
                        entry["task"] = task_name
                        cache[f"{task_name}|{model_name}|{trial}"] = entry
    return cache


if __name__ == "__main__":
    args = parse_args()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ Please set ANTHROPIC_API_KEY environment variable.")
        raise SystemExit(1)

    run_original = not (args.mmlu or args.fable_strengths or args.agent_sessions)
    run_mmlu_flag = args.mmlu or args.all

    if (
        args.agent_sessions
        and not args.fable_strengths
        and not run_mmlu_flag
        and not run_original
        and args.output == "benchmark_results_v3.json"
    ):
        args.output = "benchmark_agent_sessions.json"

    print("🚀 Starting Anthropic Benchmark v3 (stable for Fable 5 / Opus 4.8 / Sonnet 5)")
    bench = AnthropicBenchmark(api_key)

    all_summary: List[Dict] = []
    detailed_log: List[Dict] = []
    mmlu_payload: Optional[Dict[str, Any]] = None
    agent_sessions_payload: Optional[Dict[str, Any]] = None
    suites_run: List[str] = []

    if run_original:
        suites_run.append("original")
        orig_cache = (
            load_resume_cache(args.output, tasks=tasks, models=bench.models, n_trials=args.trials)
            if args.resume else None
        )
        task_summary, detailed_log = bench.run(
            tasks,
            temperature=None,
            n_trials=args.trials,
            verbose=True,
            resume_cache=orig_cache,
        )
        all_summary.extend(task_summary)

    if args.fable_strengths:
        suites_run.append("fable_strengths")
        print(
            f"\n🎯 Fable 5 Strength Suite — {len(fable_strength_tasks)} prompts "
            "(coding, debugging, security, planning, finance, refactoring)"
        )
        fable_cache = (
            load_resume_cache(
                args.output, tasks=fable_strength_tasks, models=bench.models, n_trials=args.trials
            )
            if args.resume else None
        )
        fable_summary, fable_log = bench.run(
            fable_strength_tasks,
            temperature=None,
            n_trials=args.trials,
            verbose=True,
            resume_cache=fable_cache,
        )
        all_summary.extend(fable_summary)
        detailed_log.extend(fable_log)

    resume_cache: Dict[str, Dict[str, Any]] = {}

    if args.agent_sessions:
        suites_run.append("agent_sessions")
        if args.resume:
            resume_cache = load_resume_cache(
                args.output,
                tasks=agent_session_tasks,
                models=bench.models,
                n_trials=args.trials,
            )
            if resume_cache:
                print(
                    f"↺ Resume: skipping {len(resume_cache)} completed trials "
                    f"from {args.output}"
                )
            else:
                print(f"↺ Resume: no cache in {args.output}, running fresh")
        print(
            f"\n🤖 Long-Horizon Agent Sessions — {len(agent_session_tasks)} prompts "
            "(multi-step agentic work, delegation, checkpoints)"
        )
        agent_summary, agent_log = bench.run(
            agent_session_tasks,
            temperature=None,
            n_trials=args.trials,
            verbose=True,
            resume_cache=resume_cache,
        )
        all_summary.extend(agent_summary)
        detailed_log.extend(agent_log)
        agent_sessions_payload = {
            "n_tasks": len(agent_session_tasks),
            "summary": agent_summary,
        }

    if run_mmlu_flag:
        suites_run.append("mmlu")
        mmlu_summary, mmlu_payload = bench.run_mmlu(
            n_shots=args.mmlu_shots,
            max_per_subject=args.mmlu_max_per_subject,
            temperature=None,
            save_questions=args.mmlu_save_questions,
            verbose=True,
        )
        all_summary.extend(mmlu_summary)

    bench.save_results(
        all_summary,
        detailed_log,
        temperature=None,
        n_trials=args.trials,
        mmlu=mmlu_payload,
        agent_sessions=agent_sessions_payload,
        suites_run=suites_run,
        output_path=args.output,
        verbose=True,
    )
    bench.print_summary_table(all_summary)
    print(f"\n📊 Done. Check {args.output} for full details.")
