"""Synthetic batch generator + the hidden recovery environment.

The environment holds ground truth the policy never sees. That separation is
the whole point: if the policy could read the recovery curve it would score
perfectly and the number would mean nothing.

Recovery probability for one attempt:

    p = base
        + wait_gain  * (1 - exp(-delay / tau))     # outages heal over time
        + rail_gain   if the attempt uses a fresh rail
        + nudge_gain  if the customer was nudged and acted
        - decay * (attempts_already_made)

clamped to [0, 0.95]. Terminal reasons are hard zero.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .domain import (
    COMPLIANCE_BLOCKED,
    RAW_CODE_MAP,
    FailureReason,
    Payment,
    Rail,
)


@dataclass(frozen=True)
class RecoveryProfile:
    base: float          # p(success) on an immediate same-rail retry
    wait_gain: float     # extra p available by waiting
    tau_min: float       # minutes to reach ~63% of wait_gain
    rail_gain: float     # extra p from moving to an untried rail
    nudge_gain: float    # extra p if the customer acted on a nudge
    decay: float         # penalty per attempt already burned


# Tuned to be directionally honest, not to flatter the policy.
PROFILES: dict[FailureReason, RecoveryProfile] = {
    # Issuer outage: retrying now is near useless, retrying in an hour works.
    FailureReason.ISSUER_DOWN: RecoveryProfile(0.04, 0.62, 40, 0.30, 0.00, 0.02),
    # Transient gateway blip: the one case where an immediate retry is right.
    FailureReason.GATEWAY_TIMEOUT: RecoveryProfile(0.55, 0.18, 5, 0.05, 0.00, 0.05),
    # Low balance: time is the only real lever, a nudge helps a little.
    FailureReason.INSUFFICIENT_FUNDS: RecoveryProfile(0.05, 0.34, 900, 0.06, 0.18, 0.03),
    # Collect request timed out: re-collecting after a nudge is the fix.
    FailureReason.UPI_COLLECT_EXPIRED: RecoveryProfile(0.12, 0.10, 30, 0.08, 0.45, 0.04),
    # Customer abandoned 3DS. Silent retries do nothing, nudges do everything.
    FailureReason.AUTH_3DS_DROPOFF: RecoveryProfile(0.06, 0.05, 60, 0.10, 0.50, 0.04),
    # Card is expired. Only a new instrument works.
    FailureReason.CARD_EXPIRED: RecoveryProfile(0.00, 0.00, 1, 0.42, 0.30, 0.00),
    # Issuer soft decline. Throttled retries recover a slice.
    FailureReason.DO_NOT_HONOUR: RecoveryProfile(0.06, 0.16, 240, 0.22, 0.05, 0.06),
    # Daily limit. Resets overnight.
    FailureReason.LIMIT_EXCEEDED: RecoveryProfile(0.02, 0.55, 600, 0.25, 0.05, 0.02),
    # Wrong VPA. Same rail is dead, customer has to fix it.
    FailureReason.INVALID_VPA: RecoveryProfile(0.00, 0.00, 1, 0.20, 0.48, 0.00),
    # Never retryable.
    FailureReason.STOLEN_CARD: RecoveryProfile(0.00, 0.00, 1, 0.00, 0.00, 0.00),
}

# Share of the batch per reason. Roughly mirrors a real Indian PA failure mix:
# timeouts and low balance dominate, fraud is a thin tail.
MIX: list[tuple[FailureReason, float]] = [
    (FailureReason.INSUFFICIENT_FUNDS, 0.22),
    (FailureReason.GATEWAY_TIMEOUT, 0.16),
    (FailureReason.UPI_COLLECT_EXPIRED, 0.15),
    (FailureReason.AUTH_3DS_DROPOFF, 0.12),
    (FailureReason.ISSUER_DOWN, 0.11),
    (FailureReason.DO_NOT_HONOUR, 0.09),
    (FailureReason.LIMIT_EXCEEDED, 0.06),
    (FailureReason.CARD_EXPIRED, 0.05),
    (FailureReason.INVALID_VPA, 0.03),
    (FailureReason.STOLEN_CARD, 0.01),
]

RAIL_BY_REASON: dict[FailureReason, list[Rail]] = {
    FailureReason.UPI_COLLECT_EXPIRED: [Rail.UPI],
    FailureReason.INVALID_VPA: [Rail.UPI],
    FailureReason.CARD_EXPIRED: [Rail.CARD],
    FailureReason.AUTH_3DS_DROPOFF: [Rail.CARD],
    FailureReason.STOLEN_CARD: [Rail.CARD],
}

ISSUERS = ["HDFC", "ICICI", "SBI", "AXIS", "KOTAK", "PAYTM", "YESB", "IDFC"]

REVERSE_RAW: dict[FailureReason, list[str]] = {}
for raw, reason in RAW_CODE_MAP.items():
    REVERSE_RAW.setdefault(reason, []).append(raw)


def generate_batch(n: int = 500, seed: int = 7) -> list[Payment]:
    """Generate `n` failed payments spread over a 6 hour ingestion window."""
    rng = random.Random(seed)
    reasons, weights = zip(*MIX)
    payments: list[Payment] = []

    for i in range(n):
        reason = rng.choices(reasons, weights=weights, k=1)[0]
        rail = rng.choice(RAIL_BY_REASON.get(reason, list(Rail)))
        # Log-normal-ish ticket sizes: lots of small, a few large.
        amount_paise = int(min(max(rng.lognormvariate(6.6, 1.1), 49), 90_000) * 100)
        payments.append(
            Payment(
                payment_id=f"pay_{i:04d}",
                customer_id=f"cust_{rng.randint(0, n // 3):04d}",
                merchant_id=f"acc_{rng.randint(1, 12):02d}",
                amount_paise=amount_paise,
                rail=rail,
                issuer=rng.choice(ISSUERS),
                raw_code=rng.choice(REVERSE_RAW[reason]),
                failure_reason=reason,
                failed_at_min=rng.randint(0, 360),
            )
        )
    return payments


class Environment:
    """Ground truth. Only the orchestrator touches this, never the policy."""

    def __init__(self, seed: int = 11) -> None:
        self._rng = random.Random(seed)
        # Does this customer actually act on a nudge? Fixed per customer so the
        # same person is not randomly cooperative on every attempt.
        self._responsive: dict[str, bool] = {}

    def customer_responds(self, customer_id: str) -> bool:
        if customer_id not in self._responsive:
            self._responsive[customer_id] = self._rng.random() < 0.42
        return self._responsive[customer_id]

    def attempt(
        self,
        payment: Payment,
        *,
        delay_min: int,
        rail: Rail,
        fresh_rail: bool,
        nudged: bool,
        prior_attempts: int,
    ) -> tuple[bool, FailureReason]:
        """Return (succeeded, failure_reason_if_failed)."""
        reason = payment.failure_reason
        if reason in COMPLIANCE_BLOCKED:
            return False, reason

        prof = PROFILES[reason]
        p = prof.base
        p += prof.wait_gain * (1 - math.exp(-max(delay_min, 0) / prof.tau_min))
        if fresh_rail:
            p += prof.rail_gain
        if nudged and self.customer_responds(payment.customer_id):
            p += prof.nudge_gain
        p -= prof.decay * prior_attempts

        # Very large tickets fail slightly more often on retry (risk rules).
        if payment.amount_paise > 2_000_000:
            p *= 0.85

        p = max(0.0, min(p, 0.95))
        if self._rng.random() < p:
            return True, reason
        return False, reason
