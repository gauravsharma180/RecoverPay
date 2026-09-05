import React from "react";
import { rupees } from "../api.js";

const pct = (x) => `${(x * 100).toFixed(1)}%`;
const per = (x) => (x == null ? "n/a" : x.toFixed(2));

export default function Ledger({ data }) {
  const { naive: a, backoff: c, smart: b } = data;

  // `lead` is computed from the raw numbers, never from the formatted strings,
  // so a currency or percent format change cannot silently flip a highlight.
  // It always compares the best guarded baseline (backoff) against smart,
  // since naive is unguarded and not the fair comparison.
  const rows = [
    ["Payments recovered", a.recovered, c.recovered, b.recovered, b.recovered > c.recovered],
    ["Recovery rate", pct(a.recovery_rate), pct(c.recovery_rate), pct(b.recovery_rate), b.recovery_rate > c.recovery_rate],
    ["Value recovered", rupees(a.recovered_paise), rupees(c.recovered_paise), rupees(b.recovered_paise), b.recovered_paise > c.recovered_paise],
    ["Charge attempts used", a.attempts, c.attempts, b.attempts, b.attempts < c.attempts],
    ["Attempts per recovery", per(a.attempts_per_recovery), per(c.attempts_per_recovery), per(b.attempts_per_recovery), b.attempts_per_recovery < c.attempts_per_recovery],
    ["Messages sent", a.nudges, c.nudges, b.nudges, false],
    ["Attempts that could never work", a.wasted_attempts, c.wasted_attempts, b.wasted_attempts, b.wasted_attempts < c.wasted_attempts],
    ["Attempts on stolen cards (blocked)", a.compliance_stops, c.compliance_stops, b.compliance_stops, b.compliance_stops < c.compliance_stops],
    ["Cost of recovering", rupees(a.cost_paise), rupees(c.cost_paise), rupees(b.cost_paise), b.cost_paise < c.cost_paise],
  ];

  const fewer = Math.round((1 - b.attempts / a.attempts) * 100);

  return (
    <section>
      <h2>The ledger</h2>
      <p className="note">
        Attempts cost money whether they work or not, so the number that
        actually matters is what is left after paying for the retries.
      </p>
      <div className="sheet">
        <table>
          <thead>
            <tr>
              <th>Measure</th>
              <th>Blind retries</th>
              <th>Backoff retries</th>
              <th>RecoverPay</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([label, left, mid, right, lead]) => (
              <tr key={label}>
                <td>{label}</td>
                <td className={`figure ${label === "Attempts on stolen cards (blocked)" && Number(left) > 0 ? "harm" : ""}`}>
                  {left}
                </td>
                <td className="figure">{mid}</td>
                <td className={`figure ${lead ? "lead" : ""}`}>{right}</td>
              </tr>
            ))}
            <tr className="total">
              <td>Net recovered</td>
              <td className="figure">{rupees(a.net_paise)}</td>
              <td className="figure">{rupees(c.net_paise)}</td>
              <td className="figure lead">{rupees(b.net_paise)}</td>
            </tr>
          </tbody>
        </table>
        <p className="note" style={{ margin: "16px 0 0" }}>
          {rupees(data.net_lift_paise)} more recovered on {fewer}% fewer charge
          attempts than blind retries. Blind retries' policy layer has no
          compliance check, so it waved {a.compliance_stops} stolen-card charge
          {a.compliance_stops === 1 ? "" : "s"} through toward execution — the
          second gate, checked again immediately before every charge fires,
          caught {a.compliance_stops === 1 ? "it" : "them"} there and stopped
          {" "}{a.compliance_stops === 1 ? "it" : "them"}. No stolen card was
          ever charged, by any policy: that gate firing is the safety net
          working as designed. Backoff retries carries the same compliance
          check and the same daily attempt cap as RecoverPay, which is why it
          is the fairer baseline to read RecoverPay against.
        </p>
      </div>
    </section>
  );
}
