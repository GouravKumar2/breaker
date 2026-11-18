"""Loading eval problems, building variants, and confirming that a mutant is really buggy."""
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.sandbox import Program, outputs_match
from eval.mutate import BUG_TYPES, mutants

ROOT = Path(__file__).parent
PROBLEMS_DIR = ROOT / "problems"
CACHE_DIR = ROOT / "cache"
CONFIRM_FILE = CACHE_DIR / "confirm.jsonl"


@dataclass
class Variant:
    problem: str
    vid: str            # unique within the problem, e.g. "correct", "hand:flip_comparison__strict", "mut:off_by_one:1"
    kind: str           # correct | handwritten | mutant
    bug_type: str       # "" for correct
    description: str
    code: str
    language: str


@dataclass
class EvalProblem:
    name: str
    statement: str
    correct_code: str
    language: str
    gen_path: Path | None
    bugs_dir: Path


def _ext(lang):
    return "cpp" if lang == "cpp" else "py"


def load_problems(only: list[str] | None = None) -> list[EvalProblem]:
    out = []
    for d in sorted(PROBLEMS_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith("_") or (only and d.name not in only):
            continue
        lang = "cpp" if (d / "correct.cpp").exists() else "python"
        correct = d / f"correct.{_ext(lang)}"
        gen = d / "gen.py"
        out.append(EvalProblem(d.name, (d / "problem.md").read_text(encoding="utf-8").strip(),
                               correct.read_text(encoding="utf-8"), lang, gen if gen.exists() else None, d / "bugs"))
    return out


def _sha(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:24]


def make_inputs(p: EvalProblem, n: int = 150) -> list[str]:
    """Run the problem's gen.py with seeds 0..n-1 (cached per gen.py content)."""
    if p.gen_path is None:
        return []
    code = p.gen_path.read_text(encoding="utf-8")
    cache = CACHE_DIR / f"inputs_{p.name}_{_sha(code, str(n))}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    inputs = []
    for seed in range(n):
        r = subprocess.run([sys.executable, str(p.gen_path), str(seed)], capture_output=True, text=True, timeout=20)
        if r.returncode == 0 and r.stdout.strip():
            inputs.append(r.stdout)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(inputs), encoding="utf-8")
    return inputs


def find_witness(p: EvalProblem, inputs: list[str], variant_code: str, variant_lang: str, ref_outputs: list[str | None],
                 ref_prog: Program) -> str | None:
    """Return an input on which the variant differs from the correct solution, or None (likely equivalent)."""
    key = _sha(p.correct_code, variant_code, str(len(inputs)), p.gen_path.read_text(encoding="utf-8") if p.gen_path else "")
    if CONFIRM_FILE.exists():
        for line in CONFIRM_FILE.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row["k"] == key:
                return row["witness"]
    prog = Program(variant_code, variant_lang)
    witness = None
    try:
        if prog.compile_error:
            witness = inputs[0] if inputs else ""
        else:
            for inp, exp in zip(inputs, ref_outputs):
                if exp is None:
                    continue
                r = prog.run(inp, timeout=2.0)
                if r.status != "ok" or not outputs_match(r.stdout, exp):
                    witness = inp
                    break
    finally:
        prog.cleanup()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIRM_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"k": key, "witness": witness}) + "\n")
    return witness


def build_variants(p: EvalProblem, per_type: int = 2, seed: int = 0, confirm: bool = True):
    """Returns (variants, stats). variants[0] is always the correct solution.
    Buggy variants are kept only if a witness input proves they differ from the correct solution
    (so 'equivalent mutants' do not unfairly count as misses). Unconfirmed ones are counted in stats."""
    variants = [Variant(p.name, "correct", "correct", "", "reference-correct solution", p.correct_code, p.language)]
    cands: list[Variant] = []
    if p.bugs_dir.exists():
        for f in sorted(p.bugs_dir.iterdir()):
            if f.suffix not in (".py", ".cpp") or "__" not in f.stem:
                continue
            bt = f.stem.split("__")[0]
            bt = bt if bt in BUG_TYPES else "other"
            cands.append(Variant(p.name, f"hand:{f.stem}", "handwritten", bt, f.stem,
                                 f.read_text(encoding="utf-8"), "cpp" if f.suffix == ".cpp" else "python"))
    if p.language == "python":
        for m in mutants(p.correct_code, per_type=per_type, seed=seed):
            n = sum(1 for c in cands if c.bug_type == m.bug_type and c.kind == "mutant") + 1
            cands.append(Variant(p.name, f"mut:{m.bug_type}:{n}", "mutant", m.bug_type, m.description, m.code, "python"))

    stats = {"candidates": len(cands), "confirmed": 0, "unconfirmed": 0}
    inputs = make_inputs(p) if confirm else []
    if confirm and inputs:
        ref = Program(p.correct_code, p.language)
        try:
            ref_out = []
            for inp in inputs:
                r = ref.run(inp, timeout=5.0)
                ref_out.append(r.stdout if r.status == "ok" else None)
            for v in cands:
                w = find_witness(p, inputs, v.code, v.language, ref_out, ref)
                if w is not None:
                    variants.append(v)
                    stats["confirmed"] += 1
                else:
                    stats["unconfirmed"] += 1
        finally:
            ref.cleanup()
    else:
        variants.extend(cands)   # no gen.py: trust the labels
        stats["confirmed"] = len(cands)
    return variants, stats
