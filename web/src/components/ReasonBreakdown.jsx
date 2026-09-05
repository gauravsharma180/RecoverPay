import React from "react";
import { rupees } from "../api.js";

export default function ReasonBreakdown({ rows }) {
  return (
    <section>
      <h2>Which failures are worth chasing</h2>
      <p className="note">
        Not every failed payment is recoverable, and pretending otherwise is
        what makes blind retries expensive. Stolen cards sit at zero because
        the engine refuses to touch them.
      </p>
      <div className="sheet">
        <table>
          <thead>
            <tr>
              <th>Why it failed</th>
              <th>Payments</th>
              <th>Recovered</th>
              <th className="bar-cell" />
              <th>Value back</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const rate = r.count ? r.recovered / r.count : 0;
              return (
                <tr key={r.reason}>
                  <td>{r.label}</td>
                  <td className="figure">{r.count}</td>
                  <td className="figure">{(rate * 100).toFixed(0)}%</td>
                  <td className={`bar-cell ${rate === 0 ? "zero" : ""}`}>
                    <span className="bar-track">
                      <span
                        className="bar-fill"
                        style={{ width: `${Math.max(rate * 100, rate === 0 ? 3 : 0)}%` }}
                      />
                    </span>
                  </td>
                  <td className="figure">{rupees(r.recovered_paise)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
