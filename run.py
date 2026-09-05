#!/usr/bin/env python3
"""Run the recovery batch and print the comparison.

    python run.py                 # 500 payments, both policies
    python run.py --n 2000        # bigger batch
    python run.py --seed 42       # different draw
    python run.py --brief         # add the LLM root-cause brief
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from src import llm
from src.environment import Environment, generate_batch
from src.orchestrator import Metrics, run
from src.policy import BackoffRetryPolicy, NaiveRetryPolicy, SmartRecoveryPolicy

OUT = Path("out")


def rupees(paise: int) -> str:
    return f"Rs {paise / 100:>12,.2f}"


def table(rows: list[tuple[str, ...]]) -> str:
    ncols = len(rows[0])
    widths = [max(len(r[i]) for r in rows) for i in range(ncols)]
    out = []
    for i, r in enumerate(rows):
        cells = [f"{r[0]:<{widths[0]}}"] + [f"{r[j]:>{widths[j]}}" for j in range(1, ncols)]
        out.append("  " + "  ".join(cells))
        if i == 0:
            out.append("  " + "-" * (sum(widths) + 2 * (ncols - 1)))
    return "\n".join(out)


def compare(base: Metrics, backoff: Metrics, smart: Metrics) -> str:
    rows = [("metric", "naive retry", "backoff retry", "smart recovery")]

    def add(label, a, b, c):
        rows.append((label, a, b, c))

    add("payments in batch", str(base.total), str(backoff.total), str(smart.total))
    add("at risk", rupees(base.at_risk_paise), rupees(backoff.at_risk_paise),
        rupees(smart.at_risk_paise))
    add("recovered (count)", str(base.recovered), str(backoff.recovered), str(smart.recovered))
    add("recovery rate", f"{base.recovery_rate:.1%}", f"{backoff.recovery_rate:.1%}",
        f"{smart.recovery_rate:.1%}")
    add("recovered (value)", rupees(base.recovered_paise), rupees(backoff.recovered_paise),
        rupees(smart.recovered_paise))
    add("value recovery rate",
        f"{base.value_recovery_rate:.1%}", f"{backoff.value_recovery_rate:.1%}",
        f"{smart.value_recovery_rate:.1%}")
    add("charge attempts", str(base.attempts), str(backoff.attempts), str(smart.attempts))
    add("attempts per recovery",
        f"{base.attempts_per_recovery:.2f}", f"{backoff.attempts_per_recovery:.2f}",
        f"{smart.attempts_per_recovery:.2f}")
    add("customer messages", str(base.nudges), str(backoff.nudges), str(smart.nudges))
    add("attempts that could not work", str(base.wasted_attempts), str(backoff.wasted_attempts),
        str(smart.wasted_attempts))
    add("blocked by compliance gate", str(base.compliance_blocks), str(backoff.compliance_blocks),
        str(smart.compliance_blocks))
    add("blocked at execution (2nd gate)", str(base.compliance_stops),
        str(backoff.compliance_stops), str(smart.compliance_stops))
    add("recovery cost", rupees(base.cost_paise), rupees(backoff.cost_paise),
        rupees(smart.cost_paise))
    add("net recovered", rupees(base.net_paise), rupees(backoff.net_paise), rupees(smart.net_paise))

    delta_naive = smart.net_paise - base.net_paise
    lift_naive = (delta_naive / base.net_paise * 100) if base.net_paise else float("inf")
    delta_backoff = smart.net_paise - backoff.net_paise
    lift_backoff = (delta_backoff / backoff.net_paise * 100) if backoff.net_paise else float("inf")
    footer = (
        f"\n  net lift vs naive retry:    Rs {delta_naive / 100:>12,.2f} "
        f"({lift_naive:+.1f}%)"
        f"\n  net lift vs backoff retry:  Rs {delta_backoff / 100:>12,.2f} "
        f"({lift_backoff:+.1f}%)\n"
    )
    return table(rows) + "\n" + footer


def reason_table(m: Metrics) -> str:
    rows = [("failure reason", "n", "recovered")]
    for reason, s in sorted(m.by_reason.items(), key=lambda kv: -kv[1]["count"]):
        rate = s["recovered"] / s["count"] if s["count"] else 0
        rows.append((reason, str(s["count"]), f"{s['recovered']} ({rate:.0%})"))
    return table(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--brief", action="store_true", help="generate the merchant brief")
    args = ap.parse_args()

    payments = generate_batch(args.n, seed=args.seed)

    # Same environment seed for all three runs so the comparison is like for like.
    env_seed = args.seed + 1
    base_m, base_events = run(payments, NaiveRetryPolicy(), Environment(seed=env_seed))
    backoff_m, backoff_events = run(payments, BackoffRetryPolicy(), Environment(seed=env_seed))
    smart_m, smart_events = run(payments, SmartRecoveryPolicy(), Environment(seed=env_seed))

    print("\n== recovery comparison ==\n")
    print(compare(base_m, backoff_m, smart_m))
    print("== smart policy, by failure reason ==\n")
    print(reason_table(smart_m))
    print()

    OUT.mkdir(exist_ok=True)
    with (OUT / "audit_smart.jsonl").open("w") as f:
        for e in smart_events:
            f.write(json.dumps(asdict(e)) + "\n")
    with (OUT / "audit_naive.jsonl").open("w") as f:
        for e in base_events:
            f.write(json.dumps(asdict(e)) + "\n")
    with (OUT / "audit_backoff.jsonl").open("w") as f:
        for e in backoff_events:
            f.write(json.dumps(asdict(e)) + "\n")
    with (OUT / "metrics.json").open("w") as f:
        json.dump(
            {"naive": _dump(base_m), "backoff": _dump(backoff_m), "smart": _dump(smart_m)},
            f, indent=2,
        )

    print(f"  audit trail: {OUT}/audit_smart.jsonl ({len(smart_events)} events)")
    print(f"  metrics:     {OUT}/metrics.json\n")

    if args.brief:
        top = sorted(smart_m.by_reason.items(), key=lambda kv: -kv[1]["count"])[:5]
        summary = {
            "total": smart_m.total,
            "at_risk_paise": smart_m.at_risk_paise,
            "recovered": smart_m.recovered,
            "recovered_paise": smart_m.recovered_paise,
            "attempts": smart_m.attempts,
            "nudges": smart_m.nudges,
            "top_reasons": [{"reason": k, "count": v["count"]} for k, v in top],
        }
        text, source = llm.root_cause_brief(summary)
        print(f"== merchant brief ({source}) ==\n")
        print("  " + text.replace("\n", "\n  ") + "\n")

        sample = payments[0]
        copy, csource = llm.nudge_copy(sample, "low_balance_notice")
        print(f"== sample nudge ({csource}) ==\n  {copy}\n")


def _dump(m: Metrics) -> dict:
    return {
        "policy": m.policy,
        "total": m.total,
        "at_risk_paise": m.at_risk_paise,
        "recovered": m.recovered,
        "recovered_paise": m.recovered_paise,
        "attempts": m.attempts,
        "nudges": m.nudges,
        "wasted_attempts": m.wasted_attempts,
        "compliance_blocks": m.compliance_blocks,
        "compliance_stops": m.compliance_stops,
        "cost_paise": m.cost_paise,
        "net_paise": m.net_paise,
        "recovery_rate": round(m.recovery_rate, 4),
        "value_recovery_rate": round(m.value_recovery_rate, 4),
        "by_reason": m.by_reason,
    }


if __name__ == "__main__":
    main()
