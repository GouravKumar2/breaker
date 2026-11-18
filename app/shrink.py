"""LLM-free input minimizer (delta debugging). Knows nothing about sandboxes: it just needs a predicate."""
from typing import Callable

Predicate = Callable[[str], bool]


class _Budget:
    def __init__(self, predicate: Predicate, max_calls: int):
        self.predicate = predicate
        self.left = max_calls
        self.cache: dict[str, bool] = {}

    def __call__(self, s: str) -> bool:
        if s in self.cache:
            return self.cache[s]
        if self.left <= 0:
            return False
        self.left -= 1
        r = self.predicate(s)
        self.cache[s] = r
        return r


def _ddmin(items: list, test: Callable[[list], bool]) -> list:
    """Classic ddmin: find a small sublist for which test() is still True."""
    n = 2
    while len(items) >= 2:
        chunk = max(1, len(items) // n)
        subsets = [items[i:i + chunk] for i in range(0, len(items), chunk)]
        reduced = False
        # try each chunk alone, then each complement
        for sub in subsets:
            if test(sub):
                items, n, reduced = sub, 2, True
                break
        if not reduced:
            for i in range(len(subsets)):
                comp = [x for j, s in enumerate(subsets) if j != i for x in s]
                if comp and test(comp):
                    items, n, reduced = comp, max(n - 1, 2), True
                    break
        if not reduced:
            if n >= len(items):
                break
            n = min(len(items), n * 2)
    return items


def _shrink_lines(s: str, check: _Budget) -> str:
    lines = s.splitlines()
    best = _ddmin(lines, lambda ls: check("\n".join(ls) + "\n"))
    return "\n".join(best) + "\n"


def _shrink_tokens(s: str, check: _Budget) -> str:
    """Drop tokens within each line (one line at a time)."""
    lines = s.splitlines()
    for i in range(len(lines)):
        toks = lines[i].split()
        if len(toks) < 2:
            continue

        def test(sub, i=i):
            trial = lines[:i] + [" ".join(sub)] + lines[i + 1:]
            return check("\n".join(trial) + "\n")

        lines[i] = " ".join(_ddmin(toks, test))
    return "\n".join(lines) + "\n"


def _candidates(tok: str) -> list[str]:
    """Smaller replacements for an integer token, most aggressive first."""
    try:
        v = int(tok)
    except ValueError:
        return []
    if v == 0:
        return []
    sign = -1 if v < 0 else 1
    a = abs(v)
    cands = {0, sign, sign * (a // 2), sign * (a - 1), -sign * 1 if sign < 0 else 0}
    cands.discard(v)
    return [str(c) for c in sorted(cands, key=abs) if abs(c) < a]


def _shrink_numbers(s: str, check: _Budget) -> str:
    lines = s.splitlines()
    changed = True
    while changed and check.left > 0:
        changed = False
        for i in range(len(lines)):
            toks = lines[i].split()
            for j in range(len(toks)):
                for c in _candidates(toks[j]):
                    trial_toks = toks[:j] + [c] + toks[j + 1:]
                    trial = lines[:i] + [" ".join(trial_toks)] + lines[i + 1:]
                    if check("\n".join(trial) + "\n"):
                        toks, lines[i], changed = trial_toks, " ".join(trial_toks), True
                        break
    return "\n".join(lines) + "\n"


def shrink(failing_input: str, still_fails: Predicate, max_calls: int = 200) -> str:
    """Return a smaller input for which still_fails() is True (or the original if nothing smaller works)."""
    check = _Budget(still_fails, max_calls)
    if not check(failing_input):
        return failing_input  # not reproducible; don't pretend to shrink
    best = failing_input
    for step in (_shrink_lines, _shrink_tokens, _shrink_numbers):
        candidate = step(best, check)
        if len(candidate) <= len(best) and check(candidate):
            best = candidate
    return best