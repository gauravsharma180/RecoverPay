import React, { useEffect, useState } from "react";
import { decide, wait } from "../api.js";

const RAILS = ["card", "upi", "netbanking", "wallet"];

export default function AskEngine({ reasons }) {
  const [form, setForm] = useState({
    raw_code: "INSUFFICIENT_FUNDS",
    rail: "card",
    amount: 2500,
    attempts_so_far: 0,
    nudges_so_far: 0,
  });
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const ask = async (next = form) => {
    setBusy(true);
    setError(null);
    try {
      setResult(
        await decide({
          raw_code: next.raw_code,
          rail: next.rail,
          amount_paise: Math.round(Number(next.amount) * 100),
          attempts_so_far: Number(next.attempts_so_far),
          nudges_so_far: Number(next.nudges_so_far),
          with_copy: true,
        })
      );
    } catch (e) {
      setError(e.message);
      setResult(null);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    ask();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const set = (key) => (e) => {
    const next = { ...form, [key]: e.target.value };
    setForm(next);
    ask(next);
  };

  return (
    <div className="sheet">
      <h2>Ask the engine</h2>
      <p className="note">
        Describe a failed payment and see what would happen to it. Nothing is
        charged: this is the dry run an ops team uses to check the rules before
        trusting them.
      </p>

      <div className="form-rows">
        <label className="field">
          Why it failed
          <select value={form.raw_code} onChange={set("raw_code")}>
            {reasons.map((r) => (
              <option key={r.code} value={r.code}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Payment method
          <select value={form.rail} onChange={set("rail")}>
            {RAILS.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Amount in rupees
          <input
            type="number"
            min="1"
            value={form.amount}
            onChange={set("amount")}
          />
        </label>
        <label className="field">
          Tries already made
          <select value={form.attempts_so_far} onChange={set("attempts_so_far")}>
            {[0, 1, 2, 3].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error && <p className="empty">{error}</p>}

      {result && (
        <div className="verdict" aria-live="polite" aria-busy={busy}>
          <div
            className={`verdict-action ${
              result.action === "stop" ? "stopped" : ""
            }`}
          >
            {result.action_label}
            {result.rail && result.action === "switch_rail"
              ? `: ${result.rail}`
              : ""}
          </div>
          <div className="verdict-when">
            {result.action === "stop"
              ? "no further attempts on this payment"
              : wait(result.delay_minutes)}
          </div>

          <p className="because">
            <span className="rule">{result.rule}</span>
            {result.rationale}
          </p>

          <p className="contrast">
            A blind retry loop would {result.baseline_label.toLowerCase()} here.
          </p>

          {result.customer_message && (
            <div className="message">
              {result.customer_message}
              <span className="message-src">
                {result.message_source === "model"
                  ? "written by the model, amount verified against the payment"
                  : "template fallback (no key, rate-limited, or model output rejected)"}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
