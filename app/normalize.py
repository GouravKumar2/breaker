"""Turn ANY pasted problem (LeetCode, Codeforces, plain text) into a canonical stdin/stdout spec.
This spec, not the raw text, feeds the reference, validator, test generator and driver."""
from app.llm import generate_structured, _log
from app.models import sha
from app.schemas import ProblemSpec

_CACHE: dict[str, str] = {}

SYSTEM = "You convert programming problem statements into precise, unambiguous stdin/stdout specifications."

CONVENTION = (
    "If the statement is already a stdin/stdout problem (Codeforces/AtCoder style), keep its input format EXACTLY. "
    "If it is function-style (LeetCode), define stdin by this convention: each function argument goes on its own line, "
    "in signature order. string = the raw line (no quotes); int/long/bool = one token (bool as 0/1); "
    "array of numbers = one line with the length n, then one line with the n elements; "
    "array of strings = a line with the count, then one string per line; "
    "matrix = a line 'rows cols', then one line per row; "
    "linked list / tree = a line with the length, then a line of values (use the problem's own encoding for null). "
    "The output is printed the same way (the return value, formatted by the same convention). "
    "Convert the statement's examples into this format. "
    "Never invent constraints: copy the stated ones. If the statement lacks a bound, infer a reasonable small one."
)


def spec_to_text(s: ProblemSpec) -> str:
    ex = "\n\n".join(f"Example {i + 1} input:\n{e.input.rstrip()}\nExample {i + 1} output:\n{e.output.rstrip()}"
                     for i, e in enumerate(s.examples))
    warn = ("\nNOTE: several outputs may be valid for one input; outputs are compared exactly, so only "
            "problems with a unique answer are reliable.") if s.multiple_valid_answers else ""
    return (f"TASK: {s.task}\n\nINPUT FORMAT (stdin):\n{s.input_format}\n\n"
            f"OUTPUT FORMAT (stdout):\n{s.output_format}\n\nCONSTRAINTS:\n{s.constraints}\n\n{ex}{warn}")


def normalize_problem(raw: str, code: str, language: str) -> tuple[str, str]:
    """Return (spec_text, note). Cached per raw statement. Falls back to the raw text on failure."""
    key = sha(raw)
    if key in _CACHE:
        return _CACHE[key], "spec reused from cache"
    prompt = (
        f"Raw problem statement:\n{raw}\n\n"
        f"The user's solution ({language}), shown ONLY so you can read the real function signature / I/O style:\n{code[:3000]}\n\n"
        f"{CONVENTION}\nReturn the specification."
    )
    try:
        spec = generate_structured(prompt, ProblemSpec, system=SYSTEM, temperature=0.1)
        text = spec_to_text(spec)
        _CACHE[key] = text
        return text, "spec generated"
    except Exception as e:
        _log(f"normalize failed, using raw statement: {e}")
        return raw, f"normalize failed, raw statement used: {str(e)[:120]}"