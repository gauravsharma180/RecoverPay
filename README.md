# RecoverPay

**Failed payments are not lost payments. Most of them are lost because of how they were retried.**

RecoverPay takes a batch of failed transactions, works out why each one failed,
and runs a bounded recovery plan: retry, wait, switch rail, message the
customer, or stop. Every rupee that moves is traceable to the rule that moved
it.

Razorpay AI Buildathon 2026, Track 03: AI Revenue Recovery.

---

## The problem

A merchant's failed payment queue is not one problem, it is ten. An issuer
outage and an expired card produce the same red row in a dashboard and need
opposite responses. The default fix everyone ships is a cron job that retries
three times in a row.

That baseline does three bad things at once:

- it retries failures that can never succeed on the same rail (expired card, wrong VPA)
- it retries during the outage instead of after it
- it never asks the customer to do the one thing that would fix it

Measured on a 500 payment batch, the naive baseline burns **11.7 charge
attempts per recovery**. This engine does it in **2.8**.

## Results

500 failed payments, Rs 6,98,019 at risk, identical environment seed for both
policies:

| metric | naive retry | RecoverPay |
| --- | ---: | ---: |
| recovered (count) | 113 | **221** |
| recovery rate | 22.6% | **44.2%** |
| recovered value | Rs 1,91,870 | **Rs 3,36,726** |
| value recovery rate | 27.5% | **48.2%** |
| charge attempts | 1,322 | **622** |
| attempts per recovery | 11.70 | **2.81** |
| customer messages | 0 | 136 |
| attempts that could not work | 102 | **0** |
| blocked at execution, 2nd gate (attempts, not charges) | 5 | **0** |
| recovery cost | Rs 3,305 | **Rs 1,589** |
| **net recovered** | Rs 1,88,565 | **Rs 3,35,137** |

Net lift on this batch: **+Rs 1,46,572 (+77.7%)** on half the attempts.

One batch proves nothing, so `sweep.py` reruns the whole thing across 20
independent draws of both the payment mix and the environment:

```
  20 runs x 500 payments

  net value lift        mean +104.4%   min  +64.2%   max +171.8%   sd  24.7
  recovery rate gain    mean  +20.8 pts  min  +16.6    max  +25.2
  attempts vs baseline  mean   0.45x
  compliance-gate stops, smart, execution-time (want 0): 0

  worse than baseline in 0 of 20 runs
```

The spread is wide, which is why the headline claim here is the direction and
the attempt efficiency, not the exact percentage.

## Three policies, not two

`naive_retry` alone is a strawman: an unguarded cron job is an easy baseline
to beat. `src/policy.py` implements three, as a progression, and `run.py`
scores all three against the same batch and the same environment seed:

| policy | reasons about the failure? | respects the caps? |
| --- | --- | --- |
| `naive_retry` | no | no — retries blind, ignores every cap |
| `backoff_retry` | no | yes — same caps and compliance gate as `smart_recovery` |
| `smart_recovery` | yes — per-reason playbook | yes |

`backoff_retry` isolates one variable: same rail, same lack of reasoning, same
3-attempt cap, just spaced out (5m / 30m / 120m instead of 0 / 0 / 0) and
guarded by the same compliance gate, recovery window, and per-customer daily
attempt cap as `smart_recovery`. It is the fair comparison — what a competent
team ships before it builds a failure taxonomy.

On the same batch as above:

| metric | naive retry | backoff retry | smart recovery |
| --- | ---: | ---: | ---: |
| recovered (count) | 113 | 101 | **221** |
| recovered (value) | Rs 1,91,870 | Rs 1,48,912 | **Rs 3,36,726** |
| charge attempts | 1,322 | 725 | **622** |
| net recovered | Rs 1,88,565 | Rs 1,47,099 | **Rs 3,35,137** |

The honest finding: **`backoff_retry` recovers less than `naive_retry`** — 101
payments against 113, Rs 1,48,912 against Rs 1,91,870 — despite its retries
being individually better-timed. That is not a timing failure. Once caps that
a real processor must actually enforce start binding, they cost recovered
volume: 268 of `backoff_retry`'s 500 payments stop on
`customer_daily_cap`, because several failed payments can share one customer
and `backoff_retry` — like `smart_recovery` — refuses to send that customer a
6th attempt in a day. `naive_retry` has no such scruple, so part of its 113 is
bought by a rule a production processor isn't allowed to break. Better retry
timing alone, once it plays by the same rules as everything else, is not
enough to beat a baseline that doesn't play by them.

`smart_recovery` is bound by the identical caps and still recovers 221 —
**+127.8% net value over `backoff_retry`**, next to +77.7% over `naive_retry`.
That is supporting evidence for reason-awareness, not the headline: the
naive comparison stays the primary claim in this README, because leading with
the bigger number against the weaker, unguarded baseline would read as
cherry-picking. Across the 20-seed sweep the same pattern holds — smart beats
backoff in all 20 runs, mean **+125.3%** (range +69.3% to +245.8%) — wider
than the naive comparison's mean +104.4% (range +64.2% to +171.8%), because a
smaller recovered count for `backoff_retry` makes the ratio more sensitive to
which batch you happen to draw.

## Where the AI is, and where it deliberately is not

| decision | who makes it | why |
| --- | --- | --- |
| retry / wait / switch rail / stop | rule table in `policy.py` | auditable, testable, identical every run |
| how long to wait | rule table | a model cannot beat a backoff constant here |
| whether a payment may be charged at all | hard gate, checked twice | compliance is not a prompt |
| Hinglish customer message | model, validated | genuine language task, 10 reasons x many tones |
| merchant root-cause brief | model, given pre-computed numbers | summarising aggregates is what models are good at |

The model never sees a decision it can change. It receives a decision already
made and turns it into words. `llm.py` then validates that output before it
ships: length cap, no injected URLs, and the rupee amount in the copy must
match the real amount to the paisa. Any failure falls back to a template.

**The whole pipeline runs with no API key.** Templates cover every failure
reason, so the metrics never depend on a model call.

## Stopping criteria and escalation rules

Bounded by construction, all in `policy.py` where you can read them:

- max 3 charge attempts per payment, ever
- max 2 customer messages per payment, ever
- max 5 charge attempts and 1 message per customer per day, shared across all their failed payments
- 72 hour recovery window, after which the payment is dropped with `window_expired`
- no customer messages between 21:00 and 09:00 IST, deferred to the next morning
- tickets above Rs 25,000 cannot be silently moved to a new rail, the customer is asked first
- `STOLEN_CARD` is never retried, checked in the policy and again in the orchestrator before the charge fires
- expired cards and invalid VPAs are never retried on the same rail
- an unmapped failure code gets one cautious retry and then stops, rather than silently accumulating attempts

Every decision emits a `reason_code`. The audit trail in `out/audit_smart.jsonl`
is one JSON line per action, so any recovered rupee can be traced back to the
rule that produced it.

## The failure taxonomy

Gateways emit a long tail of raw codes. They are normalised into ten buckets
that actually change the next action, and each bucket has its own playbook:

| reason | first move | why |
| --- | --- | --- |
| `GATEWAY_TIMEOUT` | retry in 2 min | the only failure where an immediate retry is right |
| `ISSUER_DOWN` | wait 50 min, then route around | the outage clears on its own |
| `INSUFFICIENT_FUNDS` | message, then retry next morning | retrying in 30 seconds cannot invent money |
| `UPI_COLLECT_EXPIRED` | fresh collect after a prompt | the request timed out, not the intent |
| `AUTH_3DS_DROPOFF` | resume link, then a simpler rail | nobody abandoned 3DS because the retry was slow |
| `CARD_EXPIRED` | ask for a new instrument | same-rail retry is mathematically dead |
| `DO_NOT_HONOUR` | back off 4h, then switch rail | issuer risk engines punish hammering |
| `LIMIT_EXCEEDED` | wait for the daily reset | limits are per instrument and per day |
| `INVALID_VPA` | ask the customer to correct it | no automated fix exists |
| `STOLEN_CARD` | stop | never retryable |

## Architecture

```
generate_batch()          500 synthetic failed payments, realistic reason mix
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

`policy.py` does not import `environment.py`. That separation is the point: if
the policy could read the recovery curves it would score perfectly and the
number would mean nothing.

## Run it

```bash
pip install -r requirements.txt

python run.py                  # 500 payments, both policies, comparison table
python run.py --n 2000         # bigger batch
python run.py --brief          # add the LLM merchant brief and a sample nudge
python sweep.py --runs 20      # stability across seeds

pytest tests -q                # 15 guardrail tests

uvicorn src.api:app --reload   # http://127.0.0.1:8000/docs
```

The service:

```bash
# dry run: what would the engine do, and why
curl -s localhost:8000/decide -H 'content-type: application/json' \
  -d '{"raw_code":"54","rail":"card","amount_paise":250000,"nudges_so_far":1}'
# -> switch_rail to upi in 30m, reason_code "switch_after_expiry"

curl -s localhost:8000/decide -H 'content-type: application/json' \
  -d '{"raw_code":"43"}'
# -> stop, reason_code "compliance_blocked"

curl -s localhost:8000/recover/batch -H 'content-type: application/json' -d '{"n":500}'
curl -s localhost:8000/audit/pay_0003
```

`/decide` is a dry run. It returns the decision and the rule behind it without
moving money, so a merchant ops person can interrogate the policy before
trusting it.

Model-written copy is optional and provider-switchable. Set `GEMINI_API_KEY`
(free tier at aistudio.google.com/apikey) to turn it on — Gemini is the
default provider. Set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY` to use
Anthropic instead. Without any key, or with an unset/unrecognised
`LLM_PROVIDER`, everything falls back to templates. No metric in this repo
changes with the provider, or with no key at all: the model only turns an
already-made decision into words, it never makes one.

## The interface

```bash
cd web && npm install && npm run build && cd ..
uvicorn src.api:app --reload      # http://127.0.0.1:8000
```

FastAPI serves the built UI at `/` and the API under it. For frontend work,
run `npm run dev` in `web/` for hot reload on port 5173 against the API on 8000.

Four panels, in the order a payments ops person would read them:

1. **The batch grid.** Every payment in the batch drawn as one square, three
   times: blind retries, backoff retries, RecoverPay. All three rows share the
   same column layout, so square N is the same payment in every row and you
   can read straight down a column. Click any square to trace that payment.
2. **The ledger.** Recovered value, attempts, cost, and what is left over,
   for all three policies side by side.
3. **Ask the engine.** Describe a failed payment, get the decision and the rule
   that produced it, with no money moved. Shows what a blind retry loop would
   have done instead, and the Hinglish message the customer would receive.
4. **What happened to this payment.** The audit trail as a timeline, switchable
   between all three policies, so you can watch blind retries burn three
   attempts on an expired card while backoff retries burns the same three tries
   more slowly, and the engine asks for a new one instead.

## What is real and what is simulated

Being straight about this, because the number above is only worth what its
assumptions are worth:

- **Simulated:** the payment batch and the recovery probabilities. The curves in
  `environment.py` are hand-tuned to be directionally honest (outages heal with
  time, expired cards never heal, nudges only work on the 42% of customers who
  act) but they are not fitted to real data.
- **Real:** the failure taxonomy, the raw-code mapping, the playbook structure,
  the guardrails, the cost model (Rs 2.50 per attempt, Rs 0.25 per message),
  and the audit trail format.
- **Therefore:** the defensible claim is that a reason-aware bounded policy
  recovers substantially more on substantially fewer attempts than blind
  retries, under a stated model of how failures recover. The absolute rupee
  figure is a simulation output, not a production result.

Swapping in real outcomes is a single interface: `Environment.attempt()`.
Replace it with Razorpay test-mode API calls and every metric in this repo
keeps working unchanged.

## Shipping a release zip

```bash
cd web && npm install && npm run build && cd ..   # refresh web/dist first
python make_release.py                             # writes recoverpay-release.zip
```

Packages the source plus the built UI, skipping everything that shouldn't
leave this machine: `.venv/`, `node_modules/`, `__pycache__/`, `.pytest_cache/`,
`out/`, and `.env` (which can hold a real API key). `web/dist/` is included
even though it's gitignored, so a judge can run the zip without a Node
toolchain. Comes in well under the 5 MB target - the source alone is a few
hundred KB; the built UI adds under 200 KB.

## Next

- back the recovery curves with real historical outcomes instead of hand-tuned constants
- per-issuer curves learned online, so the wait for HDFC is not the wait for SBI
- uplift modelling on nudges: message only the customers where the message changes the outcome
- Redis-backed scheduler and Postgres ledger in place of the in-process queue
- Razorpay test-mode integration behind the same `Environment` interface

See `FAILURES.md` for what broke on the way here.
