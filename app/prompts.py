"""Prompt strategies. Kept separate so the eval can A/B them."""

REF_SYSTEM = (
    "You are an expert competitive programmer. You write SIMPLE, OBVIOUSLY CORRECT brute-force "
    "solutions: exhaustive search, O(n^2) or O(2^n) is fine, no clever optimizations, no external libraries. "
    "Read from stdin and write to stdout exactly in the format the problem states."
)

VALIDATOR_SYSTEM = (
    "You write strict input validators for competitive programming problems, like testlib validators. "
    "You never print anything."
)

TESTS_SYSTEM = (
    "You are a ruthless problem setter who writes adversarial test inputs to break contestants' solutions. "
    "Every input MUST strictly satisfy the problem's input format and constraints (e.g. if n >= 1, never use n = 0). "
    "NEVER exceed the stated value bounds: if the problem says |a_i| <= 10^9, then 2^31-1 or 2^31 are ILLEGAL. "
    "Use the exact bounds from the problem statement as your extreme values. "
    "For small inputs, put the literal text in `input` (under 1500 characters). "
    "For LARGE inputs (e.g. n = 1000 elements), NEVER write them out: leave `input` empty and put a short Python 3 "
    "program in `generator_code` that prints the complete input to stdout (use loops / list multiplication). "
    "Keep large inputs modest (a few thousand numbers at most) because a slow brute-force reference must run on them."
)

STRATEGIES = {
    "generic": (
        "Generate {n} diverse adversarial and edge-case inputs that are all VALID under the constraints. "
        "Cover boundary values, minimal inputs, duplicates, extreme values at the stated bounds, negative numbers, "
        "and tricky structures. Include 2-3 larger inputs via generator_code."
    ),
    "boundary": (
        "Generate {n} VALID inputs focused on BOUNDARIES. Prioritize: minimum and maximum allowed values for every "
        "variable (exactly at the stated bounds, never beyond), n at its smallest allowed value and just above, "
        "off-by-one situations (first/last element mattering), all-equal and all-negative "
        "arrays, sums that exceed 32-bit range when many max-value elements are added, and inputs where a loop bound "
        "or comparison operator (< vs <=) changes the answer. Include 2-3 max-size inputs via generator_code."
    ),
}


def reference_prompt(problem: str) -> str:
    return (
        f"Problem statement:\n{problem}\n\n"
        "Write a brute-force Python 3 reference solution. Return it in the schema."
    )


def validator_prompt(problem: str) -> str:
    return (
        f"Problem statement:\n{problem}\n\n"
        "Write a Python 3 input validator. Read ALL of stdin. Check the EXACT format (number of lines, "
        "number of tokens per line, integer parsing, no extra tokens) and EVERY constraint stated "
        "(ranges of all variables, relations between them such as the array length equaling n). "
        "Wrap everything in try/except so any parse failure exits with code 1. "
        "Exit code 0 if the input is valid, otherwise sys.exit(1). Print nothing."
    )


def tests_prompt(problem: str, strategy: str, n: int, focus: str | None = None, avoid: list[str] | None = None) -> str:
    parts = [f"Problem statement:\n{problem}\n", STRATEGIES[strategy].format(n=n)]
    if focus:
        parts.append(f"Extra focus requested for this round (still obey every constraint): {focus}")
    if avoid:
        shown = "\n".join(repr(a[:120]) for a in avoid[:15])
        parts.append(f"These inputs were already tried and passed; do NOT repeat or trivially vary them:\n{shown}")
    parts.append("Each small test's `input` is the exact stdin contents, ending with a newline.")
    return "\n\n".join(parts)


def reference_fix_prompt(problem: str, code: str, failures: list) -> str:
    shown = "\n\n".join(f"Input:\n{ex.input.rstrip()}\nExpected output:\n{ex.output.rstrip()}\nYour program: {why}"
                         for ex, why in failures[:3])
    return (
        f"Problem statement:\n{problem}\n\nYour previous brute-force solution:\n```python\n{code}\n```\n\n"
        f"It FAILED these examples from the statement:\n\n{shown}\n\n"
        "Re-read the input format, output format and edge cases, then write a corrected, simple brute-force "
        "Python 3 solution. Return it in the schema."
    )


def validator_fix_prompt(problem: str, code: str, rejected: list[str]) -> str:
    shown = "\n---\n".join(r.rstrip() for r in rejected[:3])
    return (
        f"Problem statement:\n{problem}\n\nYour previous input validator:\n```python\n{code}\n```\n\n"
        f"It wrongly REJECTED these inputs, which are valid examples from the statement:\n{shown}\n\n"
        "Fix the validator (exact format and every stated constraint; exit 0 if valid, sys.exit(1) otherwise)."
    )
