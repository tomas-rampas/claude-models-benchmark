"""
Fable 5 strength benchmark tasks (50+ prompts).

Domains aligned with Anthropic's Fable positioning:
complex coding, debugging, security, agentic planning, finance, refactoring.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional


def _exec_code(code: str) -> Dict[str, Any]:
    namespace: Dict[str, Any] = {"__builtins__": __builtins__}
    exec(code, namespace, namespace)
    return namespace


def extract_code(output: str) -> str:
    match = re.search(r"```python\s*(.*?)```", output, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else output.strip()


def _result(score: float, note: str, correct: bool) -> Dict[str, Any]:
    return {"score": score, "note": note, "correct": correct}


def _parse_answer_number(output: str, tag: str = "answer") -> Optional[float]:
    match = re.search(rf"<{tag}>\s*([-+]?\d*\.?\d+)\s*</{tag}>", output, re.I)
    if match:
        return float(match.group(1))
    nums = re.findall(r"[-+]?\d+\.?\d+", output)
    return float(nums[-1]) if nums else None


def eval_code_harness(output: str, config: Dict[str, Any]) -> Dict[str, Any]:
    code = extract_code(output)
    try:
        namespace = _exec_code(code)
        exec(config["harness"], {"__builtins__": __builtins__}, namespace)
        return _result(100.0, "All tests passed", True)
    except Exception as exc:
        return _result(0.0, f"Tests failed ({type(exc).__name__})", False)


def eval_answer_numeric(output: str, config: Dict[str, Any]) -> Dict[str, Any]:
    value = _parse_answer_number(output, config.get("tag", "answer"))
    if value is None:
        return _result(0.0, "No numeric answer found", False)
    expected = config["expected"]
    tol = config.get("tolerance", 0.75)
    ok = abs(value - expected) <= tol
    return _result(
        100.0 if ok else 0.0,
        f"{'Correct' if ok else 'Wrong'} (got {value:.4g}, expected {expected})",
        ok,
    )


def eval_answer_any(output: str, config: Dict[str, Any]) -> Dict[str, Any]:
    text = output.lower()
    answers = [a.lower() for a in config["answers"]]
    ok = any(a in text for a in answers)
    return _result(
        100.0 if ok else 0.0,
        "Correct answer found" if ok else f"Expected one of: {answers}",
        ok,
    )


def eval_agent_plan(output: str, config: Dict[str, Any]) -> Dict[str, Any]:
    text = output.lower()
    min_phases = config.get("min_phases", 5)
    phase_count = len(re.findall(r"<phase\d+>", text, re.I))
    if phase_count == 0:
        phase_count = len(re.findall(r"(?:^|\n)\s*(?:phase|stage)\s*\d+[:.)]", text, re.I))

    criteria: List[bool] = [phase_count >= min_phases]
    for group in config.get("keyword_groups", []):
        criteria.append(any(k.lower() in text for k in group))

    passed = sum(criteria)
    total = len(criteria)
    if passed >= total - 1:
        return _result(100.0, f"Strong plan ({phase_count} phases, {passed}/{total})", True)
    if passed >= max(2, total // 2):
        return _result(65.0, f"Partial plan ({phase_count} phases, {passed}/{total})", False)
    return _result(30.0, f"Weak plan ({phase_count} phases, {passed}/{total})", False)


def eval_security_sql(output: str, config: Dict[str, Any]) -> Dict[str, Any]:
    code = extract_code(output)
    fn_name = config.get("function", "get_user")
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

        namespace = _exec_code(code)
        fn = namespace.get(fn_name)
        if not callable(fn):
            return _result(0.0, f"Missing function {fn_name}", False)

        conn = FakeConn()
        if not fn(conn, 1):
            return _result(0.0, "Valid query failed", False)
        cur = conn.cursor()
        if cur.last_params is None or "{" in cur.last_query:
            return _result(0.0, "Not parameterized", False)
        fn(conn, "1 OR 1=1")
        cur = conn.cursor()
        if "OR1=1" in cur.last_query.upper().replace(" ", ""):
            return _result(0.0, "Injection still possible", False)
        return _result(100.0, "Safe parameterized query", True)
    except Exception:
        return _result(0.0, "Security tests failed", False)


def eval_forbidden_patterns(output: str, config: Dict[str, Any]) -> Dict[str, Any]:
    code = extract_code(output)
    lower = code.lower()
    for pattern in config.get("forbidden", []):
        if pattern.lower() in lower:
            return _result(0.0, f"Forbidden pattern: {pattern}", False)
    if config.get("required_any"):
        if not any(r.lower() in lower for r in config["required_any"]):
            return _result(0.0, "Missing required secure pattern", False)
    if config.get("harness"):
        return eval_code_harness(output, {"harness": config["harness"]})
    return _result(100.0, "Security checks passed", True)


EVALUATORS: Dict[str, Callable[[str, Dict[str, Any]], Dict[str, Any]]] = {
    "code_harness": eval_code_harness,
    "answer_numeric": eval_answer_numeric,
    "answer_any": eval_answer_any,
    "agent_plan": eval_agent_plan,
    "security_sql": eval_security_sql,
    "forbidden_patterns": eval_forbidden_patterns,
}


def evaluate_fable_task(output: str, task: Dict[str, Any]) -> Dict[str, Any]:
    evaluator = task.get("evaluator")
    if not evaluator or evaluator not in EVALUATORS:
        return _result(50.0, "Unknown evaluator", False)
    return EVALUATORS[evaluator](output, task.get("eval_config", {}))


def _t(
    task_id: int,
    name: str,
    category: str,
    prompt: str,
    evaluator: str,
    eval_config: Optional[Dict[str, Any]] = None,
    max_tokens: int = 2048,
) -> Dict[str, Any]:
    return {
        "id": task_id,
        "name": f"Fable: {name}",
        "category": category,
        "prompt": prompt.strip(),
        "evaluator": evaluator,
        "eval_config": eval_config or {},
        "max_tokens": max_tokens,
    }


def build_fable_strength_tasks() -> List[Dict[str, Any]]:
    """Return 50+ Fable-aligned benchmark prompts."""
    tasks: List[Dict[str, Any]] = []
    n = 0

    def add(name, category, prompt, evaluator, eval_config=None, max_tokens=2048):
        nonlocal n
        n += 1
        tasks.append(_t(n, name, category, prompt, evaluator, eval_config, max_tokens))

    # --- Complex coding (12) ---
    add(
        "LRU Cache",
        "complex_coding",
        """Implement production-quality class LRUCache with:
- __init__(capacity), get(key)->int, put(key,value)
- Return -1 for missing keys; O(1) average get/put
Return code in ```python block.""",
        "code_harness",
        {"harness": """
c=LRUCache(2); c.put(1,1); c.put(2,2); assert c.get(1)==1
c.put(3,3); assert c.get(2)==-1; c.put(4,4); assert c.get(1)==-1 and c.get(3)==3 and c.get(4)==4
c2=LRUCache(1); c2.put(2,1); c2.get(2); c2.put(3,2); assert c2.get(2)==-1 and c2.get(3)==2
"""},
        4096,
    )
    add(
        "MinStack",
        "complex_coding",
        """Implement class MinStack with push, pop, top, getMin — all O(1).
Return full class in ```python block.""",
        "code_harness",
        {"harness": """
s=MinStack(); s.push(3); s.push(1); s.push(2); assert s.getMin()==1
assert s.top()==2; s.pop(); assert s.top()==1 and s.getMin()==1
"""},
    )
    add(
        "Merge Intervals",
        "complex_coding",
        """Implement def merge_intervals(intervals: list[list[int]]) -> list[list[int]].
Merge overlapping intervals. Return code in ```python block.""",
        "code_harness",
        {"harness": """
assert merge_intervals([[1,3],[2,6],[8,10],[15,18]])==[[1,6],[8,10],[15,18]]
assert merge_intervals([[1,4],[4,5]])==[[1,5]]
"""},
    )
    add(
        "Coin Change",
        "complex_coding",
        """Implement def coin_change(coins: list[int], amount: int) -> int.
Return minimum coins to make amount, or -1 if impossible.
Return code in ```python block.""",
        "code_harness",
        {"harness": """
assert coin_change([1,2,5],11)==3
assert coin_change([2],3)==-1
assert coin_change([1],0)==0
"""},
    )
    add(
        "Valid Parentheses",
        "complex_coding",
        """Implement def is_valid_parentheses(s: str) -> bool for brackets ()[]{}.
Return code in ```python block.""",
        "code_harness",
        {"harness": """
assert is_valid_parentheses("()[]{}") is True
assert is_valid_parentheses("(]") is False
assert is_valid_parentheses("{[]}") is True
assert is_valid_parentheses("") is True
"""},
    )
    add(
        "Max Subarray Sum",
        "complex_coding",
        """Implement def max_subarray(nums: list[int]) -> int (Kadane's algorithm).
Return code in ```python block.""",
        "code_harness",
        {"harness": """
assert max_subarray([-2,1,-3,4,-1,2,1,-5,4])==6
assert max_subarray([1])==1
assert max_subarray([-1])==-1
"""},
    )
    add(
        "Group Anagrams",
        "complex_coding",
        """Implement def group_anagrams(words: list[str]) -> list[list[str]].
Order within groups does not matter. Return code in ```python block.""",
        "code_harness",
        {"harness": """
res=group_anagrams(["eat","tea","tan","ate","nat","bat"])
sorted_groups=sorted(sorted(g) for g in res)
assert sorted_groups==[['ate','eat','tea'],['bat'],['nat','tan']]
"""},
    )
    add(
        "Product Except Self",
        "complex_coding",
        """Implement def product_except_self(nums: list[int]) -> list[int] without division.
Return code in ```python block.""",
        "code_harness",
        {"harness": """
assert product_except_self([1,2,3,4])==[24,12,8,6]
assert product_except_self([0,1])==[1,0]
"""},
    )
    add(
        "Simplify Unix Path",
        "complex_coding",
        """Implement def simplify_path(path: str) -> str for Unix-style paths.
Return code in ```python block.""",
        "code_harness",
        {"harness": """
assert simplify_path("/home/")=='/home'
assert simplify_path("/../")=="/"
assert simplify_path("/home//foo/")=="/home/foo"
assert simplify_path("/a/./b/../../c/")=='/c'
"""},
    )
    add(
        "Two Sum",
        "complex_coding",
        """Implement def two_sum(nums: list[int], target: int) -> list[int] (indices).
Exactly one solution exists. Return code in ```python block.""",
        "code_harness",
        {"harness": """
assert two_sum([2,7,11,15],9)==[0,1]
assert two_sum([3,3],6)==[0,1]
"""},
    )
    add(
        "Level Order Traversal",
        "complex_coding",
        """Implement TreeNode and def level_order(root) -> list[list[int]] BFS.
Return code in ```python block.""",
        "code_harness",
        {"harness": """
root=TreeNode(3,TreeNode(9),TreeNode(20,TreeNode(15),TreeNode(7)))
assert level_order(root)==[[3],[9,20],[15,7]]
assert level_order(None)==[]
"""},
    )
    add(
        "Rate Limiter",
        "complex_coding",
        """Implement class RateLimiter(max_calls, period_seconds) with allow(key)->bool.
Sliding window not required; fixed window OK. Return ```python block.""",
        "code_harness",
        {"harness": """
rl=RateLimiter(2,60)
assert rl.allow('a') and rl.allow('a') and not rl.allow('a')
assert rl.allow('b')
"""},
    )

    # --- Debugging (12) ---
    bug = lambda fn, body: f"Fix the bug. Return corrected code in ```python block.\n\n```python\n{body}\n```"
    add(
        "Bug Fix: Binary Search",
        "debugging",
        bug(
            "binary_search",
            '''def binary_search(nums, target):
    left, right = 0, len(nums)
    while left < right:
        mid = (left + right) // 2
        if nums[mid] == target:
            return mid
        elif nums[mid] < target:
            left = mid + 1
        else:
            right = mid
    return -1''',
        ),
        "code_harness",
        {"harness": """
assert binary_search([1,2,3,4,5],1)==0
assert binary_search([1,2,3,4,5],5)==4
assert binary_search([1,2,3,4,5],6)==-1
assert binary_search([],1)==-1
"""},
    )
    add(
        "Bug Fix: Sum 1..N",
        "debugging",
        bug(
            "sum_to_n",
            '''def sum_to_n(n: int) -> int:
    total = 0
    for i in range(1, n):
        total += i
    return total''',
        ),
        "code_harness",
        {"harness": "assert sum_to_n(5)==15 and sum_to_n(1)==1 and sum_to_n(0)==0"},
    )
    add(
        "Bug Fix: Fibonacci",
        "debugging",
        bug(
            "fib",
            '''def fib(n: int) -> int:
    if n <= 1:
        return 0
    return fib(n-1) + fib(n-2)''',
        ),
        "code_harness",
        {"harness": "assert fib(0)==0 and fib(1)==1 and fib(10)==55"},
    )
    add(
        "Bug Fix: Reverse String In-Place",
        "debugging",
        bug(
            "reverse_string",
            '''def reverse_string(s: list[str]) -> None:
    left, right = 0, len(s)-1
    while left < right:
        s[left], s[right] = s[right], s[left]
        left += 1
        right -= 1
    return s''',
        ),
        "code_harness",
        {"harness": """
a=['h','e','l','l','o']; reverse_string(a); assert a==['o','l','l','e','h']
b=['H','a','n','n','a','h']; reverse_string(b); assert b==['h','a','n','n','a','H']
"""},
    )
    add(
        "Bug Fix: Is Prime",
        "debugging",
        bug(
            "is_prime",
            '''def is_prime(n: int) -> bool:
    if n < 2:
        return False
    for i in range(2, n):
        if n % i == 0:
            return False
    return True''',
        ),
        "code_harness",
        {"harness": "assert is_prime(2) and is_prime(97) and not is_prime(1) and not is_prime(100)"},
    )
    add(
        "Bug Fix: Flatten List",
        "debugging",
        bug(
            "flatten",
            '''def flatten(nested):
    out = []
    for item in nested:
        if isinstance(item, list):
            out.extend(item)
        else:
            out.append(item)
    return out''',
        ),
        "code_harness",
        {"harness": "assert flatten([1,[2,[3,4]],5])==[1,2,3,4,5]"},
    )
    add(
        "Bug Fix: Word Count",
        "debugging",
        bug(
            "count_words",
            '''def count_words(text: str) -> int:
    words = text.split(' ')
    return len(words)''',
        ),
        "code_harness",
        {"harness": "assert count_words('hello world')==2 and count_words('  a  b  ')==2"},
    )
    add(
        "Bug Fix: GCD",
        "debugging",
        bug(
            "gcd",
            '''def gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return b''',
        ),
        "code_harness",
        {"harness": "assert gcd(48,18)==6 and gcd(17,13)==1"},
    )
    add(
        "Bug Fix: Binary Tree Height",
        "debugging",
        bug(
            "tree_height",
            '''class Node:
    def __init__(self, v, left=None, right=None):
        self.v=v; self.left=left; self.right=right

def tree_height(root):
    if not root:
        return 0
    return 1 + max(tree_height(root.left), tree_height(root.right))''',
        ),
        "code_harness",
        {"harness": """
n=Node(1,Node(2),Node(3,Node(4)))
assert tree_height(n)==3
assert tree_height(None)==0
"""},
    )
    add(
        "Bug Fix: Parse Int",
        "debugging",
        bug(
            "parse_int",
            '''def parse_int(s: str) -> int:
    sign = -1 if s[0]=='-' else 1
    if s[0] in '+-':
        s = s[1:]
    return sign * int(s)''',
        ),
        "code_harness",
        {"harness": "assert parse_int('-42')==-42 and parse_int('+7')==7 and parse_int('0')==0"},
    )
    add(
        "Bug Fix: Remove Duplicates Sorted",
        "debugging",
        bug(
            "remove_duplicates",
            '''def remove_duplicates(nums: list[int]) -> int:
    if not nums:
        return 0
    k = 1
    for i in range(1, len(nums)):
        if nums[i] != nums[i-1]:
            nums[k] = nums[i]
            k += 1
    return k - 1''',
        ),
        "code_harness",
        {"harness": """
a=[1,1,2,2,3]; k=remove_duplicates(a); assert k==3 and a[:k]==[1,2,3]
"""},
    )
    add(
        "Bug Fix: Balanced Brackets Depth",
        "debugging",
        bug(
            "max_depth",
            '''def max_depth(s: str) -> int:
    depth = cur = 0
    for ch in s:
        if ch == '(':
            cur += 1
            depth = max(depth, cur)
        elif ch == ')':
            cur -= 1
    return depth''',
        ),
        "code_harness",
        {"harness": "assert max_depth('(())()')==2 and max_depth('()')==1"},
    )

    # --- Security coding (8) ---
    add(
        "Security: SQL Injection",
        "security_coding",
        """Fix SQL injection. Return secure ```python code.

```python
def get_user(conn, user_id):
    cursor = conn.cursor()
    query = f"SELECT id, name FROM users WHERE id = {user_id}"
    cursor.execute(query)
    return cursor.fetchone()
```""",
        "security_sql",
        {"function": "get_user"},
    )
    add(
        "Security: Shell Injection",
        "security_coding",
        """Fix command injection. Return secure ```python code.

```python
import subprocess
def list_dir(path: str) -> str:
    return subprocess.check_output(f"ls {path}", shell=True).decode()
```""",
        "forbidden_patterns",
        {"forbidden": ["shell=true"], "required_any": ["shell=False", "shlex"]},
    )
    add(
        "Security: Path Traversal",
        "security_coding",
        """Fix path traversal in safe_read. Return ```python code.

```python
def safe_read(base_dir: str, user_path: str) -> str:
    full = base_dir + "/" + user_path
    with open(full) as f:
        return f.read()
```""",
        "forbidden_patterns",
        {
            "required_any": ["os.path", "pathlib", "realpath", "resolve"],
            "harness": """
import os, tempfile
base=tempfile.mkdtemp()
open(os.path.join(base,'ok.txt'),'w').write('hi')
assert safe_read(base,'ok.txt')=='hi'
try:
    safe_read(base,'../etc/passwd')
    raise AssertionError('traversal allowed')
except Exception:
    pass
""",
        },
    )
    add(
        "Security: XSS Escape",
        "security_coding",
        """Implement def escape_html(text: str) -> str escaping <>&\"'.
Return ```python block.""",
        "code_harness",
        {"harness": """
assert escape_html('<script>"&')=='&lt;script&gt;&quot;&amp;'
assert escape_html('safe')=='safe'
"""},
    )
    add(
        "Security: Pickle to JSON",
        "security_coding",
        """Rewrite load_data to use json.loads instead of pickle.loads.
Return ```python with def load_data(raw: str): ...

```python
import pickle
def load_data(raw: bytes):
    return pickle.loads(raw)
```""",
        "forbidden_patterns",
        {"forbidden": ["pickle"], "required_any": ["json"]},
    )
    add(
        "Security: Constant-Time Compare",
        "security_coding",
        """Implement def secure_compare(a: str, b: str) -> bool using hmac.compare_digest or secrets.compare_digest.
Return ```python block.""",
        "code_harness",
        {"harness": """
assert secure_compare('abc','abc') is True
assert secure_compare('abc','abd') is False
"""},
    )
    add(
        "Security: Sanitize Filename",
        "security_coding",
        """Implement def sanitize_filename(name: str) -> str removing path separators and parent refs.
Return ```python block.""",
        "code_harness",
        {"harness": """
assert '/' not in sanitize_filename('../../etc/passwd')
assert '..' not in sanitize_filename('../x')
assert sanitize_filename('report.pdf')=='report.pdf'
"""},
    )
    add(
        "Security: Safe Redirect",
        "security_coding",
        """Implement def is_safe_redirect(url: str, allowed_hosts: set[str]) -> bool blocking open redirects.
Return ```python block.""",
        "code_harness",
        {"harness": """
allowed={'app.example.com'}
assert is_safe_redirect('https://app.example.com/path', allowed)
assert not is_safe_redirect('https://evil.com', allowed)
assert not is_safe_redirect('//evil.com', allowed)
"""},
    )

    # --- Agent planning (10) ---
    plan_kw = [
        ["database", "data migration", "schema"],
        ["rollback", "canary", "feature flag", "staged"],
        ["test", "monitor", "observability", "slo"],
        ["deliverable", "milestone", "checkpoint"],
    ]
    plans = [
        (
            "Agent Plan: Monolith Migration",
            "Plan 6-month migration of 2M-user e-commerce monolith to microservices. "
            "Use <phase1>..</phase1> through <phase5>..</phase5> with goals, risks, rollback.",
        ),
        (
            "Agent Plan: ML Platform Rollout",
            "Plan enterprise ML platform rollout for 50 data scientists. "
            "5 phases in <phase1>..<phase5> tags. Include model registry, CI/CD, monitoring.",
        ),
        (
            "Agent Plan: Multi-Region DR",
            "Plan active-passive DR across us-east and eu-west for payment API (99.99% SLA). "
            "5 phased tags with failover testing and RPO/RTO targets.",
        ),
        (
            "Agent Plan: SOC2 Type II",
            "Plan 12-month SOC2 Type II readiness for B2B SaaS. "
            "5 phases with controls, evidence collection, audit milestones.",
        ),
        (
            "Agent Plan: Data Warehouse Modernization",
            "Plan Snowflake migration from legacy Oracle warehouse (15TB). "
            "5 phases: assessment, pipeline rewrite, validation, cutover, decommission.",
        ),
        (
            "Agent Plan: Zero-Downtime Deploy",
            "Plan zero-downtime Kubernetes deployment system for 200 microservices. "
            "5 phases with canary, observability, rollback automation.",
        ),
        (
            "Agent Plan: Legacy Java Upgrade",
            "Plan Java 8 to 21 migration for 3M LOC banking monolith. "
            "5 phases with dependency audit, module boundaries, regression strategy.",
        ),
        (
            "Agent Plan: Observability Stack",
            "Plan company-wide OpenTelemetry rollout across 400 services. "
            "5 phases with sampling, dashboards, SLOs, on-call runbooks.",
        ),
        (
            "Agent Plan: Multi-Tenant Isolation",
            "Plan tenant isolation redesign for multi-tenant CRM (10k tenants). "
            "5 phases covering data partitioning, authZ, noisy-neighbor controls.",
        ),
        (
            "Agent Plan: API Gateway Replacement",
            "Plan replacement of aging API gateway with Envoy-based mesh. "
            "5 phases with traffic shadowing, auth migration, perf benchmarks.",
        ),
    ]
    for title, prompt in plans:
        add(title, "agent_planning", prompt, "agent_plan", {"min_phases": 5, "keyword_groups": plan_kw}, 4096)

    # --- Finance analysis (10) ---
    finance = [
        ("NPV Project", "Cash flows Y0:-100 Y1:30 Y2:40 Y3:50 Y4:60 at 10%.", 38.88),
        ("ROI Calculation", "Invest $50k, return $68k after 1 year. ROI %?", 36.0),
        ("Gross Margin", "Revenue $500k, COGS $325k. Gross margin %?", 35.0),
        ("Break-Even Units", "Fixed $120k, price $40, variable $25. Break-even units?", 8000.0),
        ("CAGR", "Revenue grew $2M to $3.5M over 4 years. CAGR %?", 15.1),
        ("Simple Interest", "Principal $10k, 6% annual, 3 years simple interest total?", 1800.0),
        ("Payback Period", "Cost $250k, annual cash inflow $100k. Payback years?", 2.5),
        ("Current Ratio", "Assets $400k, liabilities $250k. Current ratio?", 1.6),
        ("Inventory Turnover", "COGS $900k, avg inventory $150k. Turnover ratio?", 6.0),
        ("Operating Margin", "Revenue $1.2M, operating income $180k. Operating margin %?", 15.0),
    ]
    for title, desc, expected in finance:
        add(
            f"Finance: {title}",
            "finance_analysis",
            f"""Senior finance analyst task: {desc}
Show work step-by-step. Final answer in <answer>NUMBER</answer> (percent without % sign).""",
            "answer_numeric",
            {"expected": expected, "tolerance": 1.2},
        )

    # --- Refactoring / data reasoning (8) ---
    add(
        "Refactor: Extract Validation",
        "refactoring",
        """Refactor by extracting validate_email helper without changing behavior.
Return ```python block.

```python
def register(user):
    if '@' not in user['email'] or '.' not in user['email']:
        raise ValueError('bad email')
    if len(user['password']) < 8:
        raise ValueError('bad password')
    return {'ok': True, 'email': user['email']}
```""",
        "code_harness",
        {"harness": """
assert register({'email':'a@b.co','password':'12345678'})['ok']
try:
    register({'email':'bad','password':'12345678'})
    raise AssertionError
except ValueError: pass
assert 'validate_email' in globals()
"""},
    )
    add(
        "Refactor: Remove Global Counter",
        "refactoring",
        """Refactor to class-based Counter without global state. Return ```python.

```python
count = 0
def inc():
    global count
    count += 1
    return count
```""",
        "code_harness",
        {"harness": """
c1=Counter(); c2=Counter()
assert c1.inc()==1 and c1.inc()==2 and c2.inc()==1
"""},
    )
    add(
        "Data: CSV Aggregate",
        "data_reasoning",
        """Implement def total_spend(rows: list[dict]) -> float where each row has 'amount'.
Return ```python block.""",
        "code_harness",
        {"harness": """
rows=[{'amount':'10.5'},{'amount':20},{'amount':'0.5'}]
assert abs(total_spend(rows)-31.0)<0.01
"""},
    )
    add(
        "Data: Deduplicate Stable",
        "data_reasoning",
        """Implement def dedupe_stable(items: list[str]) -> list[str] keeping first occurrence order.
Return ```python block.""",
        "code_harness",
        {"harness": "assert dedupe_stable(['a','b','a','c','b'])==['a','b','c']"},
    )
    add(
        "Data: Parse Key-Value Log",
        "data_reasoning",
        """Implement def parse_kv_line(line: str) -> dict for strings like 'a=1;b=2'.
Return ```python block.""",
        "code_harness",
        {"harness": "assert parse_kv_line('a=1;b=2;c=hello')=={'a':'1','b':'2','c':'hello'}"},
    )
    add(
        "Data: Rolling Average",
        "data_reasoning",
        """Implement def rolling_avg(values: list[float], window: int) -> list[float].
Return ```python block.""",
        "code_harness",
        {"harness": "assert rolling_avg([1,2,3,4],2)==[1.5,2.5,3.5]"},
    )
    add(
        "Refactor: Simplify Nested Ifs",
        "refactoring",
        """Rewrite is_adult_eligible preserving logic but without nested ifs >2 deep.
Return ```python block with same signature.

```python
def is_adult_eligible(age, has_consent, region):
    if region == 'US':
        if age >= 18:
            if has_consent:
                return True
            else:
                return False
        else:
            return False
    else:
        return age >= 21
```""",
        "code_harness",
        {"harness": """
assert is_adult_eligible(18,True,'US')
assert not is_adult_eligible(17,True,'US')
assert is_adult_eligible(21,False,'EU')
"""},
    )
    add(
        "Legal: Clause Checklist",
        "enterprise_reasoning",
        """Review SaaS MSA clause: unlimited liability, auto-renewal, unilateral price changes.
Produce remediation checklist with 5 items in <item1>..</item5> tags covering liability cap, notice, opt-out, audit, and SLA credits.""",
        "answer_any",
        {"answers": ["<item1>", "liability cap", "auto-renewal", "sla credit"]},
    )

    assert len(tasks) >= 50, f"Expected >=50 tasks, got {len(tasks)}"
    return tasks