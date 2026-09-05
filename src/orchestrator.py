"""Runs a batch of failed payments through a policy and measures the outcome.

The orchestrator is deliberately dumb. It advances a virtual clock, asks the
policy what to do, re-checks the compliance gate, calls the environment, and
appends to the audit trail. All judgement lives in `policy.py`.
"""

from __future__ import annotations

import heapq
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .domain import (
    COMPLIANCE_BLOCKED,
    ActionType,
    Event,
    FailureReason,
    Payment,
    PaymentState,
    Rail,
)
from .environment import Environment
from .policy import RECOVERY_WINDOW_MIN, Budget, Policy

ATTEMPT_COST_PAISE = 250  # gateway + platform cost of one charge attempt
NUDGE_COST_PAISE = 25     # one WhatsApp / SMS
MAX_STEPS_PER_PAYMENT = 12  # loop fuse


@dataclass
class Metrics:
    policy: str
    total: int = 0
    at_risk_paise: int = 0
    recovered: int = 0
    recovered_paise: int = 0
    attempts: int = 0
    nudges: int = 0
    wasted_attempts: int = 0       # charges that could not have worked
    compliance_blocks: int = 0     # policy itself refused to charge (gate 1)
    compliance_stops: int = 0      # policy tried to charge anyway; the second
                                    # gate caught it at execution before any
                                    # money moved. Blocked, not charged.
    by_reason: dict = field(default_factory=dict)

    @property
    def cost_paise(self) -> int:
        return self.attempts * ATTEMPT_COST_PAISE + self.nudges * NUDGE_COST_PAISE

    @property
    def net_paise(self) -> int:
        return self.recovered_paise - self.cost_paise

    @property
    def recovery_rate(self) -> float:
        return self.recovered / self.total if self.total else 0.0

    @property
    def value_recovery_rate(self) -> float:
        return self.recovered_paise / self.at_risk_paise if self.at_risk_paise else 0.0

    @property
    def attempts_per_recovery(self) -> float:
        return self.attempts / self.recovered if self.recovered else float("inf")


def run(
    payments: list[Payment],
    policy: Policy,
    env: Environment,
) -> tuple[Metrics, list[Event]]:
    states = {p.payment_id: PaymentState(payment=p) for p in payments}
    budget = Budget()
    events: list[Event] = []
    steps: Counter[str] = Counter()

    heap: list[tuple[int, int, str]] = []
    for i, p in enumerate(payments):
        heapq.heappush(heap, (p.failed_at_min, i, p.payment_id))
    seq = len(payments)

    m = Metrics(policy=policy.name, total=len(payments))
    m.at_risk_paise = sum(p.amount_paise for p in payments)
    reason_stats: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "recovered": 0, "at_risk_paise": 0,
                 "recovered_paise": 0, "attempts": 0}
    )
    for p in payments:
        r = reason_stats[p.failure_reason.value]
        r["count"] += 1
        r["at_risk_paise"] += p.amount_paise

    def log(at, st, action, rail, code, why, outcome, cost=0):
        events.append(Event(
            at_min=at, payment_id=st.payment.payment_id, action=action,
            rail=rail.value if rail else None, reason_code=code, rationale=why,
            outcome=outcome, amount_paise=st.payment.amount_paise, cost_paise=cost,
        ))

    while heap:
        now, _, pid = heapq.heappop(heap)
        st = states[pid]
        if st.recovered or st.stop_code:
            continue
        steps[pid] += 1
        if steps[pid] > MAX_STEPS_PER_PAYMENT:
            st.stop_code = "step_fuse"
            log(now, st, "stop", None, "step_fuse", "loop fuse tripped", "stopped")
            continue

        d = policy.decide(st, now, budget)

        if d.action == ActionType.STOP:
            st.stop_code = d.reason_code
            if d.reason_code == "compliance_blocked":
                m.compliance_blocks += 1
            log(now, st, "stop", None, d.reason_code, d.rationale, "stopped")
            continue

        fire_at = now + d.delay_min
        if fire_at - st.payment.failed_at_min > RECOVERY_WINDOW_MIN:
            st.stop_code = "window_expired"
            log(now, st, "stop", None, "window_expired",
                "action would land outside the 72h window", "stopped")
            continue

        if d.action == ActionType.NUDGE:
            st.nudges += 1
            st.nudge_pending = True
            budget.record_nudge(st.payment.customer_id, fire_at)
            m.nudges += 1
            log(fire_at, st, "nudge", None, d.reason_code, d.rationale,
                "sent", NUDGE_COST_PAISE)
            seq += 1
            heapq.heappush(heap, (fire_at, seq, pid))
            continue

        # --- charge attempt path -------------------------------------------
        # Second compliance gate. The policy already checked; this catches a
        # buggy or swapped-in policy before money moves.
        if st.payment.failure_reason in COMPLIANCE_BLOCKED:
            m.compliance_stops += 1
            st.stop_code = "compliance_blocked_at_execution"
            log(fire_at, st, "stop", None, "compliance_blocked_at_execution",
                "policy tried to charge a blocked payment", "blocked")
            continue

        rail: Rail = d.rail or st.current_rail or st.payment.rail
        fresh = rail not in st.rails_tried
        nudged = st.nudge_pending

        ok, new_reason = env.attempt(
            st.payment,
            delay_min=fire_at - st.last_action_min,
            rail=rail,
            fresh_rail=fresh,
            nudged=nudged,
            prior_attempts=st.attempts,
        )

        st.attempts += 1
        m.attempts += 1
        reason_stats[st.payment.failure_reason.value]["attempts"] += 1
        budget.record_attempt(st.payment.customer_id, fire_at)
        st.rails_tried.add(rail)
        st.current_rail = rail
        st.last_action_min = fire_at
        st.nudge_pending = False

        # An attempt that had no path to success. Counted separately because
        # "we recovered money" means nothing if you burned the margin doing it.
        if not fresh and st.payment.failure_reason in (
            FailureReason.CARD_EXPIRED, FailureReason.INVALID_VPA,
        ):
            m.wasted_attempts += 1

        if ok:
            st.recovered = True
            st.recovered_at_min = fire_at
            m.recovered += 1
            m.recovered_paise += st.payment.amount_paise
            rs = reason_stats[st.payment.failure_reason.value]
            rs["recovered"] += 1
            rs["recovered_paise"] += st.payment.amount_paise
            log(fire_at, st, d.action.value, rail, d.reason_code, d.rationale,
                "captured", ATTEMPT_COST_PAISE)
            continue

        st.last_reason = new_reason
        log(fire_at, st, d.action.value, rail, d.reason_code, d.rationale,
            "failed", ATTEMPT_COST_PAISE)
        seq += 1
        heapq.heappush(heap, (fire_at, seq, pid))

    m.by_reason = dict(reason_stats)
    return m, events
