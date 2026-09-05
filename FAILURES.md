# What broke

The application asks what broke and how it got fixed. This is the running log.
Add to it as you extend the project, and keep the entries specific enough that
you could be cross-examined on any one of them.

---

**1. The first version scored perfectly, which is how I knew it was wrong.**

The policy was picking its backoff by reading the same recovery-curve constants
the simulator used to decide success. It was grading its own exam. Recovery
rate came out near 80% and meant nothing.

Fix: split ground truth into `environment.py` and made `policy.py` not import
it. The policy now sees only what a production system would see, which is the
failure code, the amount, the rail, and its own attempt history. Recovery
dropped to 44% and the number started meaning something.

---

**2. The compliance gate was one `if` statement, and it was not enough.**

`STOLEN_CARD` was blocked inside the policy. Then I wrote a test that swapped
in a deliberately rogue policy, and the orchestrator happily charged the
blocked card, because the only check lived in the component being replaced.

Fix: second gate in `orchestrator.py`, immediately before the charge fires,
plus `test_compliance_holds_even_at_execution` which swaps in that rogue policy
and asserts zero attempts. The naive baseline's policy layer still has no
compliance check at all, so it now visibly gets caught by that second gate 5
times per batch — attempts stopped at execution, not charges that went
through — while the engine's own policy never tries in the first place, so it
never even reaches that gate. That contrast is in the results table.

---

**3. Nudges were firing at 3am.**

Delays were computed as minutes since batch start with no concept of a wall
clock, so a payment that failed at 20:00 with an 8 hour backoff messaged the
customer in the middle of the night.

Fix: `minute_of_day()` anchored to a 09:00 start, plus a quiet-hours guard that
defers any message landing between 21:00 and 09:00 to the next morning and tags
the reason code `+quiet_hours` so the deferral is visible in the audit trail.

---

**4. Infinite loop on zero-delay decisions.**

A payment whose playbook returned a retry with `delay_min=0` got re-enqueued at
the same timestamp forever. The batch never terminated.

Fix: `MAX_STEPS_PER_PAYMENT` fuse in the orchestrator that stops the payment
with reason code `step_fuse`. The fuse has never tripped since the playbooks
were fixed, which is what you want from a fuse. It stays in.

---

**5. The model wrote a message with the wrong amount in it.**

Testing the nudge copy, the model occasionally rounded or restated the rupee
figure. A payment message with a wrong number is worse than a boring one.

Fix: `validate_copy()` regex-extracts every amount in the generated copy and
rejects the whole message if any of them differs from the true amount by more
than a paisa. Also rejects injected URLs and anything over 320 characters.
Rejection falls back to the per-reason template. The response reports
`message_source` as `model` or `template` so the fallback rate is measurable
rather than invisible.

---

**6. I did not trust the headline number, and I was right not to.**

The first clean run showed +77.7% net lift. That is exactly the kind of figure
that falls apart under a different seed.

Fix: `sweep.py`, which reruns 20 independent draws of both the batch and the
environment. Mean lift is +104%, but the range is +64% to +172% with a standard
deviation of 25 points. So the README leads with the direction and the attempts
-per-recovery ratio, which are stable, and treats the single-batch rupee figure
as an illustration rather than a claim.
