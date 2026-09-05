"""Guardrail tests. These are the rules that must not regress."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.domain import ActionType, FailureReason, Payment, PaymentState, Rail
from src.environment import Environment, generate_batch
from src.orchestrator import run
from src.policy import (
    CUSTOMER_ATTEMPT_CAP,
    MAX_ATTEMPTS,
    BackoffRetryPolicy,
    Budget,
    NaiveRetryPolicy,
    SmartRecoveryPolicy,
    delay_until_hour,
    in_quiet_hours,
)


def make(reason, amount_paise=50_000, rail=Rail.CARD, failed_at_min=0):
    return Payment(
        payment_id="pay_test", customer_id="cust_1", merchant_id="acc_1",
        amount_paise=amount_paise, rail=rail, issuer="HDFC",
        raw_code="X", failure_reason=reason, failed_at_min=failed_at_min,
    )


@pytest.fixture
def policy():
    return SmartRecoveryPolicy()


def test_stolen_card_never_retried(policy):
    st = PaymentState(payment=make(FailureReason.STOLEN_CARD))
    d = policy.decide(st, 0, Budget())
    assert d.action is ActionType.STOP
    assert d.reason_code == "compliance_blocked"


def test_compliance_holds_even_at_execution():
    """A policy that ignores the gate must still not move money."""

    class RoguePolicy(NaiveRetryPolicy):
        name = "rogue"

    payments = [make(FailureReason.STOLEN_CARD)]
    m, events = run(payments, RoguePolicy(), Environment(seed=1))
    assert m.compliance_stops == 1
    assert m.attempts == 0
    assert any(e.outcome == "blocked" for e in events)


def test_attempt_cap_enforced(policy):
    st = PaymentState(payment=make(FailureReason.GATEWAY_TIMEOUT))
    st.attempts = MAX_ATTEMPTS
    d = policy.decide(st, 0, Budget())
    assert d.action is ActionType.STOP
    assert d.reason_code == "attempt_cap"


def test_expired_card_is_never_retried_on_same_rail(policy):
    st = PaymentState(payment=make(FailureReason.CARD_EXPIRED))
    st.nudges = 1  # already messaged, so the playbook wants a rail switch
    d = policy.decide(st, 0, Budget())
    assert d.action is not ActionType.RETRY
    if d.action is ActionType.SWITCH_RAIL:
        assert d.rail is not Rail.CARD


def test_low_balance_messages_before_charging_again(policy):
    st = PaymentState(payment=make(FailureReason.INSUFFICIENT_FUNDS))
    d = policy.decide(st, 0, Budget())
    assert d.action is ActionType.NUDGE


def test_high_value_needs_consent_before_rail_switch(policy):
    st = PaymentState(payment=make(FailureReason.ISSUER_DOWN, amount_paise=9_000_000))
    st.attempts = 1  # playbook would switch rails here
    d = policy.decide(st, 60, Budget())
    assert d.reason_code == "high_value_needs_consent"
    assert d.action is ActionType.NUDGE


def test_nudges_are_deferred_out_of_quiet_hours(policy):
    # t0 is 09:00, so minute 780 is 22:00.
    st = PaymentState(payment=make(FailureReason.UPI_COLLECT_EXPIRED,
                                   rail=Rail.UPI, failed_at_min=780))
    d = policy.decide(st, 780, Budget())
    assert d.action is ActionType.NUDGE
    assert not in_quiet_hours(780 + d.delay_min)
    assert "quiet_hours" in d.reason_code


def test_customer_daily_attempt_cap(policy):
    budget = Budget()
    for _ in range(CUSTOMER_ATTEMPT_CAP):
        budget.record_attempt("cust_1", 0)
    st = PaymentState(payment=make(FailureReason.GATEWAY_TIMEOUT))
    d = policy.decide(st, 0, budget)
    assert d.reason_code == "customer_daily_cap"


def test_recovery_window_closes(policy):
    st = PaymentState(payment=make(FailureReason.GATEWAY_TIMEOUT))
    d = policy.decide(st, 73 * 60, Budget())
    assert d.reason_code == "window_expired"


def test_delay_until_hour_wraps_forward():
    # minute 0 is 09:00, so the next 10:00 is 60 minutes away
    assert delay_until_hour(0, 10) == 60
    # and the next 08:00 is 23 hours away
    assert delay_until_hour(0, 8) == 23 * 60


def test_smart_policy_beats_baseline_on_net_value():
    payments = generate_batch(400, seed=3)
    base, _ = run(payments, NaiveRetryPolicy(), Environment(seed=4))
    smart, _ = run(payments, SmartRecoveryPolicy(), Environment(seed=4))
    assert smart.net_paise > base.net_paise
    assert smart.attempts < base.attempts
    assert smart.compliance_stops == 0


def test_every_event_carries_a_reason_code():
    payments = generate_batch(120, seed=5)
    _, events = run(payments, SmartRecoveryPolicy(), Environment(seed=6))
    assert events
    assert all(e.reason_code for e in events)
    assert all(e.at_min >= 0 for e in events)


def test_backoff_beats_naive_on_attempts_per_recovery():
    payments = generate_batch(400, seed=3)
    naive, _ = run(payments, NaiveRetryPolicy(), Environment(seed=4))
    backoff, _ = run(payments, BackoffRetryPolicy(), Environment(seed=4))
    assert backoff.attempts_per_recovery < naive.attempts_per_recovery


def test_smart_beats_backoff_on_net_value():
    payments = generate_batch(400, seed=3)
    backoff, _ = run(payments, BackoffRetryPolicy(), Environment(seed=4))
    smart, _ = run(payments, SmartRecoveryPolicy(), Environment(seed=4))
    assert smart.net_paise > backoff.net_paise


def test_backoff_is_guarded_and_naive_is_not():
    """The two baselines must differ deliberately, not by accident.

    backoff_retry is meant to be the honest baseline: same lack of reasoning
    as naive_retry, but guarded by the same compliance gate as smart_recovery.
    If this ever regresses, the two baselines collapse into the same thing
    and the vs-backoff comparison stops meaning anything.
    """
    payments = generate_batch(400, seed=3)
    naive, _ = run(payments, NaiveRetryPolicy(), Environment(seed=4))
    backoff, _ = run(payments, BackoffRetryPolicy(), Environment(seed=4))
    assert backoff.compliance_stops == 0
    assert naive.compliance_stops > 0
