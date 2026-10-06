"""Evaluation runner CLI.

  python -m intake.eval --gold v1 --prompts triage=v2
  python -m intake.eval --gold v1 --limit 10 --repeat 1     # PR smoke test
  python -m intake.eval --export data/eval_runs/v1.json --run <id>

Prints the headline metrics, the diff against the previous run and the release gate.
"""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from intake.eval.runner import export_run, previous_run, run_eval
from intake.llm.factory import make_llm


def _fmt(metric: dict[str, Any] | int | float | None) -> str:
    if isinstance(metric, dict):
        if metric.get("value") is None:
            return "n/a"
        low, high = metric["ci95"]
        return (
            f"{metric['value']:.1%} ({metric['n']}/{metric['total']}, 95% CI {low:.0%}-{high:.0%})"
        )
    return "n/a" if metric is None else str(metric)


HEADLINES = (
    ("Field accuracy (clean)", "clean", "field_accuracy"),
    ("Field accuracy (hard)", "hard", "field_accuracy"),
    ("Evidence validity", "all", "evidence_validity"),
    ("Priority agreement", "all", "priority_agreement"),
    ("  model only", "all", "model_priority_agreement"),
    ("Under-triage", "all", "under_triage"),
    ("  model only", "all", "model_under_triage"),
    ("Over-triage", "all", "over_triage"),
    ("P1 suggested as P3/P4", "all", "severe_under_triage"),
    ("Retrieval recall@5", "all", "retrieval_recall_at_5"),
    ("Protocol top-1", "all", "protocol_top1"),
    ("Contrast flag accuracy", "all", "contrast_flag_accuracy"),
    ("Manual entry", "all", "manual_entry"),
    ("Cost per case (USD)", "all", "cost_usd_per_case"),
    ("Latency p50 (ms)", "all", "latency_ms_p50"),
)


def print_report(result: dict[str, Any], baseline: dict[str, Any] | None) -> None:
    m = result["metrics"]
    if m.get("simulation"):
        print("SIMULATION (dev-oracle): not model output; do not report these numbers.\n")
    print(f"Run {result['id']}")
    for label, group, key in HEADLINES:
        current = m[group][key]
        line = f"  {label:<26} {_fmt(current)}"
        if baseline is not None:
            line += f"   (was {_fmt(baseline['metrics'][group][key])})"
        print(line)
    nondet = m.get("nondeterminism")
    if nondet is not None:
        print(f"  {'Non-deterministic cases':<26} {len(nondet['cases'])}")
        for case in nondet["cases"][:10]:
            print(f"    {case['case_key']}: {', '.join(case['differs'][:6])}")
    failing = [
        c
        for c in result["per_case"]
        if c["priority"] != c["gold_priority"]
        or c["protocol_id"] != c["gold_protocol_id"]
        or not c["contrast_ok"]
    ]
    if failing:
        print("  Failing cases:")
        for c in failing[:20]:
            print(
                f"    {c['case_key']} ({c['difficulty']}): priority {c['priority']} "
                f"(gold {c['gold_priority']}), protocol {c['protocol_id']} "
                f"(gold {c['gold_protocol_id']}), flags {c['contrast_flags']}"
            )
    gate = m["release_gate"]
    print(f"Release gate: {'PASS' if gate['passed'] else 'FAIL'}")
    for check in gate["checks"]:
        print(f"  [{'x' if check['ok'] else ' '}] {check['check']}: {check['value']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--gold", default="v1")
    parser.add_argument("--prompts", nargs="*", default=[], help="step=version, e.g. triage=v2")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--cases", help="comma-separated case keys")
    parser.add_argument("--label")
    parser.add_argument("--backend", choices=["openai", "dev-oracle"])
    parser.add_argument("--export", type=Path, help="write a snapshot of --run (or this run)")
    parser.add_argument("--run", help="existing run id (with --export)")
    args = parser.parse_args()

    if args.export and args.run:
        export_run(args.run, args.export)
        print(f"Wrote {args.export}")
        return 0
    overrides = dict(p.split("=", 1) for p in args.prompts)
    result = asyncio.run(
        run_eval(
            make_llm(args.backend),
            gold_version=args.gold,
            prompt_overrides=overrides,
            repeat=args.repeat,
            case_keys=args.cases.split(",") if args.cases else None,
            limit=args.limit,
            label=args.label,
        )
    )
    import uuid

    print_report(result, previous_run(args.gold, uuid.UUID(result["id"])))
    if args.export:
        export_run(result["id"], args.export)
        print(f"Wrote {args.export}")
    return 0 if result["metrics"]["release_gate"]["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
