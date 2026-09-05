import React from "react";
import { rupees } from "../api.js";

function Run({ name, grid, field, tally, selected, onPick }) {
  return (
    <div className="run">
      <div className="run-label">
        <span className="run-name">{name}</span>
        <span className="run-tally">
          <b>{tally.recovered}</b> of {grid.length} recovered
          {" · "}
          <b>{rupees(tally.recovered_paise)}</b>
          {" · "}
          <b>{tally.attempts}</b> attempts
        </span>
      </div>
      <div className="grid reveal">
        {grid.map((p, i) => (
          <button
            key={p.id}
            className={[
              "cell",
              p[field] ? "won" : "",
              selected === p.id ? "picked" : "",
            ].join(" ")}
            style={{ animationDelay: `${Math.min(i, 260) * 1.4}ms` }}
            onClick={() => onPick(p.id)}
            title={`${p.id} · ${rupees(p.amount_paise)} · ${p.label} · ${
              p[field] ? "recovered" : "not recovered"
            }`}
            aria-label={`${p.id}, ${p.label}, ${
              p[field] ? "recovered" : "not recovered"
            }`}
          />
        ))}
      </div>
    </div>
  );
}

export default function BatchGrid({ data, selected, onPick }) {
  return (
    <section>
      <h2>Every payment in the batch, one square each</h2>
      <p className="note">
        Same {data.total} failed payments, same conditions, three different
        recovery strategies. Filled squares came back. Every row lines up
        column for column, so square N is the same payment in all three.
        Click any square to see exactly what the engine did to it.
      </p>
      <div className="sheet">
        <Run
          name="Blind retries"
          grid={data.grid}
          field="naive"
          tally={data.naive}
          selected={selected}
          onPick={onPick}
        />
        <Run
          name="Backoff retries"
          grid={data.grid}
          field="backoff"
          tally={data.backoff}
          selected={selected}
          onPick={onPick}
        />
        <Run
          name="RecoverPay"
          grid={data.grid}
          field="smart"
          tally={data.smart}
          selected={selected}
          onPick={onPick}
        />
        <div className="key">
          <span>
            <i className="swatch" style={{ background: "var(--won)" }} />
            Payment recovered
          </span>
          <span>
            <i className="swatch" style={{ background: "var(--lost)" }} />
            Still lost
          </span>
          <span>{rupees(data.at_risk_paise)} at risk in this batch</span>
        </div>
      </div>
    </section>
  );
}
