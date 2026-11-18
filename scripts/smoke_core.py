from app.sandbox import Program
from app.tools import make_oracle, generate_tests, run_in_sandbox, shrink_input

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

oracle = make_oracle(PROBLEM)
print("reference:", oracle.ref_notes)
print("validator:", oracle.validator_notes)
tests = generate_tests(PROBLEM, "boundary", n=20)
print(f"generated {len(tests)} tests\n")

for name, code in [("BUGGY", BUGGY), ("CORRECT", CORRECT)]:
    sol = Program(code, "python")
    results = run_in_sandbox(oracle, sol, tests, stop_on_fail=(name == "BUGGY"))
    invalid = [r for r in results if r.verdict == "invalid_test"]
    fails = [r for r in results if r.failed]
    print(f"== {name}: ran {len(results)}, invalid {len(invalid)}, failures {len(fails)}")
    for r in invalid[:3]:
        print(f"   invalid example: {r.input[:60]!r} -> {r.stderr[:80]}")
    if fails:
        m = shrink_input(oracle, sol, fails[0].input)
        print(f"   original failing input: {fails[0].input!r}")
        print(f"   minimized: {m.input!r}  verdict={m.verdict}  expected={m.expected.strip()!r} actual={m.actual.strip()!r}")
    sol.cleanup()
oracle.cleanup()