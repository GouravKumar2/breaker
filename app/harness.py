"""Wrap LeetCode-style solutions (class Solution, no main) into runnable stdin/stdout programs."""
import re

from app.llm import generate_structured
from app.schemas import Driver

CPP_PRELUDE = (
    "#include <bits/stdc++.h>\nusing namespace std;\n"
    "struct ListNode{int val;ListNode*next;ListNode():val(0),next(nullptr){}ListNode(int x):val(x),next(nullptr){}"
    "ListNode(int x,ListNode*n):val(x),next(n){}};\n"
    "struct TreeNode{int val;TreeNode*left;TreeNode*right;TreeNode():val(0),left(nullptr),right(nullptr){}"
    "TreeNode(int x):val(x),left(nullptr),right(nullptr){}"
    "TreeNode(int x,TreeNode*l,TreeNode*r):val(x),left(l),right(r){}};\n"
)
PY_PRELUDE = "import sys\nfrom typing import *\nfrom collections import *\nimport heapq, bisect, math, itertools, functools\n"

DRIVER_SYSTEM = (
    "You write stdin/stdout driver code for LeetCode-style solutions. You never modify the Solution class."
)

_CPP_MARKERS = [r"#include", r"\bpublic\s*:", r"\bvector\s*<", r"\bstd::", r"\bint\s+main\s*\(",
                r"\bstring\s+\w+\s*\(", r"\bunordered_map\s*<", r";\s*$"]
_PY_MARKERS = [r"^\s*def\s+\w+\s*\(", r"^\s*import\s+\w+", r"^\s*from\s+\w+\s+import", r"\bprint\s*\(",
               r"^\s*elif\b", r":\s*$"]


def _hits(code: str, pats: list[str]) -> int:
    return sum(1 for p in pats if re.search(p, code, re.M))


def language_mismatch(code: str, language: str) -> str | None:
    """Return an error message if the code clearly belongs to the other language, else None."""
    cpp, py = _hits(code, _CPP_MARKERS), _hits(code, _PY_MARKERS)
    if language == "python" and cpp >= 3 and cpp > py:
        return "This looks like C++ code but Python is selected. Switch the language dropdown to C++."
    if language == "cpp" and py >= 3 and py > cpp:
        return "This looks like Python code but C++ is selected. Switch the language dropdown to Python."
    return None


def needs_wrap(code: str, language: str) -> bool:
    if language == "cpp":
        return re.search(r"\bmain\s*\(", code) is None
    return re.search(r"^\s*class\s+Solution\b", code, re.M) is not None and "__main__" not in code


def wrap(problem: str, code: str, language: str) -> tuple[str, str]:
    """Return (runnable_code, driver_notes). Stored solution text stays the user's original code."""
    lang_name = "C++17" if language == "cpp" else "Python 3"
    prompt = (
        f"Problem statement (includes the stdin format):\n{problem}\n\n"
        f"The user's {lang_name} solution (class Solution only, do not repeat it):\n{code}\n\n"
        + ("Write ONLY a complete `int main()` that reads the input from stdin exactly in the stated stdin format, "
           "calls Solution on it, and prints the result in the stated output format. "
           "Assume <bits/stdc++.h> and `using namespace std;` are already included before the solution. "
           "Do not include the Solution class."
           if language == "cpp" else
           "Write ONLY the script tail (no class Solution): read stdin exactly in the stated stdin format, "
           "call Solution().<method>(...), and print the result in the stated output format. "
           "Assume `from typing import *` is already imported.")
    )
    d = generate_structured(prompt, Driver, system=DRIVER_SYSTEM, temperature=0.1)
    prelude = CPP_PRELUDE if language == "cpp" else PY_PRELUDE
    return f"{prelude}\n{code}\n\n{d.code}\n", d.notes