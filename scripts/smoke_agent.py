import json
from app.agent import run_break

PROBLEM = """Maximum Subarray Sum.
Input: first line n (1 <= n <= 1000), second line n integers a_i (|a_i| <= 10^9).
Output: the maximum sum over all non-empty contiguous subarrays."""

BUGGY = """n = int(input())
a = list(map(int, input().split()))
best = 0
cur = 0
for x in a:
    cur = max(0, cur + x)
    best = max(best, cur)
print(best)
"""

CORRECT = """n = int(input())
a = list(map(int, input().split()))
best = a[0]
cur = a[0]
for x in a[1:]:
    cur = max(x, cur + x)
    best = max(best, cur)
print(best)
"""

for name, code in [("BUGGY", BUGGY), ("CORRECT", CORRECT)]:
    r = run_break(PROBLEM, code, "python", strategy="boundary", n_tests=15)
    print(f"\n===== {name}: {r.verdict} in {r.runtime_s:.1f}s | tests run {r.total_tests}, invalid {r.invalid_tests}")
    for s in r.steps:
        print("  step", s["step"], s["action"], json.dumps(s.get("args", {})), "->", json.dumps(s.get("result", s.get("note")))[:160])
    if r.minimized:
        m = r.minimized
        print(f"  MINIMAL INPUT: {m.input!r}\n  expected: {m.expected.strip()!r}  actual: {m.actual.strip()!r}")
    if r.error:
        print("  ERROR:", r.error)