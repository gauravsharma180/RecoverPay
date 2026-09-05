"""Deterministic recovery policy.

No model is called here and no model can override this. An LLM writes the
merchant explanation and the customer nudge copy; the decision of whether to
move money, when, and on which rail is a rule table you can read in one sitting
and unit test line by line.

Every branch returns a `reason_code`. That string is what lands in the audit
trail, so any rupee that moved can be traced back to the rule that moved it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .domain import (
    COMPLIANCE_BLOCKED,
    DEAD_ON_SAME_RAIL,
    ActionType,
    Decision,
    FailureReason,
    PaymentState,
    Rail,
)

# --- bounds -----------------------------------------------------------------

MAX_ATTEMPTS = 3               # charge attempts per payment, ever
MAX_NUDGES = 2                 # customer messages per payment, ever
RECOVERY_WINDOW_MIN = 72 * 60  # stop chasing after 72h
CUSTOMER_ATTEMPT_CAP = 5       # charge attempts per customer per day
CUSTOMER_NUDGE_CAP = 1         # messages per customer per day
HIGH_VALUE_PAISE = 2_500_000   # above this, no silent rail switch
QUIET_START_HOUR = 21          # no nudges 21:00 - 09:00 IST
QUIET_END_HOUR = 9
T0_HOUR = 9                    # simulation clock starts at 09:00


def minute_of_day(now_min: int) -> int:
    return (T0_HOUR * 60 + now_min) % 1440


def delay_until_hour(now_min: int, hour: int) -> int:
    """Minutes from now until the next occurrence of `hour`:00."""
    current = minute_of_day(now_min)
    target = hour * 60
    return (target - current) % 1440


def in_quiet_hours(now_min: int) -> bool:
    hour = minute_of_day(now_min) // 60
    return hour >= QUIET_START_HOUR or hour < QUIET_END_HOUR


ALTERNATE_RAILS: dict[Rail, list[Rail]] = {
    Rail.CARD: [Rail.UPI, Rail.NETBANKING],
    Rail.UPI: [Rail.CARD, Rail.NETBANKING],
    Rail.NETBANKING: [Rail.UPI, Rail.CARD],
    Rail.WALLET: [Rail.UPI, Rail.CARD],
}


@dataclass
class Budget:
    """Per-customer daily caps, shared across all their failed payments."""

    attempts: dict[tuple[str, int], int] = field(default_factory=dict)
    nudges: dict[tuple[str, int], int] = field(default_factory=dict)

    @staticmethod
    def _day(now_min: int) -> int:
        return (T0_HOUR * 60 + now_min) // 1440

    def can_attempt(self, customer_id: str, now_min: int) -> bool:
        key = (customer_id, self._day(now_min))
        return self.attempts.get(key, 0) < CUSTOMER_ATTEMPT_CAP

    def can_nudge(self, customer_id: str, now_min: int) -> bool:
        key = (customer_id, self._day(now_min))
        return self.nudges.get(key, 0) < CUSTOMER_NUDGE_CAP

    def record_attempt(self, customer_id: str, now_min: int) -> None:
        key = (customer_id, self._day(now_min))
        self.attempts[key] = self.attempts.get(key, 0) + 1

    def record_nudge(self, customer_id: str, now_min: int) -> None:
        key = (customer_id, self._day(now_min))
        self.nudges[key] = self.nudges.get(key, 0) + 1


class Policy:
    name = "base"

    def decide(self, state: PaymentState, now_min: int, budget: Budget) -> Decision:
        raise NotImplementedError


class NaiveRetryPolicy(Policy):
    """The baseline almost every merchant ships first.

    Retry the same rail immediately, three times, no questions asked. It is the
    honest comparison point because it is what a cron job plus a for-loop does.
    """

    name = "naive_retry"

    def decide(self, state: PaymentState, now_min: int, budget: Budget) -> Decision:
        if state.attempts >= 3:
            return Decision(ActionType.STOP, 0, None, "attempt_cap", "3 tries done")
        return Decision(
            ActionType.RETRY,
            delay_min=0,
            rail=state.payment.rail,
            reason_code="blind_retry",
            rationale="retry same rail immediately",
        )


class BackoffRetryPolicy(Policy):
    """Same rail every time, but spaced out instead of hammered back to back.

    This is the honest baseline, not `naive_retry`. It is what a competent
    team ships without a failure taxonomy: no reasoning about *why* the
    payment failed, no rail switching, no nudges, just exponential-ish
    backoff and the same hard bounds every policy in this file respects.
    """

    name = "backoff_retry"

    DELAYS = [5, 30, 120]  # minutes before attempt 1, 2, 3

    def decide(self, state: PaymentState, now_min: int, budget: Budget) -> Decision:
        p = state.payment
        reason = state.last_reason or p.failure_reason

        # --- gate 1: compliance. Same check, same reason_code as
        # SmartRecoveryPolicy - this baseline is guarded, naive_retry is not.
        if reason in COMPLIANCE_BLOCKED:
            return Decision(
                ActionType.STOP, 0, None, "compliance_blocked",
                "reason is on the never-retry list",
            )

        if state.attempts >= MAX_ATTEMPTS:
            return Decision(ActionType.STOP, 0, None, "attempt_cap",
                            f"{MAX_ATTEMPTS} charge attempts used")
        if now_min - p.failed_at_min > RECOVERY_WINDOW_MIN:
            return Decision(ActionType.STOP, 0, None, "window_expired",
                            "72h recovery window closed")
        if not budget.can_attempt(p.customer_id, now_min):
            return Decision(ActionType.STOP, 0, None, "customer_daily_cap",
                            "customer hit the daily attempt cap")

        delay = self.DELAYS[state.attempts]
        return Decision(
            ActionType.RETRY,
            delay_min=delay,
            rail=state.payment.rail,
            reason_code="backoff_wait",
            rationale=f"retry same rail in {delay}m, backoff attempt {state.attempts + 1}",
        )


class SmartRecoveryPolicy(Policy):
    """Reason-aware playbook with hard bounds."""

    name = "smart_recovery"

    def decide(self, state: PaymentState, now_min: int, budget: Budget) -> Decision:
        p = state.payment
        reason = state.last_reason or p.failure_reason

        # --- gate 1: compliance. Checked before anything else, and again in
        # the orchestrator before the charge actually fires.
        if reason in COMPLIANCE_BLOCKED:
            return Decision(
                ActionType.STOP, 0, None, "compliance_blocked",
                "reason is on the never-retry list",
            )

        # --- gate 2: hard bounds
        if state.attempts >= MAX_ATTEMPTS:
            return Decision(ActionType.STOP, 0, None, "attempt_cap",
                            f"{MAX_ATTEMPTS} charge attempts used")
        if now_min - p.failed_at_min > RECOVERY_WINDOW_MIN:
            return Decision(ActionType.STOP, 0, None, "window_expired",
                            "72h recovery window closed")
        if not budget.can_attempt(p.customer_id, now_min):
            return Decision(ActionType.STOP, 0, None, "customer_daily_cap",
                            "customer hit the daily attempt cap")

        playbook = getattr(self, f"_plan_{reason.value.lower()}", None)
        decision = playbook(state, now_min) if playbook else self._default(state, now_min)

        # --- gate 3: post-filters that apply to every branch
        return self._apply_guards(decision, state, now_min, budget)

    # ------------------------------------------------------------------ guards

    def _apply_guards(
        self, d: Decision, state: PaymentState, now_min: int, budget: Budget
    ) -> Decision:
        p = state.payment

        if d.action == ActionType.NUDGE:
            if state.nudges >= MAX_NUDGES or not budget.can_nudge(p.customer_id, now_min):
                return Decision(ActionType.STOP, 0, None, "nudge_cap",
                                "customer already messaged")
            # Never message someone at 3am. Push to the next morning instead.
            fire_at = now_min + d.delay_min
            if in_quiet_hours(fire_at):
                shift = delay_until_hour(fire_at, QUIET_END_HOUR)
                d = Decision(d.action, d.delay_min + shift, d.rail,
                             d.reason_code + "+quiet_hours",
                             d.rationale + ", deferred past quiet hours")

        if d.action == ActionType.SWITCH_RAIL:
            if p.amount_paise >= HIGH_VALUE_PAISE and state.nudges == 0:
                # Large ticket on a new instrument without telling anyone is
                # exactly how you turn a failed payment into a chargeback.
                return Decision(ActionType.NUDGE, d.delay_min, None,
                                "high_value_needs_consent",
                                "ticket above cap, ask before switching rail")
            if d.rail is None or d.rail in state.rails_tried:
                return Decision(ActionType.STOP, 0, None, "no_fresh_rail",
                                "all alternate rails tried")

        if d.action == ActionType.RETRY and (state.last_reason in DEAD_ON_SAME_RAIL):
            return Decision(ActionType.STOP, 0, None, "same_rail_dead",
                            "same rail cannot succeed for this reason")

        return d

    def _fresh_rail(self, state: PaymentState) -> Rail | None:
        for rail in ALTERNATE_RAILS.get(state.current_rail or state.payment.rail, []):
            if rail not in state.rails_tried:
                return rail
        return None

    # --------------------------------------------------------------- playbooks

    def _plan_gateway_timeout(self, state: PaymentState, now_min: int) -> Decision:
        # The one failure where an immediate retry is correct. Short backoff,
        # then a longer one, then give up rather than hammer the gateway.
        delays = [2, 20]
        if state.attempts < len(delays):
            return Decision(ActionType.RETRY, delays[state.attempts],
                            state.current_rail, "transient_backoff",
                            f"transient gateway error, retry in {delays[state.attempts]}m")
        return Decision(ActionType.STOP, 0, None, "transient_exhausted",
                        "two backoffs did not clear it")

    def _plan_issuer_down(self, state: PaymentState, now_min: int) -> Decision:
        if state.attempts == 0:
            return Decision(ActionType.RETRY, 50, state.current_rail,
                            "outage_wait", "issuer outage, wait for it to clear")
        rail = self._fresh_rail(state)
        if rail:
            return Decision(ActionType.SWITCH_RAIL, 20, rail, "route_around_issuer",
                            f"issuer still down, route via {rail.value}")
        return Decision(ActionType.RETRY, 180, state.current_rail, "outage_long_wait",
                        "no alternate rail, back off 3h")

    def _plan_insufficient_funds(self, state: PaymentState, now_min: int) -> Decision:
        # Money is not there. Retrying in 30 seconds cannot invent it.
        if state.nudges == 0:
            return Decision(ActionType.NUDGE, 30, None, "low_balance_notice",
                            "tell the customer before charging again")
        return Decision(ActionType.RETRY, delay_until_hour(now_min, 11) or 600,
                        state.current_rail, "retry_next_morning",
                        "retry next morning when balances top up")

    def _plan_upi_collect_expired(self, state: PaymentState, now_min: int) -> Decision:
        if state.nudges == 0:
            return Decision(ActionType.NUDGE, 5, None, "recollect_prompt",
                            "collect request expired, send a fresh one")
        return Decision(ActionType.RETRY, 25, state.current_rail, "recollect",
                        "re-issue collect after the prompt")

    def _plan_auth_3ds_dropoff(self, state: PaymentState, now_min: int) -> Decision:
        # Nobody abandoned 3DS because the retry was too slow.
        if state.nudges == 0:
            return Decision(ActionType.NUDGE, 10, None, "resume_auth",
                            "customer dropped at auth, send a resume link")
        rail = self._fresh_rail(state)
        if rail:
            return Decision(ActionType.SWITCH_RAIL, 45, rail, "offer_simpler_rail",
                            f"offer {rail.value} to skip the 3DS step")
        return Decision(ActionType.STOP, 0, None, "auth_exhausted", "no simpler rail left")

    def _plan_card_expired(self, state: PaymentState, now_min: int) -> Decision:
        if state.nudges == 0:
            return Decision(ActionType.NUDGE, 5, None, "update_instrument",
                            "card expired, ask for a new instrument")
        rail = self._fresh_rail(state)
        if rail:
            return Decision(ActionType.SWITCH_RAIL, 30, rail, "switch_after_expiry",
                            f"card is dead, collect on {rail.value}")
        return Decision(ActionType.STOP, 0, None, "expired_no_alternative",
                        "no usable instrument")

    def _plan_invalid_vpa(self, state: PaymentState, now_min: int) -> Decision:
        if state.nudges == 0:
            return Decision(ActionType.NUDGE, 5, None, "fix_vpa",
                            "VPA is wrong, ask the customer to correct it")
        rail = self._fresh_rail(state)
        if rail:
            return Decision(ActionType.SWITCH_RAIL, 20, rail, "switch_after_bad_vpa",
                            f"collect on {rail.value} instead")
        return Decision(ActionType.STOP, 0, None, "vpa_no_alternative", "no alternate rail")

    def _plan_do_not_honour(self, state: PaymentState, now_min: int) -> Decision:
        if state.attempts == 0:
            return Decision(ActionType.RETRY, 240, state.current_rail, "soft_decline_backoff",
                            "issuer soft decline, back off 4h")
        rail = self._fresh_rail(state)
        if rail:
            return Decision(ActionType.SWITCH_RAIL, 30, rail, "route_around_decline",
                            f"issuer keeps declining, try {rail.value}")
        return Decision(ActionType.STOP, 0, None, "decline_exhausted",
                        "issuer will not authorise this")

    def _plan_limit_exceeded(self, state: PaymentState, now_min: int) -> Decision:
        if state.attempts == 0:
            return Decision(ActionType.RETRY, delay_until_hour(now_min, 10) or 720,
                            state.current_rail, "wait_for_limit_reset",
                            "daily limit hit, retry after reset")
        rail = self._fresh_rail(state)
        if rail:
            return Decision(ActionType.SWITCH_RAIL, 15, rail, "other_instrument_limit",
                            f"limit is per instrument, try {rail.value}")
        return Decision(ActionType.STOP, 0, None, "limit_exhausted", "no headroom left")

    def _default(self, state: PaymentState, now_min: int) -> Decision:
        # Unknown reason. Do the cautious thing, once, and log it loudly so the
        # taxonomy gets a new entry instead of silently accumulating retries.
        if state.attempts == 0:
            return Decision(ActionType.RETRY, 60, state.current_rail, "unknown_reason_backoff",
                            "unmapped failure reason, single cautious retry")
        return Decision(ActionType.STOP, 0, None, "unknown_reason_stop",
                        "unmapped failure reason, not retrying further")
