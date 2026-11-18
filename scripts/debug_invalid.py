from pathlib import Path

from app.tools import make_oracle, generate_tests, VAL_TIMEOUT, REF_TIMEOUT

PROBLEM = """Maximum Subarray Sum.
Input: first line n (1 <= n <= 1000), second line n integers a_i (|a_i| <= 10^9).
Output: the maximum sum over all non-empty contiguous subarrays."""

oracle = make_oracle(PROBLEM)

print("=== VALIDATOR SOURCE ===")
print((Path(oracle.validator.dir) / "sol.py").read_text(encoding="utf-8"))
print("=== REFERENCE SOURCE ===")
print((Path(oracle.ref.dir) / "sol.py").read_text(encoding="utf-8"))

print("=== SANITY: known-valid inputs ===")
for inp in ["1\n-1\n", "3\n1 2 3\n", "1\n-1", "2\n-5 7\n"]:
    v = oracle.validator.run(inp, timeout=VAL_TIMEOUT)
    r = oracle.ref.run(inp, timeout=REF_TIMEOUT)
    print(f"{inp!r:14} validator={v.status:14} ref={r.status:14} ref_out={r.stdout.strip()!r}")

print("\n=== GENERATED TESTS ===")
tests = generate_tests(PROBLEM, "boundary", n=15)
print(f"{len(tests)} tests after materializing generators")
for t in tests:
    v = oracle.validator.run(t.input, timeout=VAL_TIMEOUT)
    if v.status != "ok":
        reason = f"VALIDATOR {v.status}"
    else:
        r = oracle.ref.run(t.input, timeout=REF_TIMEOUT)
        reason = "ok" if (r.status == "ok" and r.stdout.strip()) else f"REF {r.status} ({r.runtime_ms:.0f}ms) {r.stderr[-80:]}"
    print(f"{len(t.input):7} chars | {t.category.value:15} | {reason} | {t.input[:50]!r}")

oracle.cleanup()