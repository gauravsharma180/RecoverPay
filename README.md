# RecoverPay

Reason-aware recovery for failed payments. Razorpay AI Buildathon 2026, Track 03.

## What it is

A failed payment queue is not one problem, it's ten: an issuer outage and an
expired card need opposite responses, but most systems just retry everything
blind. RecoverPay looks at *why* each payment failed and picks the right move
instead: retry, wait, switch rail, message the customer, or stop.

Three policies ship side by side so the claim is checkable, not asserted:

- **`naive_retry`** — unguarded blind retry (what a cron job + for-loop does)
- **`backoff_retry`** — same blind retry, but spaced out and guarded by the
  same compliance gate and attempt caps as the real policy
- **`smart_recovery`** — reason-aware playbook, bound by the same caps

Every decision is a rule in `policy.py`, deterministic and unit-tested. An LLM
(Gemini by default, Anthropic optional) only writes the customer-facing copy
for a decision already made — it never decides anything, and its output is
validated (amount must match to the paisa, no injected links) before use.
With no API key, everything still runs on templates.

## Architecture

```
generate_batch()          synthetic batch of failed payments, realistic mix
        |
        v
  orchestrator.py         virtual clock, priority queue, budget enforcement
        |                 re-checks the compliance gate before any charge
        +--> policy.py    deterministic decision + reason_code   (no model)
        +--> llm.py       nudge copy / merchant brief            (model, validated)
        +--> environment.py  hidden recovery curves              (ground truth)
        |
        v
  metrics + out/audit_*.jsonl
```

`policy.py` never imports `environment.py` — the policy only sees what a
production system would see (failure code, amount, rail, its own attempt
history), not the ground truth it's being scored against.

`src/api.py` (FastAPI) exposes the engine over HTTP and serves the built React
UI (`web/`) at `/`. The UI has four panels: a batch grid (one square per
payment, one row per policy), a ledger, a dry-run "ask the engine" form, and a
per-payment audit trail.

## Run it

```bash
pip install -r requirements.txt

python run.py                  # 500 payments, all three policies, comparison table
pytest tests -q                # guardrail tests
```

The service, with the UI:

```bash
cd web && npm install && npm run build && cd ..
uvicorn src.api:app --reload    
```

`/docs` has the interactive API reference. `/decide` is a dry run — it
returns the decision and the rule behind it without moving any money.


