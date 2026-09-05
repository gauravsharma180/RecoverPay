"""Core domain types for the recovery orchestrator.

Everything here is plain data. No business rules, no randomness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Rail(str, Enum):
    CARD = "card"
    UPI = "upi"
    NETBANKING = "netbanking"
    WALLET = "wallet"


class FailureReason(str, Enum):
    """Normalised failure taxonomy.

    Gateways emit a long tail of raw codes. We map them onto ten buckets that
    actually change what you should do next. The mapping lives in
    `RAW_CODE_MAP` so a new gateway code never changes policy code.
    """

    ISSUER_DOWN = "ISSUER_DOWN"
    GATEWAY_TIMEOUT = "GATEWAY_TIMEOUT"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    UPI_COLLECT_EXPIRED = "UPI_COLLECT_EXPIRED"
    AUTH_3DS_DROPOFF = "AUTH_3DS_DROPOFF"
    CARD_EXPIRED = "CARD_EXPIRED"
    DO_NOT_HONOUR = "DO_NOT_HONOUR"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    INVALID_VPA = "INVALID_VPA"
    STOLEN_CARD = "STOLEN_CARD"


RAW_CODE_MAP: dict[str, FailureReason] = {
    "BANK_DOWN": FailureReason.ISSUER_DOWN,
    "ISSUER_UNAVAILABLE": FailureReason.ISSUER_DOWN,
    "GATEWAY_ERROR": FailureReason.GATEWAY_TIMEOUT,
    "TIMEOUT": FailureReason.GATEWAY_TIMEOUT,
    "INSUFFICIENT_FUNDS": FailureReason.INSUFFICIENT_FUNDS,
    "51": FailureReason.INSUFFICIENT_FUNDS,
    "COLLECT_EXPIRED": FailureReason.UPI_COLLECT_EXPIRED,
    "U69": FailureReason.UPI_COLLECT_EXPIRED,
    "3DS_ABANDONED": FailureReason.AUTH_3DS_DROPOFF,
    "AUTH_NOT_COMPLETED": FailureReason.AUTH_3DS_DROPOFF,
    "54": FailureReason.CARD_EXPIRED,
    "EXPIRED_CARD": FailureReason.CARD_EXPIRED,
    "05": FailureReason.DO_NOT_HONOUR,
    "DO_NOT_HONOUR": FailureReason.DO_NOT_HONOUR,
    "61": FailureReason.LIMIT_EXCEEDED,
    "INVALID_VPA": FailureReason.INVALID_VPA,
    "43": FailureReason.STOLEN_CARD,
    "STOLEN_CARD": FailureReason.STOLEN_CARD,
}


# Reasons where any further automated charge attempt is not allowed,
# regardless of what a model might suggest. Hard gate, checked twice.
COMPLIANCE_BLOCKED: frozenset[FailureReason] = frozenset(
    {FailureReason.STOLEN_CARD}
)

# Reasons where retrying the *same* rail can never work. Retrying these is
# pure cost: it burns gateway fees and annoys the issuer's risk engine.
DEAD_ON_SAME_RAIL: frozenset[FailureReason] = frozenset(
    {
        FailureReason.CARD_EXPIRED,
        FailureReason.INVALID_VPA,
        FailureReason.STOLEN_CARD,
    }
)


class ActionType(str, Enum):
    RETRY = "retry"              # same rail, later
    SWITCH_RAIL = "switch_rail"  # different rail
    NUDGE = "nudge"              # ask the customer to act
    STOP = "stop"                # give up, with a reason code


@dataclass(frozen=True)
class Payment:
    payment_id: str
    customer_id: str
    merchant_id: str
    amount_paise: int
    rail: Rail
    issuer: str
    raw_code: str
    failure_reason: FailureReason
    failed_at_min: int  # minutes since simulation t0

    @property
    def amount_rupees(self) -> float:
        return self.amount_paise / 100.0


@dataclass
class PaymentState:
    """Mutable per-payment recovery state owned by the orchestrator."""

    payment: Payment
    attempts: int = 0
    nudges: int = 0
    rails_tried: set[Rail] = field(default_factory=set)
    current_rail: Optional[Rail] = None
    last_reason: Optional[FailureReason] = None
    last_action_min: int = 0
    nudge_pending: bool = False
    recovered: bool = False
    recovered_at_min: Optional[int] = None
    stop_code: Optional[str] = None

    def __post_init__(self) -> None:
        if self.current_rail is None:
            self.current_rail = self.payment.rail
        if self.last_reason is None:
            self.last_reason = self.payment.failure_reason
        self.rails_tried.add(self.payment.rail)
        self.last_action_min = self.payment.failed_at_min


@dataclass(frozen=True)
class Decision:
    action: ActionType
    delay_min: int
    rail: Optional[Rail]
    reason_code: str          # machine readable, drives the audit trail
    rationale: str            # short deterministic string, never model output
    expected_gain: float = 0.0


@dataclass
class Event:
    """One line of the audit trail."""

    at_min: int
    payment_id: str
    action: str
    rail: Optional[str]
    reason_code: str
    rationale: str
    outcome: str
    amount_paise: int
    cost_paise: int
