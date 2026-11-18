"""Tool-using agent loop. The LLM chooses WHICH tool to call; only real execution decides the verdict."""
import time
from dataclasses import dataclass, field

from google.genai import types

from app.harness import needs_wrap, wrap, language_mismatch
from app.llm import _cfg, _log, _call
from app.normalize import get_examples, normalize_problem
from app.sandbox import Program
from app.schemas import TestCase
from app.tools import (Oracle, CaseResult, make_oracle, generate_tests,
                       run_in_sandbox, shrink_input)

MAX_STEPS = 6

AGENT_SYSTEM = (
    "You are Breaker, an agent that tries to find an input where a contestant's solution gives a wrong answer. "
    "You have tools: generate_tests(focus), run_in_sandbox(), shrink_input(). "
    "Workflow: call generate_tests, then run_in_sandbox to execute the newest tests. "
    "If run_in_sandbox reports a failure, call shrink_input and then stop. "
    "If no failure was found, call generate_tests again with a specific `focus` describing a suspected weakness "
    "(use the categories and runtimes you saw), then run_in_sandbox again. "
    "Focus text must never ask for values outside the problem's stated constraints. "
    "You never decide whether the solution is wrong; the tools report real execution results. "
    "Reply with a short plain-text summary when done."
)

TOOL_DECLS = types.Tool(function_declarations=[
    types.FunctionDeclaration(
        name="generate_tests",
        description="Ask for a new batch of adversarial test inputs. Optionally steer with a focus.",
        parameters=types.Schema(
            type="OBJECT",
            properties={"focus": types.Schema(type="STRING", description="Suspected weakness to target, e.g. 'all equal elements'")},
        ),
    ),
    types.FunctionDeclaration(
        name="run_in_sandbox",
        description="Execute the user's solution and the reference on the most recently generated tests.",
        parameters=types.Schema(type="OBJECT", properties={}),
    ),
    types.FunctionDeclaration(
        name="shrink_input",
        description="Minimize the first failing input found. Only call after run_in_sandbox reports a failure.",
        parameters=types.Schema(type="OBJECT", properties={}),
    ),
])


@dataclass
class BreakResult:
    verdict: str                         # "BROKEN" | "NO_BUG_FOUND" | "INCONCLUSIVE" | "ERROR"
    failing: CaseResult | None = None
    minimized: CaseResult | None = None
    steps: list[dict] = field(default_factory=list)
    all_tests: list[TestCase] = field(default_factory=list)
    all_results: list[CaseResult] = field(default_factory=list)
    reference_notes: str = ""
    validator_notes: str = ""
    total_tests: int = 0
    invalid_tests: int = 0
    runtime_s: float = 0.0
    oracle_rebuilds: int = 0
    error: str = ""
    reason: str = ""                     # why the verdict is INCONCLUSIVE (human-readable)
    oracle_confidence: str = ""          # verified | unverified | failed
    oracle_attempts: int = 0
    samples_passed: int = 0
    samples_total: int = 0

    def to_dict(self):
        return {
            "verdict": self.verdict,
            "failing": self.failing.to_dict() if self.failing else None,
            "minimized": self.minimized.to_dict() if self.minimized else None,
            "steps": self.steps,
            "total_tests": self.total_tests,
            "invalid_tests": self.invalid_tests,
            "runtime_s": round(self.runtime_s, 2),
            "oracle_rebuilds": self.oracle_rebuilds,
            "reference_notes": self.reference_notes,
            "validator_notes": self.validator_notes,
            "error": self.error,
            "reason": self.reason,
            "oracle_confidence": self.oracle_confidence,
            "oracle_attempts": self.oracle_attempts,
            "samples_passed": self.samples_passed,
            "samples_total": self.samples_total,
        }


class _StepLog(list):
    """List that notifies a callback on every append, so the API can stream steps while the job runs."""
    def __init__(self, cb):
        super().__init__()
        self._cb = cb

    def append(self, item):
        super().append(item)
        self._cb(item)


def _record_oracle(out: "BreakResult", oracle: Oracle) -> None:
    out.oracle_confidence, out.oracle_attempts = oracle.confidence, oracle.attempts
    out.samples_passed, out.samples_total = oracle.samples_passed, oracle.samples_total
    out.steps.append({"step": 0, "action": "validate_reference", "result": {
        "confidence": oracle.confidence, "samples_passed": oracle.samples_passed,
        "samples_total": oracle.samples_total, "attempts": oracle.attempts, "note": oracle.check_note}})


def run_break(problem: str, solution_code: str, language: str,
              strategy: str = "generic", n_tests: int = 25,
              oracle: Oracle | None = None, on_step=None, on_phase=None) -> BreakResult:
    t0 = time.perf_counter()
    out = BreakResult(verdict="NO_BUG_FOUND")
    if on_step:
        out.steps = _StepLog(on_step)
    phase = on_phase or (lambda _p: None)
    owned: list[Oracle] = []          # oracles created here (cleaned up at the end)
    examples: list = []
    mismatch = language_mismatch(solution_code, language)
    if mismatch:
        out.verdict, out.error = "ERROR", mismatch
        out.runtime_s = time.perf_counter() - t0
        return out

    # Step 0a: raw statement -> canonical stdin/stdout spec (everything below uses the spec)
    raw_problem = problem
    phase("Normalizing problem statement")
    problem, spec_note = normalize_problem(raw_problem, solution_code, language)
    out.steps.append({"step": 0, "action": "normalize_problem", "result": spec_note, "spec": problem[:1500]})
    examples = get_examples(raw_problem)

    # Step 0b: LeetCode-style code -> runnable program
    harness_note = ""
    runnable = solution_code
    if needs_wrap(solution_code, language):
        phase("Wrapping LeetCode-style solution")
        try:
            runnable, harness_note = wrap(problem, solution_code, language)
            _log(f"wrapped LeetCode-style solution: {harness_note}")
        except Exception as e:
            out.verdict, out.error = "ERROR", f"could not build driver: {e}"
            out.runtime_s = time.perf_counter() - t0
            return out
    sol = Program(runnable, language)
    if sol.compile_error:
        out.verdict, out.error = "ERROR", f"solution did not compile: {sol.compile_error}"
        out.runtime_s = time.perf_counter() - t0
        sol.cleanup()
        return out
    try:
        if oracle is None:
            phase("Building reference solution + input validator")
            oracle = make_oracle(problem, examples)
            owned.append(oracle)
        out.reference_notes, out.validator_notes = oracle.ref_notes, oracle.validator_notes
        _record_oracle(out, oracle)
        if oracle.confidence == "failed":
            # Do not hunt for bugs with an oracle we know is wrong: that is how false BROKEN verdicts happen.
            out.verdict, out.reason = "INCONCLUSIVE", f"Reference solution could not be validated: {oracle.check_note}"
            return out
        if harness_note:
            out.steps.append({"step": 0, "action": "wrap_solution", "result": harness_note})

        seen: set[str] = set()
        pending: list[TestCase] = []
        executed_pending = False

        def t_generate(focus: str | None):
            nonlocal pending, executed_pending
            pending = generate_tests(problem, strategy, n_tests, focus, seen)
            executed_pending = False
            out.all_tests.extend(pending)
            seen.update(t.input for t in pending)
            cats = sorted({t.category.value for t in pending})
            return {"generated": len(pending), "categories": cats}

        def t_run():
            nonlocal oracle, executed_pending
            if not pending:
                return {"error": "no tests generated yet; call generate_tests first"}
            run = run_in_sandbox(oracle, sol, pending, stop_on_fail=True)
            rebuilt = False
            # Oracle self-repair: a whole batch rejected means the validator/reference is broken, not the tests.
            if len(run) >= 5 and all(r.verdict == "invalid_test" for r in run) and out.oracle_rebuilds < 1:
                _log("entire batch invalid -> rebuilding oracle once")
                out.oracle_rebuilds += 1
                phase("Rebuilding oracle (whole batch was invalid)")
                oracle = make_oracle(problem, examples)
                owned.append(oracle)
                out.reference_notes, out.validator_notes = oracle.ref_notes, oracle.validator_notes
                _record_oracle(out, oracle)
                run = run_in_sandbox(oracle, sol, pending, stop_on_fail=True)
                rebuilt = True
            executed_pending = True
            out.all_results.extend(run)
            valid = [r for r in run if r.verdict != "invalid_test"]
            invalid = [r for r in run if r.verdict == "invalid_test"]
            fail = next((r for r in run if r.failed), None)
            if fail and out.failing is None:
                out.failing = fail
            slow = max((r.sol_runtime_ms for r in valid), default=0)
            return {"ran": len(run), "invalid_tests": len(invalid),
                    "invalid_reason": invalid[0].stderr[:100] if invalid else None,
                    "passed": sum(r.verdict == "pass" for r in run),
                    "failure_found": fail is not None, "slowest_ms": int(round(slow, -2)),
                    "failure_verdict": fail.verdict if fail else None,
                    "failure_category": fail.category if fail else None,
                    "oracle_rebuilt": rebuilt}

        def t_shrink():
            if out.failing is None:
                return {"error": "no failing input to shrink"}
            if out.minimized is None:
                out.minimized = shrink_input(oracle, sol, out.failing.input)
            return {"minimized_length": len(out.minimized.input)}

        tools = {"generate_tests": lambda a: t_generate(a.get("focus")),
                 "run_in_sandbox": lambda a: t_run(),
                 "shrink_input": lambda a: t_shrink()}

        contents = [types.Content(role="user", parts=[types.Part(text=(
            f"Problem:\n{problem}\n\nSolution language: {language}. Find an input where the solution is wrong. "
            f"You have at most {MAX_STEPS} tool calls."))])]
        cfg = _cfg(system_instruction=AGENT_SYSTEM, temperature=0.4, tools=[TOOL_DECLS],
                   automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))

        for step in range(MAX_STEPS):
            _log(f"agent step {step + 1}")
            phase("Agent choosing next tool")
            resp = _call(contents, cfg)   # throttled + retry-aware
            cand = resp.candidates[0].content if resp.candidates else None
            calls = [p.function_call for p in (cand.parts if cand and cand.parts else []) if p.function_call]
            if not calls:
                out.steps.append({"step": step + 1, "action": "finish", "note": (resp.text or "")[:300]})
                break
            contents.append(cand)
            reply_parts = []
            for fc in calls:
                args = dict(fc.args or {})
                phase(f"Running {fc.name}")
                fn = tools.get(fc.name)
                result = fn(args) if fn else {"error": f"unknown tool {fc.name}"}
                out.steps.append({"step": step + 1, "action": fc.name, "args": args, "result": result})
                reply_parts.append(types.Part.from_function_response(name=fc.name, response={"result": result}))
            contents.append(types.Content(role="user", parts=reply_parts))
            if out.minimized is not None:
                break

        # Fallbacks: guarantee tests actually ran and a minimized counterexample exists
        phase("Finalizing")
        if pending and not executed_pending and out.failing is None:
            r = t_run()
            out.steps.append({"step": "auto", "action": "run_in_sandbox", "result": r})
        if not out.all_results and out.failing is None:
            t_generate(None)
            r = t_run()
            out.steps.append({"step": "auto", "action": "generate_tests+run_in_sandbox", "result": r})
        if out.failing is not None and out.minimized is None:
            out.minimized = shrink_input(oracle, sol, out.failing.input)
            out.steps.append({"step": "auto", "action": "shrink_input", "result": {"minimized_length": len(out.minimized.input)}})

        out.total_tests = len(out.all_results)
        out.invalid_tests = sum(r.verdict == "invalid_test" for r in out.all_results)
        valid_runs = out.total_tests - out.invalid_tests
        if oracle.confidence == "failed":
            out.verdict, out.failing, out.minimized = "INCONCLUSIVE", None, None
            out.reason = f"Reference solution could not be validated: {oracle.check_note}"
        elif out.failing is not None:
            out.verdict = "BROKEN"
        elif valid_runs == 0:
            out.verdict = "INCONCLUSIVE"   # nothing valid ever ran: not evidence the solution is correct
        else:
            out.verdict = "NO_BUG_FOUND"
    except Exception as e:
        out.verdict, out.error = "ERROR", f"{type(e).__name__}: {e}"
    finally:
        sol.cleanup()
        for o in owned:
            o.cleanup()
        out.runtime_s = time.perf_counter() - t0
    return out