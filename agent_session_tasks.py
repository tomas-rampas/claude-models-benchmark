"""
Long-horizon agent session benchmark (25 prompts).

Simulates multi-step agentic work: observe → plan → act → verify loops,
checkpoints, sub-agent delegation, and deliverables — the domain Fable 5
is designed for.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional


def _exec_code(code: str) -> Dict[str, Any]:
    namespace: Dict[str, Any] = {"__builtins__": __builtins__}
    exec(code, namespace, namespace)
    return namespace


def extract_code_last(output: str) -> str:
    """Use the last python block (agent traces often include earlier snippets)."""
    matches = re.findall(r"```python\s*(.*?)```", output, re.DOTALL | re.IGNORECASE)
    if matches:
        return matches[-1].strip()
    match = re.search(r"```python\s*(.*?)```", output, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else output.strip()


def _result(score: float, note: str, correct: bool) -> Dict[str, Any]:
    return {"score": score, "note": note, "correct": correct}


def _count_tags(text: str, pattern: str) -> int:
    return len(re.findall(pattern, text, re.IGNORECASE))


def eval_agent_session_rubric(output: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Score long-horizon agent trace structure."""
    text = output.lower()
    min_steps = config.get("min_steps", 5)
    step_count = _count_tags(text, r"<step\d+>")
    if step_count == 0:
        step_count = _count_tags(text, r"(?:^|\n)\s*step\s*\d+[:.)]")

    checkpoint_count = _count_tags(text, r"<checkpoint\d*>")
    has_verify = any(k in text for k in config.get("verify_keywords", [
        "verify", "validation", "self-check", "self check", "test pass", "confirmed",
    ]))
    has_observe_plan_act = sum(
        k in text for k in ["observe", "plan", "act", "execute", "delegate"]
    ) >= 2
    has_deliverable = True
    if tag := config.get("deliverable_tag"):
        has_deliverable = f"<{tag.lower()}>" in text or f"</{tag.lower()}>" in text

    keyword_hits = 0
    for group in config.get("keyword_groups", []):
        # Flatten accidental double-nested groups
        if group and isinstance(group[0], list):
            group = group[0]
        if any(k.lower() in text for k in group):
            keyword_hits += 1
    min_kw = config.get("min_keyword_groups", 0)
    kw_ok = keyword_hits >= min_kw

    criteria = [
        step_count >= min_steps,
        checkpoint_count >= config.get("min_checkpoints", 1),
        has_verify,
        has_observe_plan_act,
        has_deliverable,
        kw_ok,
    ]
    passed = sum(criteria)
    total = len(criteria)

    if passed >= total - 1:
        return _result(
            100.0,
            f"Strong session ({step_count} steps, {checkpoint_count} ckpt, {passed}/{total})",
            True,
        )
    if passed >= max(3, total // 2):
        return _result(
            65.0,
            f"Partial session ({step_count} steps, {passed}/{total})",
            False,
        )
    return _result(30.0, f"Weak session ({step_count} steps, {passed}/{total})", False)


def eval_agent_code_session(output: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Agent rubric + executable code deliverable (last python block)."""
    rubric = eval_agent_session_rubric(output, config.get("rubric", {}))
    code = extract_code_last(output)
    try:
        namespace = _exec_code(code)
        exec(config["harness"], {"__builtins__": __builtins__}, namespace)
        code_ok = True
        code_note = "Code OK"
    except Exception as exc:
        code_ok = False
        code_note = f"Code fail ({type(exc).__name__})"

    if rubric["correct"] and code_ok:
        return _result(100.0, f"Full session + {code_note}", True)
    if rubric["score"] >= 65 and code_ok:
        return _result(85.0, f"Partial trace + {code_note}", True)
    if code_ok:
        return _result(70.0, f"Code only ({rubric['note']})", False)
    if rubric["score"] >= 65:
        return _result(50.0, f"Trace only ({code_note})", False)
    return _result(0.0, f"{rubric['note']}; {code_note}", False)


def eval_agent_delegation(output: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Parent agent delegating to numbered sub-agents."""
    text = output.lower()
    min_sub = config.get("min_subagents", 3)
    sub_count = _count_tags(text, r"<subagent\d+>")
    has_parent = any(k in text for k in ["orchestrat", "parent", "coordinator", "synthesis"])
    has_handoff = any(k in text for k in ["handoff", "delegate", "assign", "sub-agent", "subagent"])
    rubric = eval_agent_session_rubric(output, config.get("rubric", {}))

    if sub_count >= min_sub and has_parent and has_handoff and rubric["score"] >= 65:
        return _result(100.0, f"Delegation OK ({sub_count} subagents)", True)
    if sub_count >= min_sub - 1 and rubric["score"] >= 65:
        return _result(75.0, f"Partial delegation ({sub_count} subagents)", False)
    return _result(35.0, f"Weak delegation ({sub_count} subagents)", False)


EVALUATORS: Dict[str, Callable[[str, Dict[str, Any]], Dict[str, Any]]] = {
    "agent_session_rubric": eval_agent_session_rubric,
    "agent_code_session": eval_agent_code_session,
    "agent_delegation": eval_agent_delegation,
}


def evaluate_agent_session_task(output: str, task: Dict[str, Any]) -> Dict[str, Any]:
    evaluator = task.get("evaluator")
    if not evaluator or evaluator not in EVALUATORS:
        return _result(50.0, "Unknown agent evaluator", False)
    return EVALUATORS[evaluator](output, task.get("eval_config", {}))


def _t(
    task_id: int,
    name: str,
    category: str,
    prompt: str,
    evaluator: str,
    eval_config: Optional[Dict[str, Any]] = None,
    max_tokens: int = 8192,
) -> Dict[str, Any]:
    return {
        "id": task_id,
        "suite": "agent_sessions",
        "name": f"Agent: {name}",
        "category": category,
        "prompt": prompt.strip(),
        "evaluator": evaluator,
        "eval_config": eval_config or {},
        "max_tokens": max_tokens,
    }


# Reusable rubric presets
PLAN_KW = [
    ["rollback", "canary", "feature flag", "staged rollout"],
    ["observability", "monitor", "slo", "alert"],
    ["test", "verify", "validation", "smoke"],
    ["deliverable", "milestone", "checkpoint"],
]

AGENT_HEADER = """You are an autonomous long-horizon agent. Work through this WITHOUT asking the user questions.

For EACH step document: OBSERVE → PLAN → ACT → VERIFY inside <step1>...</step1>, <step2>...</step2>, etc.
Add at least one <checkpoint> summarizing progress and rollback state.
End with a clear deliverable section.
"""


def build_agent_session_tasks() -> List[Dict[str, Any]]:
    tasks: List[Dict[str, Any]] = []
    n = 0

    def add(name, category, body, evaluator, eval_config=None, max_tokens=8192):
        nonlocal n
        n += 1
        prompt = AGENT_HEADER + "\n" + body
        tasks.append(_t(n, name, category, prompt, evaluator, eval_config, max_tokens))

    # --- Multi-stage coding projects (6) ---
    add(
        "Session: REST Task API",
        "agent_coding",
        """Build a minimal in-memory Task API module over 6+ agent steps.

Final deliverable: Python code in a ```python block defining:
- class TaskAPI with create(title), get(id), list_all(), complete(id)
- IDs increment from 1; completed tasks track done=True

Include self-tests in your VERIFY steps before submitting code.""",
        "agent_code_session",
        {
            "rubric": {"min_steps": 5, "min_checkpoints": 1, "deliverable_tag": "deliverable"},
            "harness": """
api=TaskAPI()
t1=api.create('a'); t2=api.create('b')
assert api.get(t1)['title']=='a' and len(api.list_all())==2
api.complete(t1); assert api.get(t1)['done'] is True
""",
        },
    )
    add(
        "Session: ETL Pipeline",
        "agent_coding",
        """Design and implement a 3-stage ETL pipeline in 6+ steps:
extract (parse CSV string) → transform (normalize emails lower-case) → load (dedupe).

Deliverable: ```python with def run_etl(csv_text: str) -> list[dict]""",
        "agent_code_session",
        {
            "rubric": {"min_steps": 5, "keyword_groups": [["extract","transform","load"]]},
            "harness": """
csv='email\\nA@X.com\\na@x.com\\nb@y.com'
out=run_etl(csv)
assert len(out)==2 and out[0]['email']=='a@x.com'
""",
        },
    )
    add(
        "Session: CLI Stats Tool",
        "agent_coding",
        """Over 6+ steps build def compute_stats(numbers: list[float]) -> dict
returning count, mean, min, max (no imports required).

Verify with edge cases (empty list returns count=0, mean=0).""",
        "agent_code_session",
        {
            "rubric": {"min_steps": 5},
            "harness": """
s=compute_stats([1,2,3,4]); assert s['count']==4 and s['mean']==2.5
e=compute_stats([]); assert e['count']==0 and e['mean']==0
""",
        },
    )
    add(
        "Session: Job Queue",
        "agent_coding",
        """Implement class JobQueue with enqueue(fn), process_all() executing FIFO in 6+ steps.
process_all returns list of results in order.""",
        "agent_code_session",
        {
            "rubric": {"min_steps": 5},
            "harness": """
q=JobQueue(); q.enqueue(lambda: 1); q.enqueue(lambda: 2)
assert q.process_all()==[1,2]
""",
        },
    )
    add(
        "Session: Config Validator",
        "agent_coding",
        """Implement def validate_config(cfg: dict) -> list[str] returning error strings.
Rules: required keys host, port, debug; port int 1-65535; host non-empty string.
6+ agent steps with verification.""",
        "agent_code_session",
        {
            "rubric": {"min_steps": 5},
            "harness": """
assert not validate_config({'host':'h','port':80,'debug':False})
assert validate_config({'host':'','port':99,'debug':True})
""",
        },
    )
    add(
        "Session: Retry Decorator",
        "agent_coding",
        """Implement def retry(times: int) decorator retrying on Exception, 6+ steps.
Verify with a flaky function that succeeds on 3rd call.""",
        "agent_code_session",
        {
            "rubric": {"min_steps": 5},
            "harness": """
calls={'n':0}
@retry(3)
def f():
    calls['n']+=1
    if calls['n']<3: raise ValueError('no')
    return 'ok'
assert f()=='ok' and calls['n']==3
""",
        },
    )

    # --- Long-horizon planning sessions (8) ---
    plans = [
        ("Platform Migration 180d", "agent_planning", 7, "monolith", PLAN_KW),
        ("Production Incident Response", "agent_incident", 6, "outage", [
            ["rollback", "mitigation", "customer"],
            ["postmortem", "root cause", "timeline"],
            ["monitor", "alert", "observability"],
        ]),
        ("Greenfield Microservice", "agent_planning", 6, "microservice", PLAN_KW),
        ("Strangler Fig Legacy Cutover", "agent_planning", 6, "strangler", PLAN_KW),
        ("ML Productionization", "agent_planning", 7, "mlops", [
            ["training", "registry", "model"],
            ["monitor", "drift", "observability"],
            ["rollback", "canary"],
        ]),
        ("Database Sharding Rollout", "agent_planning", 6, "sharding", PLAN_KW),
        ("Security Hardening Program", "agent_planning", 6, "security", [
            ["threat", "vulnerability", "audit"],
            ["remediat", "patch", "fix"],
            ["verify", "test", "penetration"],
        ]),
        ("Cost Optimization Sprint", "agent_planning", 5, "finops", [
            ["rightsizing", "reserved", "spot"],
            ["observability", "cost", "dashboard"],
        ]),
    ]
    for title, cat, steps, tag, kw in plans:
        add(
            f"Session: {title}",
            cat,
            f"""Execute a long-horizon agent session ({steps}+ steps) to produce a complete plan.
Wrap deliverable in <{tag}>...</{tag}>.
Include risks, checkpoints, and verification strategy per step.""",
            "agent_session_rubric",
            {
                "min_steps": steps,
                "min_checkpoints": 2,
                "deliverable_tag": tag,
                "keyword_groups": kw,
                "min_keyword_groups": max(2, len(kw) - 1),
            },
        )

    # --- Research & deliverable documents (4) ---
    add(
        "Session: Architecture Decision Record",
        "agent_research",
        """6+ step agent session producing an ADR for event-driven architecture vs REST.
Deliverable in <adr> with: context, decision, consequences, alternatives, verification.""",
        "agent_session_rubric",
        {
            "min_steps": 6,
            "deliverable_tag": "adr",
            "keyword_groups": [
                ["event", "message", "queue", "kafka"],
                ["tradeoff", "alternative", "consequence"],
                ["verify", "pilot", "prototype"],
            ],
            "min_keyword_groups": 2,
        },
    )
    add(
        "Session: Incident Postmortem",
        "agent_research",
        """6+ steps: investigate hypothetical 45-min API outage (DB connection pool exhaustion).
Deliverable <postmortem>: timeline, root cause, 5-whys, action items, verification.""",
        "agent_session_rubric",
        {
            "min_steps": 6,
            "deliverable_tag": "postmortem",
            "keyword_groups": [
                ["timeline", "detection", "impact"],
                ["root cause", "5 whys", "five whys"],
                ["action item", "prevent", "verify"],
            ],
            "min_keyword_groups": 2,
        },
    )
    add(
        "Session: SRE Runbook",
        "agent_research",
        """6+ steps creating runbook for payment service latency spike.
Deliverable <runbook>: symptoms, diagnostics, mitigation, escalation, verification tests.""",
        "agent_session_rubric",
        {
            "min_steps": 6,
            "deliverable_tag": "runbook",
            "keyword_groups": [
                ["symptom", "alert", "slo"],
                ["diagnostic", "dashboard", "trace"],
                ["mitigation", "rollback", "escalat"],
            ],
            "min_keyword_groups": 2,
        },
    )
    add(
        "Session: Competitive → Roadmap",
        "agent_research",
        """7+ steps: analyze 3 fictional competitors, identify gaps, produce Q3 roadmap.
Deliverable <roadmap> with themes, milestones, metrics, verification.""",
        "agent_session_rubric",
        {
            "min_steps": 7,
            "deliverable_tag": "roadmap",
            "keyword_groups": [
                ["competitor", "market", "gap"],
                ["milestone", "theme", "priority"],
                ["metric", "success", "verify"],
            ],
            "min_keyword_groups": 2,
        },
    )

    # --- Sub-agent delegation (4) ---
    add(
        "Session: Delegate Research+Code+Test",
        "agent_delegation",
        """Parent agent orchestrates 3 sub-agents over 6+ steps:
<subagent1> research requirements, <subagent2> implement, <subagent3> test.
Synthesize in <synthesis>. Parent must not skip verification.""",
        "agent_delegation",
        {
            "min_subagents": 3,
            "rubric": {"min_steps": 6, "min_checkpoints": 1, "deliverable_tag": "synthesis"},
        },
    )
    add(
        "Session: Delegate Code Review Pipeline",
        "agent_delegation",
        """Orchestrate subagents for: static analysis, security review, performance review.
6+ steps with <subagent1..3> and final <review_report>.""",
        "agent_delegation",
        {
            "min_subagents": 3,
            "rubric": {"min_steps": 6, "deliverable_tag": "review_report"},
        },
    )
    add(
        "Session: Delegate Data Migration Workers",
        "agent_delegation",
        """Parent coordinates 3 parallel migration sub-agents (shards A/B/C).
6+ steps, checkpoints, verification per shard, <migration_report> deliverable.""",
        "agent_delegation",
        {
            "min_subagents": 3,
            "rubric": {
                "min_steps": 6,
                "min_checkpoints": 2,
                "deliverable_tag": "migration_report",
                "keyword_groups": [["shard", "parallel", "worker"]],
                "min_keyword_groups": 1,
            },
        },
    )
    add(
        "Session: Delegate Bug Triage",
        "agent_delegation",
        """Autonomous bug triage: reproduce, classify, assign, verify fix strategy.
Subagents in <subagent1..3>; deliverable <triage_report>. 5+ steps.""",
        "agent_delegation",
        {
            "min_subagents": 3,
            "rubric": {"min_steps": 5, "deliverable_tag": "triage_report"},
        },
    )

    # --- Autonomous multi-day style workflows (3) ---
    add(
        "Session: Week-Long Refactor Plan+Execute",
        "agent_autonomous",
        """Simulate a 5-day autonomous refactor of a 10k LOC Python monolith to packages.
8+ steps with daily <checkpoint> entries and <deliverable> listing modules moved, tests run, risks.""",
        "agent_session_rubric",
        {
            "min_steps": 8,
            "min_checkpoints": 3,
            "deliverable_tag": "deliverable",
            "keyword_groups": [
                ["day 1", "day 2", "daily", "checkpoint"],
                ["module", "package", "refactor"],
                ["test", "verify", "ci"],
            ],
            "min_keyword_groups": 2,
        },
    )
    add(
        "Session: Autonomous Test Coverage Sprint",
        "agent_autonomous",
        """8+ steps: agent audits coverage gaps, writes tests, runs them, reports.
Deliverable <coverage_report> with before/after %, tests added, verification log.""",
        "agent_session_rubric",
        {
            "min_steps": 8,
            "min_checkpoints": 2,
            "deliverable_tag": "coverage_report",
            "keyword_groups": [
                ["coverage", "pytest", "unittest"],
                ["gap", "audit", "missing"],
                ["verify", "pass", "fail"],
            ],
            "min_keyword_groups": 2,
        },
    )
    add(
        "Session: Autonomous Docs Generation",
        "agent_autonomous",
        """7+ steps: agent inventories public APIs, drafts docs, cross-checks examples.
Deliverable <documentation> with sections: overview, api, examples, verification checklist.""",
        "agent_session_rubric",
        {
            "min_steps": 7,
            "deliverable_tag": "documentation",
            "keyword_groups": [
                ["api", "endpoint", "function"],
                ["example", "snippet", "usage"],
                ["verify", "accuracy", "checklist"],
            ],
            "min_keyword_groups": 2,
        },
    )

    assert len(tasks) >= 25, f"Expected >=25 agent session tasks, got {len(tasks)}"
    return tasks