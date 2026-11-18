"""All DB reads/writes live here so the API and eval stay thin."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import BreakResult
from app.models import GeneratedTest, Problem, Run, RunResult, Solution, sha

MAX_TEXT = 20_000  # keep rows small; large inputs are truncated for storage


def _cut(s: str | None) -> str | None:
    return None if s is None else s[:MAX_TEXT]


def save_run(db: Session, problem: str, code: str, language: str, strategy: str, r: BreakResult) -> Run:
    p = db.scalar(select(Problem).where(Problem.text_hash == sha(problem)))
    if p is None:
        p = Problem(text_hash=sha(problem), statement=problem)
        db.add(p)
        db.flush()
    s = db.scalar(select(Solution).where(Solution.problem_id == p.id, Solution.code_hash == sha(code),
                                         Solution.language == language))
    if s is None:
        s = Solution(problem_id=p.id, language=language, code_hash=sha(code), code=code)
        db.add(s)
        db.flush()

    run = Run(
        solution_id=s.id, strategy=strategy, verdict=r.verdict, runtime_s=r.runtime_s,
        total_tests=r.total_tests, invalid_tests=r.invalid_tests, oracle_rebuilds=r.oracle_rebuilds,
        failing_input=_cut(r.failing.input) if r.failing else None,
        minimized_input=_cut(r.minimized.input) if r.minimized else None,
        expected_output=_cut(r.minimized.expected) if r.minimized else None,
        actual_output=_cut(r.minimized.actual) if r.minimized else None,
        failure_verdict=r.failing.verdict if r.failing else None,
        steps=r.steps, reference_notes=r.reference_notes, error=r.error[:2000],
    )
    db.add(run)
    db.flush()
    for t in r.all_tests:
        db.add(GeneratedTest(run_id=run.id, input=_cut(t.input), category=t.category.value, reasoning=t.reasoning))
    for x in r.all_results:
        db.add(RunResult(run_id=run.id, input=_cut(x.input), category=x.category, verdict=x.verdict,
                         expected=_cut(x.expected) or "", actual=_cut(x.actual) or "",
                         sol_runtime_ms=x.sol_runtime_ms))
    db.commit()
    return run


def run_to_dict(run: Run, detail: bool = False) -> dict:
    d = {
        "id": run.id, "verdict": run.verdict, "strategy": run.strategy, "language": run.solution.language,
        "runtime_s": round(run.runtime_s, 2), "total_tests": run.total_tests, "invalid_tests": run.invalid_tests,
        "minimized_input": run.minimized_input, "expected_output": run.expected_output,
        "actual_output": run.actual_output, "failure_verdict": run.failure_verdict,
        "created_at": run.created_at.isoformat(), "error": run.error,
    }
    if detail:
        d["failing_input"] = run.failing_input
        d["steps"] = run.steps
        d["reference_notes"] = run.reference_notes
        d["oracle_rebuilds"] = run.oracle_rebuilds
        d["problem"] = run.solution.problem.statement
        d["code"] = run.solution.code
    return d