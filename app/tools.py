"""The three agent tools + helpers. Only real execution decides correctness, never the LLM."""
from dataclasses import dataclass, asdict

from app import prompts
from app.llm import generate_structured
from app.sandbox import Program, outputs_match
from app.schemas import BruteForce, TestCase, TestSuite, ValidatorCode
from app.shrink import shrink

SOL_TIMEOUT = 4.0
REF_TIMEOUT = 10.0
VAL_TIMEOUT = 5.0
GEN_TIMEOUT = 5.0
SHRINK_TIMEOUT = 2.0
MAX_INPUT_CHARS = 200_000


@dataclass
class Oracle:
    """Everything derived from the problem statement: reference solution + input validator."""
    ref: Program
    validator: Program
    ref_notes: str
    validator_notes: str

    def cleanup(self):
        self.ref.cleanup()
        self.validator.cleanup()


@dataclass
class CaseResult:
    input: str
    category: str
    reasoning: str
    verdict: str   # pass | fail_wrong_answer | fail_runtime_error | fail_timeout | fail_memory | invalid_test
    expected: str
    actual: str
    sol_runtime_ms: float
    stderr: str = ""

    @property
    def failed(self) -> bool:
        return self.verdict.startswith("fail")

    def to_dict(self):
        return asdict(self)


def make_oracle(problem: str) -> Oracle:
    bf = generate_structured(prompts.reference_prompt(problem), BruteForce,
                             system=prompts.REF_SYSTEM, temperature=0.2)
    vc = generate_structured(prompts.validator_prompt(problem), ValidatorCode,
                             system=prompts.VALIDATOR_SYSTEM, temperature=0.2)
    return Oracle(Program(bf.code, "python"), Program(vc.code, "python"), bf.notes, vc.notes)


def _materialize(t: TestCase) -> TestCase | None:
    """Turn generator_code into a literal input by actually running it in the sandbox."""
    if t.generator_code.strip():
        gen = Program(t.generator_code, "python")
        try:
            r = gen.run("", timeout=GEN_TIMEOUT)
        finally:
            gen.cleanup()
        if r.status != "ok" or not r.stdout.strip() or len(r.stdout) > MAX_INPUT_CHARS:
            return None
        t.input = r.stdout
    if not t.input.strip():
        return None
    if not t.input.endswith("\n"):
        t.input += "\n"
    return t


def generate_tests(problem: str, strategy: str = "generic", n: int = 25,
                   focus: str | None = None, seen: set[str] | None = None) -> list[TestCase]:
    """TOOL 1: ask the LLM for adversarial inputs (validated by pydantic, one retry inside llm.py).
    Large inputs come from LLM-written generator programs that we execute ourselves."""
    seen = seen or set()
    suite = generate_structured(
        prompts.tests_prompt(problem, strategy, n, focus, list(seen)),
        TestSuite, system=prompts.TESTS_SYSTEM, temperature=0.8,
    )
    out, local = [], set(seen)
    for t in suite.tests:
        t = _materialize(t)
        if t is not None and t.input not in local:
            local.add(t.input)
            out.append(t)
    return out


def judge(oracle: Oracle, sol: Program, inp: str, sol_timeout: float = SOL_TIMEOUT) -> tuple[str, str, str, float, str]:
    """Run validator, reference, then solution -> (verdict, expected, actual, sol_ms, stderr)."""
    v = oracle.validator.run(inp, timeout=VAL_TIMEOUT)
    if v.status != "ok":
        return "invalid_test", "", "", 0.0, f"validator rejected input ({v.status})"
    r = oracle.ref.run(inp, timeout=REF_TIMEOUT)
    if r.status != "ok" or not r.stdout.strip():
        return "invalid_test", "", "", 0.0, f"reference {r.status}: {r.stderr[-300:]}"
    s = sol.run(inp, timeout=sol_timeout)
    if s.status == "timeout":
        return "fail_timeout", r.stdout, "", s.runtime_ms, ""
    if s.status == "memory":
        return "fail_memory", r.stdout, "", s.runtime_ms, ""
    if s.status == "runtime_error":
        return "fail_runtime_error", r.stdout, s.stdout, s.runtime_ms, s.stderr[-500:]
    if not outputs_match(r.stdout, s.stdout):
        return "fail_wrong_answer", r.stdout, s.stdout, s.runtime_ms, ""
    return "pass", r.stdout, s.stdout, s.runtime_ms, ""


def run_in_sandbox(oracle: Oracle, sol: Program, tests: list[TestCase], stop_on_fail: bool = True) -> list[CaseResult]:
    """TOOL 2: execute tests for real and report verdicts."""
    results = []
    for t in tests:
        v, exp, act, ms, err = judge(oracle, sol, t.input)
        results.append(CaseResult(t.input, t.category.value, t.reasoning, v, exp, act, ms, err))
        if stop_on_fail and results[-1].failed:
            break
    return results


def shrink_input(oracle: Oracle, sol: Program, failing_input: str) -> CaseResult:
    """TOOL 3: LLM-free minimization. A candidate counts only if the validator accepts it,
    the reference runs on it, AND the solution still fails."""
    def still_fails(s: str) -> bool:
        return judge(oracle, sol, s, sol_timeout=SHRINK_TIMEOUT)[0].startswith("fail")

    small = shrink(failing_input, still_fails)
    v, exp, act, ms, err = judge(oracle, sol, small)
    return CaseResult(small, "minimized", "delta-debugged counterexample", v, exp, act, ms, err)