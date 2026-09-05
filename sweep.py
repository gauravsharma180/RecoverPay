#!/usr/bin/env python3
"""Run the comparison across many seeds.

One good batch proves nothing. This runs N independent draws of both the
payment mix and the environment, and reports the spread of the net lift.
"""

from __future__ import annotations

import argparse
import statistics

from src.environment import Environment, generate_batch
from src.orchestrator import run
from src.policy import BackoffRetryPolicy, NaiveRetryPolicy, SmartRecoveryPolicy


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--n", type=int, default=500)
    args = ap.parse_args()

    lifts_naive, lifts_backoff = [], []
    rates_naive, rates_backoff = [], []
    attempt_ratios_naive, attempt_ratios_backoff = [], []
    compliance_stops = 0

    for seed in range(args.runs):
        payments = generate_batch(args.n, seed=seed)
        env_seed = 1000 + seed
        base, _ = run(payments, NaiveRetryPolicy(), Environment(seed=env_seed))
        backoff, _ = run(payments, BackoffRetryPolicy(), Environment(seed=env_seed))
        smart, _ = run(payments, SmartRecoveryPolicy(), Environment(seed=env_seed))

        lifts_naive.append((smart.net_paise - base.net_paise) / base.net_paise * 100)
        lifts_backoff.append((smart.net_paise - backoff.net_paise) / backoff.net_paise * 100)
        rates_naive.append((smart.recovery_rate - base.recovery_rate) * 100)
        rates_backoff.append((smart.recovery_rate - backoff.recovery_rate) * 100)
        attempt_ratios_naive.append(smart.attempts / base.attempts)
        attempt_ratios_backoff.append(smart.attempts / backoff.attempts)
        compliance_stops += smart.compliance_stops

    print(f"\n  {args.runs} runs x {args.n} payments\n")

    print("  -- smart vs naive retry --")
    print(f"  net value lift        mean {statistics.mean(lifts_naive):+6.1f}%   "
          f"min {min(lifts_naive):+6.1f}%   max {max(lifts_naive):+6.1f}%   "
          f"sd {statistics.pstdev(lifts_naive):5.1f}")
    print(f"  recovery rate gain    mean {statistics.mean(rates_naive):+6.1f} pts  "
          f"min {min(rates_naive):+6.1f}    max {max(rates_naive):+6.1f}")
    print(f"  attempts vs baseline  mean {statistics.mean(attempt_ratios_naive):6.2f}x")
    print(f"  worse than naive in {sum(1 for x in lifts_naive if x <= 0)} of {args.runs} runs\n")

    print("  -- smart vs backoff retry (the honest baseline) --")
    print(f"  net value lift        mean {statistics.mean(lifts_backoff):+6.1f}%   "
          f"min {min(lifts_backoff):+6.1f}%   max {max(lifts_backoff):+6.1f}%   "
          f"sd {statistics.pstdev(lifts_backoff):5.1f}")
    print(f"  recovery rate gain    mean {statistics.mean(rates_backoff):+6.1f} pts  "
          f"min {min(rates_backoff):+6.1f}    max {max(rates_backoff):+6.1f}")
    print(f"  attempts vs baseline  mean {statistics.mean(attempt_ratios_backoff):6.2f}x")
    print(f"  worse than backoff in {sum(1 for x in lifts_backoff if x <= 0)} of {args.runs} runs\n")

    # Smart's own policy already refuses these before any charge is attempted,
    # so this should read 0: it counts attempts the second gate had to catch
    # at execution, not attempts that actually charged a blocked payment.
    print(f"  compliance-gate stops, smart, execution-time (want 0): {compliance_stops}\n")


if __name__ == "__main__":
    main()
