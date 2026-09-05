import React, { useEffect, useState } from "react";
import { getAudit, rupees } from "../api.js";

const clock = (min) => {
  const total = 9 * 60 + min; // the batch clock starts at 09:00
  const day = Math.floor(total / 1440);
  const h = String(Math.floor((total % 1440) / 60)).padStart(2, "0");
  const m = String(total % 60).padStart(2, "0");
  return day ? `d${day + 1} ${h}:${m}` : `${h}:${m}`;
};

const WHAT = {
  retry: "Retried the same method",
  switch_rail: "Moved to another method",
  nudge: "Messaged the customer",
  stop: "Stopped",
};

export default function AuditTrail({ paymentId, payment }) {
  const [policy, setPolicy] = useState("smart");
  const [events, setEvents] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!paymentId) return;
    let live = true;
    getAudit(paymentId, policy)
      .then((d) => live && (setEvents(d.events), setError(null)))
      .catch((e) => live && (setError(e.message), setEvents(null)));
    return () => {
      live = false;
    };
  }, [paymentId, policy]);

  return (
    <div className="sheet">
      <h2>{paymentId ? `What happened to ${paymentId}` : "Payment history"}</h2>
      <p className="note">
        {paymentId
          ? `${rupees(payment?.amount_paise)} · ${payment?.label}`
          : "Pick a square above to trace one payment through its whole recovery."}
      </p>

      {paymentId && (
        <div className="tabs">
          {[
            ["smart", "RecoverPay"],
            ["backoff", "Backoff retries"],
            ["naive", "Blind retries"],
          ].map(([key, label]) => (
            <button
              key={key}
              className={`tab ${policy === key ? "on" : ""}`}
              onClick={() => setPolicy(key)}
            >
              {label}
            </button>
          ))}
        </div>
      )}

      {error && <p className="empty">{error}</p>}
      {!paymentId && <p className="empty">Nothing selected yet.</p>}

      {events && (
        <ol className="trail">
          {events.map((e, i) => (
            <li key={i}>
              <span className="clock">{clock(e.at_min)}</span>
              <span>
                <span className="step-what">
                  {WHAT[e.action] || e.action}
                  {e.rail ? ` (${e.rail})` : ""}
                  <span className={`outcome ${e.outcome}`}>{e.outcome}</span>
                </span>
                <span className="step-why"> {e.rationale}</span>
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
