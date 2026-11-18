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
    confidence: str = "unverified"   # verified | unverified (no samples to check) | failed
    samples_total: int = 0
    samples_passed: int = 0
    attempts: int = 1                # reference generations used (1 = first try passed)
    check_note: str = ""

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


def _ex_input(ex) -> str:
    return ex.input if ex.input.endswith("\n") else ex.input + "\n"


def check_reference(ref: Program, examples: list) -> list[tuple]:
    """Run the reference on statement examples. Returns [(example, why)] for every failure."""
    fails = []
    for ex in examples:
        r = ref.run(_ex_input(ex), timeout=REF_TIMEOUT)
        if r.status != "ok":
            fails.append((ex, f"{r.status}: {r.stderr[-200:]}"))
        elif not outputs_match(r.stdout, ex.output):
            fails.append((ex, f"printed {r.stdout.strip()[:200]!r}"))
    return fails


def check_validator(val: Program, examples: list) -> list[str]:
    """Validator must accept the statement examples. Returns the rejected inputs."""
    return [_ex_input(ex) for ex in examples if val.run(_ex_input(ex), timeout=VAL_TIMEOUT).status != "ok"]


def make_oracle(problem: str, examples: list | None = None, max_retries: int = 2) -> Oracle:
    """Build reference + validator. If statement examples are given, both are checked against them and
    regenerated (with the failure fed back) up to `max_retries` times. Confidence is reported, never assumed."""
    examples = examples or []
    bf = generate_structured(prompts.reference_prompt(problem), BruteForce,
                             system=prompts.REF_SYSTEM, temperature=0.2)
    vc = generate_structured(prompts.validator_prompt(problem), ValidatorCode,
                             system=prompts.VALIDATOR_SYSTEM, temperature=0.2)
    ref, val = Program(bf.code, "python"), Program(vc.code, "python")
    o = Oracle(ref, val, bf.notes, vc.notes, samples_total=len(examples))
    if not examples:
        o.check_note = "no statement examples available; reference not validated"
        return o

    fails = check_reference(ref, examples)
    while fails and o.attempts <= max_retries:
        ref.cleanup()
        bf = generate_structured(prompts.reference_fix_prompt(problem, bf.code, fails), BruteForce,
                                 system=prompts.REF_SYSTEM, temperature=0.2)
        ref = Program(bf.code, "python")
        o.ref, o.ref_notes, o.attempts = ref, bf.notes, o.attempts + 1
        fails = check_reference(ref, examples)
    o.samples_passed = len(examples) - len(fails)

    rejected = check_validator(val, examples)
    tries = 0
    while rejected and tries < max_retries:
        val.cleanup()
        vc = generate_structured(prompts.validator_fix_prompt(problem, vc.code, rejected), ValidatorCode,
                                 system=prompts.VALIDATOR_SYSTEM, temperature=0.2)
        val = Program(vc.code, "python")
        o.validator, o.validator_notes = val, vc.notes
        rejected = check_validator(val, examples)
        tries += 1

    if fails:
        ex, why = fails[0]
        o.confidence = "failed"
        o.check_note = (f"reference failed {len(fails)}/{len(examples)} statement examples after "
                        f"{o.attempts} attempts (e.g. input {ex.input.strip()[:60]!r}, expected "
                        f"{ex.output.strip()[:60]!r}, {why[:80]})")
    elif rejected:
        o.confidence = "failed"
        o.check_note = f"input validator rejects {len(rejected)}/{len(examples)} valid statement examples"
    else:
        o.confidence = "verified"
        o.check_note = f"reference passed {len(examples)}/{len(examples)} statement examples (attempt {o.attempts})"
    return o


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