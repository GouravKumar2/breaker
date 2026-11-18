"""Bug injector: AST mutation rules for Python solutions. Each mutant is labeled with its bug type.
Hand-written buggy variants (any language) live in eval/problems/<name>/bugs/ and are loaded in common.py."""
import ast
import random
from dataclasses import dataclass

BUG_TYPES = ["off_by_one", "flip_comparison", "wrong_loop_bound", "int32_overflow", "missing_edge_case", "other"]

I32_HELPER = (
    "def _i32(v):\n"
    "    v &= 0xFFFFFFFF\n"
    "    return v - (1 << 32) if v >= (1 << 31) else v\n"
)

_FLIP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt}
_REVERSE = {ast.Lt: ast.Gt, ast.Gt: ast.Lt, ast.LtE: ast.GtE, ast.GtE: ast.LtE}


@dataclass
class Mutant:
    bug_type: str
    description: str
    code: str


def _sites(tree: ast.AST, bug_type: str) -> list[ast.AST]:
    nodes = list(ast.walk(tree))
    if bug_type == "off_by_one":
        return [n for n in nodes if isinstance(n, ast.Constant) and type(n.value) is int and abs(n.value) <= 1000]
    if bug_type == "flip_comparison":
        return [n for n in nodes if isinstance(n, ast.Compare) and len(n.ops) == 1 and type(n.ops[0]) in _FLIP]
    if bug_type == "wrong_loop_bound":
        out = []
        for n in nodes:
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "range" and 1 <= len(n.args) <= 2:
                out.append(n)
            elif isinstance(n, ast.For) and isinstance(n.iter, (ast.Name, ast.Subscript)):
                out.append(n)
        return out
    if bug_type == "int32_overflow":
        return [n for n in nodes if (isinstance(n, ast.AugAssign) and isinstance(n.op, (ast.Add, ast.Mult)))
                or (isinstance(n, ast.Assign) and isinstance(n.value, ast.BinOp)
                    and isinstance(n.value.op, (ast.Add, ast.Mult)) and len(n.targets) == 1)]
    if bug_type == "missing_edge_case":
        ok = (ast.Return, ast.Continue, ast.Break, ast.Pass, ast.Expr)
        return [n for n in nodes if isinstance(n, ast.If) and not n.orelse and all(isinstance(b, ok) for b in n.body)]
    return []


def _apply(tree: ast.Module, bug_type: str, idx: int) -> str:
    """Mutate the idx-th site (in a fresh copy) and return (description). Mutates `tree` in place."""
    site = _sites(tree, bug_type)[idx]
    line = getattr(site, "lineno", "?")
    if bug_type == "off_by_one":
        site.value += 1
        return f"constant at line {line} incremented by 1"
    if bug_type == "flip_comparison":
        old = type(site.ops[0])
        site.ops[0] = (_FLIP[old] if idx % 2 == 0 else _REVERSE[old])()
        return f"comparison at line {line}: {old.__name__} -> {type(site.ops[0]).__name__}"
    if bug_type == "wrong_loop_bound":
        if isinstance(site, ast.Call):
            if len(site.args) == 1:
                site.args[0] = ast.BinOp(site.args[0], ast.Sub(), ast.Constant(1))
            else:
                site.args[1] = ast.BinOp(site.args[1], ast.Sub(), ast.Constant(1))
            return f"range() upper bound at line {line} reduced by 1"
        site.iter = ast.Subscript(site.iter, ast.Slice(None, ast.UnaryOp(ast.USub(), ast.Constant(1))), ast.Load())
        return f"for-loop at line {line} skips the last element"
    if bug_type == "int32_overflow":
        if isinstance(site, ast.AugAssign):
            new = ast.Assign([site.target], ast.Call(ast.Name("_i32", ast.Load()),
                             [ast.BinOp(ast.copy_location(ast.Name(ast.unparse(site.target), ast.Load()), site),
                                        site.op, site.value)], []))
            ast.copy_location(new, site)
            _replace_stmt(tree, site, new)
        else:
            site.value = ast.Call(ast.Name("_i32", ast.Load()), [site.value], [])
        return f"result at line {line} truncated to int32"
    if bug_type == "missing_edge_case":
        _replace_stmt(tree, site, ast.copy_location(ast.Pass(), site))
        return f"guard `if` at line {line} removed"
    raise ValueError(bug_type)


def _replace_stmt(tree: ast.AST, old: ast.stmt, new: ast.stmt):
    for n in ast.walk(tree):
        for f in ("body", "orelse", "finalbody"):
            lst = getattr(n, f, None)
            if isinstance(lst, list) and old in lst:
                lst[lst.index(old)] = new
                return


def _with_helper(tree: ast.Module) -> None:
    helper = ast.parse(I32_HELPER).body
    i = 0
    while i < len(tree.body) and isinstance(tree.body[i], ast.ImportFrom) and tree.body[i].module == "__future__":
        i += 1
    tree.body[i:i] = helper


def mutants(source: str, per_type: int = 2, seed: int = 0) -> list[Mutant]:
    """Up to `per_type` distinct mutants per bug type, chosen with a seeded RNG (deterministic)."""
    rng = random.Random(seed)
    out: list[Mutant] = []
    seen = {ast.unparse(ast.parse(source))}
    for bt in BUG_TYPES:
        if bt == "other":
            continue
        n_sites = len(_sites(ast.parse(source), bt))
        order = list(range(n_sites))
        rng.shuffle(order)
        made = 0
        for idx in order:
            if made >= per_type:
                break
            tree = ast.parse(source)
            try:
                desc = _apply(tree, bt, idx)
                if bt == "int32_overflow":
                    _with_helper(tree)
                ast.fix_missing_locations(tree)
                code = ast.unparse(tree) + "\n"
            except Exception:
                continue
            if code in seen:
                continue
            seen.add(code)
            out.append(Mutant(bt, desc, code))
            made += 1
    return out
