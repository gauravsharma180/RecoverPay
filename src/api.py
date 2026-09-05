"""HTTP surface.

  GET  /reasons        the failure taxonomy, so the UI is server-driven
  POST /decide         what would the engine do with this failure, and why
  POST /recover/batch  run a batch, get the money numbers and per-payment outcomes
  GET  /audit/{id}     every action taken on one payment, in order

`/decide` is the one that matters in a demo. It is a dry run: it returns the
decision and the rule that produced it without moving any money, so a merchant
ops person can interrogate the policy before trusting it.

If `web/dist` exists, the built UI is served from `/`.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import llm
from .domain import RAW_CODE_MAP, FailureReason, Payment, PaymentState, Rail
from .environment import Environment, generate_batch
from .orchestrator import ATTEMPT_COST_PAISE, NUDGE_COST_PAISE, run
from .policy import (
    CUSTOMER_ATTEMPT_CAP,
    HIGH_VALUE_PAISE,
    MAX_ATTEMPTS,
    MAX_NUDGES,
    RECOVERY_WINDOW_MIN,
    BackoffRetryPolicy,
    Budget,
    NaiveRetryPolicy,
    SmartRecoveryPolicy,
)

app = FastAPI(title="RecoverPay", version="0.2.0")

# The dev UI runs on 5173. In production the UI is served from this same
# origin, so this only matters while developing.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_audit: dict[str, dict[str, list]] = {"smart": {}, "naive": {}, "backoff": {}}

# Plain-language labels. The UI shows these, never the enum names.
REASON_LABELS: dict[FailureReason, str] = {
    FailureReason.ISSUER_DOWN: "Bank was down",
    FailureReason.GATEWAY_TIMEOUT: "Gateway timed out",
    FailureReason.INSUFFICIENT_FUNDS: "Not enough balance",
    FailureReason.UPI_COLLECT_EXPIRED: "UPI request expired",
    FailureReason.AUTH_3DS_DROPOFF: "Left during bank verification",
    FailureReason.CARD_EXPIRED: "Card expired",
    FailureReason.DO_NOT_HONOUR: "Bank declined",
    FailureReason.LIMIT_EXCEEDED: "Daily limit reached",
    FailureReason.INVALID_VPA: "Wrong UPI ID",
    FailureReason.STOLEN_CARD: "Card reported stolen",
}

ACTION_LABELS = {
    "retry": "Retry same method",
    "switch_rail": "Try another method",
    "nudge": "Message the customer",
    "stop": "Stop",
}


class FailureIn(BaseModel):
    payment_id: str = "pay_demo"
    customer_id: str = "cust_demo"
    merchant_id: str = "acc_01"
    amount_paise: int = Field(gt=0, default=250_000)
    rail: Rail = Rail.CARD
    issuer: str = "HDFC"
    raw_code: str = "51"
    attempts_so_far: int = 0
    nudges_so_far: int = 0
    minutes_since_failure: int = 0
    with_copy: bool = False


class BatchIn(BaseModel):
    n: int = Field(gt=0, le=20_000, default=500)
    seed: int = 7


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/reasons")
def reasons() -> dict:
    """Taxonomy plus the bounds, so the UI never hardcodes policy constants."""
    return {
        "reasons": [
            {"code": r.value, "label": REASON_LABELS[r],
             "raw_codes": [k for k, v in RAW_CODE_MAP.items() if v is r]}
            for r in FailureReason
        ],
        "rails": [r.value for r in Rail],
        "limits": {
            "max_attempts": MAX_ATTEMPTS,
            "max_nudges": MAX_NUDGES,
            "customer_attempt_cap": CUSTOMER_ATTEMPT_CAP,
            "window_hours": RECOVERY_WINDOW_MIN // 60,
            "high_value_paise": HIGH_VALUE_PAISE,
            "attempt_cost_paise": ATTEMPT_COST_PAISE,
            "nudge_cost_paise": NUDGE_COST_PAISE,
        },
    }


@app.post("/decide")
def decide(body: FailureIn) -> dict:
    reason = RAW_CODE_MAP.get(body.raw_code.upper())
    if reason is None:
        try:
            reason = FailureReason(body.raw_code.upper())
        except ValueError:
            raise HTTPException(422, f"unmapped failure code: {body.raw_code}")

    payment = Payment(
        payment_id=body.payment_id, customer_id=body.customer_id,
        merchant_id=body.merchant_id, amount_paise=body.amount_paise,
        rail=body.rail, issuer=body.issuer, raw_code=body.raw_code,
        failure_reason=reason, failed_at_min=0,
    )
    state = PaymentState(payment=payment)
    state.attempts = body.attempts_so_far
    state.nudges = body.nudges_so_far

    d = SmartRecoveryPolicy().decide(state, body.minutes_since_failure, Budget())
    naive = NaiveRetryPolicy().decide(state, body.minutes_since_failure, Budget())

    out = {
        "payment_id": payment.payment_id,
        "reason_code": reason.value,
        "reason_label": REASON_LABELS[reason],
        "action": d.action.value,
        "action_label": ACTION_LABELS[d.action.value],
        "delay_minutes": d.delay_min,
        "rail": d.rail.value if d.rail else None,
        "rule": d.reason_code,
        "rationale": d.rationale,
        "baseline_action": naive.action.value,
        "baseline_label": ACTION_LABELS[naive.action.value],
        "dry_run": True,
    }
    if body.with_copy and d.action.value == "nudge":
        copy, source = llm.nudge_copy(payment, d.reason_code)
        out["customer_message"] = copy
        out["message_source"] = source
    return out


@app.post("/recover/batch")
def recover_batch(body: BatchIn) -> dict:
    payments = generate_batch(body.n, seed=body.seed)
    env_seed = body.seed + 1

    smart, smart_events = run(payments, SmartRecoveryPolicy(), Environment(seed=env_seed))
    naive, naive_events = run(payments, NaiveRetryPolicy(), Environment(seed=env_seed))
    backoff, backoff_events = run(payments, BackoffRetryPolicy(), Environment(seed=env_seed))

    for name, events in (
        ("smart", smart_events), ("naive", naive_events), ("backoff", backoff_events),
    ):
        _audit[name].clear()
        for e in events:
            _audit[name].setdefault(e.payment_id, []).append(asdict(e))

    def won(events: list) -> set:
        return {e.payment_id for e in events if e.outcome == "captured"}

    won_smart, won_naive = won(smart_events), won(naive_events)
    won_backoff = won(backoff_events)

    # One row per payment. The UI draws every single one, so the panel sees the
    # whole batch rather than a summary of it.
    grid = [
        {
            "id": p.payment_id,
            "amount_paise": p.amount_paise,
            "reason": p.failure_reason.value,
            "label": REASON_LABELS[p.failure_reason],
            "smart": p.payment_id in won_smart,
            "naive": p.payment_id in won_naive,
            "backoff": p.payment_id in won_backoff,
        }
        for p in payments
    ]

    def summary(m) -> dict:
        return {
            "policy": m.policy,
            "recovered": m.recovered,
            "recovered_paise": m.recovered_paise,
            "recovery_rate": round(m.recovery_rate, 4),
            "value_recovery_rate": round(m.value_recovery_rate, 4),
            "attempts": m.attempts,
            "nudges": m.nudges,
            "attempts_per_recovery": round(m.attempts_per_recovery, 2) if m.recovered else None,
            "wasted_attempts": m.wasted_attempts,
            "compliance_blocks": m.compliance_blocks,
            "compliance_stops": m.compliance_stops,
            # Deprecated alias for compliance_stops, kept for one release so
            # existing consumers don't break. It was misleadingly named: the
            # count is attempts BLOCKED at execution, not actual violations.
            "compliance_violations": m.compliance_stops,
            "cost_paise": m.cost_paise,
            "net_paise": m.net_paise,
        }

    by_reason = [
        {
            "reason": k,
            "label": REASON_LABELS[FailureReason(k)],
            "count": v["count"],
            "recovered": v["recovered"],
            "at_risk_paise": v["at_risk_paise"],
            "recovered_paise": v["recovered_paise"],
        }
        for k, v in sorted(smart.by_reason.items(), key=lambda kv: -kv[1]["count"])
    ]

    return {
        "total": smart.total,
        "at_risk_paise": smart.at_risk_paise,
        "smart": summary(smart),
        "naive": summary(naive),
        "backoff": summary(backoff),
        "net_lift_paise": smart.net_paise - naive.net_paise,
        "grid": grid,
        "by_reason": by_reason,
    }


@app.get("/audit/{payment_id}")
def audit(payment_id: str, policy: str = Query("smart", pattern="^(smart|naive|backoff)$")) -> dict:
    store = _audit[policy]
    if payment_id not in store:
        raise HTTPException(404, "No audit trail yet. Run a batch first.")
    return {"payment_id": payment_id, "policy": policy, "events": store[payment_id]}


# Serve the built UI last so it does not shadow the API routes.
_dist = Path(__file__).resolve().parents[1] / "web" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="ui")
