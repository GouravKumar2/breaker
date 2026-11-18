"""Run Breaker on correct + buggy variants and report catch rate / false-alarm rate.

    python -m eval.run_eval                      # all problems, both strategies
    python -m eval.run_eval --problems max_subarray --strategies boundary
    python -m eval.run_eval --report-only        # just rebuild results.md / results.csv from raw.jsonl

Resumable: finished runs are appended to eval/results/raw.jsonl and skipped next time (ERROR rows are retried).
LLM responses are cached in eval/cache/llm_cache.jsonl, so a rerun after a crash or a code tweak is mostly free.
"""
import argparse
import csv
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

from app import llm
from app.config import settings
from app.normalize import get_examples, normalize_problem
from app.tools import make_oracle
from eval.common import CACHE_DIR, ROOT, build_variants, load_problems

RAW = ROOT / "results" / "raw.jsonl"
MD = ROOT / "results.md"
CSV = ROOT / "results.csv"
TOOL_NAMES = {"generate_tests", "run_in_sandbox", "shrink_input"}


def load_done() -> dict[str, dict]:
    done = {}
    if RAW.exists():
        for line in RAW.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("verdict") != "ERROR":      # errors (quota, network) get retried
                row.setdefault("validation", "off")   # rows from before Part B had no reference validation
                done[row["key"]] = row
    return done


def rate(n, d):
    return f"{100 * n / d:.0f}% ({n}/{d})" if d else "n/a"


def summarize(rows: list[dict]) -> str:
    rows = [r for r in rows if r["model"] == settings.gemini_model] or rows
    all_rows = rows
    mode = "on" if any(r["validation"] == "on" for r in rows) else "off"
    rows = [r for r in rows if r["validation"] == mode]
    strategies = sorted({r["strategy"] for r in rows})
    out = [f"# Breaker eval results\n", f"Model: `{', '.join(sorted({r['model'] for r in rows}))}`. "
           f"Problems: {len({r['problem'] for r in rows})}. Runs: {len(rows)}. Reference validation: **{mode}**.\n",
           "Buggy variants are only counted if a witness input proves they differ from the correct solution. "
           "ERROR runs are excluded from the rates (they are retried on the next run); INCONCLUSIVE counts as a miss.\n"]

    def agg(sub, buggy):
        sub = [r for r in sub if r["verdict"] != "ERROR"]
        n = len(sub)
        broken = sum(r["verdict"] == "BROKEN" for r in sub)
        inc = sum(r["verdict"] == "INCONCLUSIVE" for r in sub)
        avg = lambda k: f"{sum(r[k] for r in sub) / n:.1f}" if n else "n/a"
        return n, broken, inc, avg

    out.append("## Strategy comparison\n")
    out.append("| Strategy | Bug catch rate | False-alarm rate (correct solutions) | Inconclusive | Avg tests run | Avg agent tool calls | Avg LLM calls | Avg wall time (s)* |")
    out.append("|---|---|---|---|---|---|---|---|")
    for s in strategies + ["ALL"]:
        sub = [r for r in rows if s == "ALL" or r["strategy"] == s]
        bug = [r for r in sub if r["kind"] != "correct" and r["verdict"] != "ERROR"]
        ok = [r for r in sub if r["kind"] == "correct" and r["verdict"] != "ERROR"]
        n, _, _, avg = agg(bug + ok, True)
        out.append(f"| {s} | {rate(sum(r['verdict'] == 'BROKEN' for r in bug), len(bug))} | "
                   f"{rate(sum(r['verdict'] == 'BROKEN' for r in ok), len(ok))} | "
                   f"{sum(r['verdict'] == 'INCONCLUSIVE' for r in bug + ok)} | {avg('total_tests')} | {avg('tool_steps')} | "
                   f"{avg('llm_calls')} | {avg('wall_s')} |")

    out.append("\n## Catch rate by bug type\n")
    types = sorted({r["bug_type"] for r in rows if r["kind"] != "correct"})
    out.append("| Bug type | " + " | ".join(strategies) + " | ALL |")
    out.append("|---|" + "---|" * (len(strategies) + 1))
    for bt in types:
        cells = []
        for s in strategies + ["ALL"]:
            sub = [r for r in rows if r["bug_type"] == bt and r["verdict"] != "ERROR" and (s == "ALL" or r["strategy"] == s)]
            cells.append(rate(sum(r["verdict"] == "BROKEN" for r in sub), len(sub)))
        out.append(f"| {bt} | " + " | ".join(cells) + " |")

    out.append("\n## Per problem (all strategies)\n")
    out.append("| Problem | Buggy variants | Caught | False alarms / correct runs |")
    out.append("|---|---|---|---|")
    for p in sorted({r["problem"] for r in rows}):
        bug = [r for r in rows if r["problem"] == p and r["kind"] != "correct" and r["verdict"] != "ERROR"]
        ok = [r for r in rows if r["problem"] == p and r["kind"] == "correct" and r["verdict"] != "ERROR"]
        out.append(f"| {p} | {len(bug)} | {rate(sum(r['verdict'] == 'BROKEN' for r in bug), len(bug))} | "
                   f"{sum(r['verdict'] == 'BROKEN' for r in ok)}/{len(ok)} |")

    # Before/after: same (problem, variant, strategy) runs with and without reference validation
    def ids(m):
        return {(r["problem"], r["variant"], r["strategy"]): r for r in all_rows
                if r["validation"] == m and r["verdict"] != "ERROR"}
    off, on = ids("off"), ids("on")
    common = sorted(set(off) & set(on))
    if common:
        out.append("\n## Reference validation: before vs after (identical runs)\n")
        out.append("| Reference validation | Bug catch rate | False-alarm rate | Inconclusive (correct solutions) | Inconclusive (buggy) |")
        out.append("|---|---|---|---|---|")
        for label, d in (("off (before)", off), ("on (after)", on)):
            bug = [d[k] for k in common if d[k]["kind"] != "correct"]
            ok = [d[k] for k in common if d[k]["kind"] == "correct"]
            out.append(f"| {label} | {rate(sum(r['verdict'] == 'BROKEN' for r in bug), len(bug))} | "
                       f"{rate(sum(r['verdict'] == 'BROKEN' for r in ok), len(ok))} | "
                       f"{sum(r['verdict'] == 'INCONCLUSIVE' for r in ok)} | {sum(r['verdict'] == 'INCONCLUSIVE' for r in bug)} |")

    # Oracle confidence
    conf = [r for r in rows if r.get("oracle_confidence")]
    if conf:
        out.append("\n## Oracle confidence (reference validated against statement samples)\n")
        out.append("| Confidence | Runs | BROKEN verdicts |")
        out.append("|---|---|---|")
        for c in sorted({r["oracle_confidence"] for r in conf}):
            sub = [r for r in conf if r["oracle_confidence"] == c]
            out.append(f"| {c} | {len(sub)} | {sum(r['verdict'] == 'BROKEN' for r in sub)} |")

    out.append("\n\\* Wall time = real execution time + the original LLM latency of cached responses "
               "(replayed), so reruns report comparable numbers. Test suites are shared by all variants of a problem "
               "(identical prompts hit the same cache entry), so strategies are compared on identical inputs.")
    return "\n".join(out) + "\n"


def write_reports(done: dict[str, dict]):
    rows = list(done.values())
    if not rows:
        print("no results yet")
        return
    MD.write_text(summarize(rows), encoding="utf-8")
    fields = ["problem", "variant", "kind", "bug_type", "description", "strategy", "model", "verdict", "failure_verdict",
              "failure_category", "total_tests", "invalid_tests", "tool_steps", "llm_calls", "llm_cache_hits",
              "exec_s", "wall_s", "minimized_len", "validation", "oracle_confidence", "oracle_samples", "oracle_attempts", "error"]
    with open(CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {MD} and {CSV}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--problems", nargs="*")
    ap.add_argument("--strategies", nargs="*", default=["generic", "boundary"])
    ap.add_argument("--mutants-per-type", type=int, default=2)
    ap.add_argument("--n-tests", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--ref-validation", choices=["on", "off"], default="on",
                    help="check the LLM-written reference against statement examples (off = pre-Part-B behaviour)")
    ap.add_argument("--no-confirm", action="store_true", help="skip witness check (trust all variants)")
    a = ap.parse_args()

    RAW.parent.mkdir(parents=True, exist_ok=True)
    done = load_done()
    if a.report_only:
        return write_reports(done)

    from app.agent import run_break          # imported late so --report-only needs no API key
    print(f"LLM cache: {llm.enable_cache(str(CACHE_DIR / 'llm_cache.jsonl'))} entries loaded; model={settings.gemini_model}")
    for p in load_problems(a.problems):
        variants, st = build_variants(p, a.mutants_per_type, a.seed, confirm=not a.no_confirm)
        print(f"\n== {p.name}: {len(variants) - 1} buggy variants ({st['unconfirmed']} unconfirmed mutants dropped)")
        suffix = "|val" if a.ref_validation == "on" else ""
        todo = [(v, s) for v in variants for s in a.strategies
                if f"{p.name}|{v.vid}|{s}|{settings.gemini_model}{suffix}" not in done]
        if not todo:
            continue
        # One spec + one oracle per problem, shared by every variant (so the oracle is not re-sampled per run).
        spec, _ = normalize_problem(p.statement, p.correct_code, p.language)
        oracle = make_oracle(spec, get_examples(p.statement) if a.ref_validation == "on" else None)
        print(f"  oracle: {oracle.confidence} - {oracle.check_note}")
        try:
            for v, s in todo:
                key = f"{p.name}|{v.vid}|{s}|{settings.gemini_model}{suffix}"
                c0 = dict(llm.STATS)
                t0 = time.perf_counter()
                r = run_break(p.statement, v.code, v.language, strategy=s, n_tests=a.n_tests, oracle=oracle)
                exec_s = time.perf_counter() - t0
                d = {k: llm.STATS[k] - c0[k] for k in c0}
                row = {
                    "key": key, "problem": p.name, "variant": v.vid, "kind": v.kind, "bug_type": v.bug_type,
                    "description": v.description, "strategy": s, "model": settings.gemini_model,
                    "verdict": r.verdict, "failure_verdict": r.failing.verdict if r.failing else "",
                    "failure_category": r.failing.category if r.failing else "",
                    "total_tests": r.total_tests, "invalid_tests": r.invalid_tests,
                    "tool_steps": sum(1 for x in r.steps if x.get("action") in TOOL_NAMES),
                    "llm_calls": d["calls"] + d["hits"], "llm_cache_hits": d["hits"],
                    "exec_s": round(exec_s, 2), "wall_s": round(exec_s + d["replayed_s"], 2),
                    "minimized_len": len(r.minimized.input) if r.minimized else 0,
                    "validation": a.ref_validation, "oracle_confidence": r.oracle_confidence or oracle.confidence,
                    "oracle_samples": f"{oracle.samples_passed}/{oracle.samples_total}", "oracle_attempts": oracle.attempts,
                    "error": (r.error or r.reason)[:300],
                }
                with open(RAW, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + "\n")
                if r.verdict != "ERROR":
                    done[key] = row
                print(f"  {v.vid:42s} {s:8s} -> {r.verdict:12s} tests={r.total_tests:3d} {exec_s:5.1f}s {r.error[:60]}")
        finally:
            oracle.cleanup()
        write_reports(done)
    write_reports(done)


if __name__ == "__main__":
    sys.exit(main())
