from sqlalchemy import select, func

from app.agent import run_break
from app.db import SessionLocal, init_db
from app.models import Run, RunResult, GeneratedTest
from app.repo import save_run

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

init_db()
r = run_break(PROBLEM, BUGGY, "python", strategy="boundary", n_tests=15)
with SessionLocal() as db:
    run = save_run(db, PROBLEM, BUGGY, "python", "boundary", r)
    print("saved run id", run.id, run.verdict, repr(run.minimized_input))
    print("runs:", db.scalar(select(func.count(Run.id))),
          "| tests:", db.scalar(select(func.count(GeneratedTest.id))),
          "| results:", db.scalar(select(func.count(RunResult.id))))