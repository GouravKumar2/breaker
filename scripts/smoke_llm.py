from app.llm import generate_structured
from app.schemas import TestSuite

problem = "Given n and an array of n integers, print the maximum subarray sum."
suite = generate_structured(
    f"Problem:\n{problem}\n\nGenerate 3 adversarial test inputs for a solution to this problem.",
    TestSuite,
    system="You are an expert competitive-programming problem setter who writes tricky tests.",
)
for t in suite.tests:
    print(f"[{t.category.value}] {t.reasoning}\n{t.input!r}\n")