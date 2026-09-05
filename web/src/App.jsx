import React, { useEffect, useMemo, useState } from "react";
import { getReasons, runBatch } from "./api.js";
import BatchGrid from "./components/BatchGrid.jsx";
import Ledger from "./components/Ledger.jsx";
import AskEngine from "./components/AskEngine.jsx";
import AuditTrail from "./components/AuditTrail.jsx";
import ReasonBreakdown from "./components/ReasonBreakdown.jsx";

export default function App() {
  const [size, setSize] = useState(500);
  const [seed, setSeed] = useState(7);
  const [data, setData] = useState(null);
  const [reasons, setReasons] = useState([]);
  const [selected, setSelected] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const go = async () => {
    setBusy(true);
    setError(null);
    try {
      const d = await runBatch(Number(size), Number(seed));
      setData(d);
      setSelected(null);
    } catch (e) {
      setError(
        `${e.message}. Start the API with: uvicorn src.api:app --reload`
      );
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    getReasons()
      .then((d) => setReasons(d.reasons))
      .catch(() => setReasons([]));
    go();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const picked = useMemo(
    () => data?.grid.find((p) => p.id === selected) ?? null,
    [data, selected]
  );

  return (
    <div className="shell">
      <header className="masthead">
        <div>
          <h1 className="wordmark">RecoverPay</h1>
          <p className="standfirst">
            A failed payment is not the same as a lost one. This engine works
            out why each payment failed and chases only the ones worth chasing.
          </p>
        </div>
        <div className="controls">
          <label className="field">
            Batch size
            <input
              type="number"
              min="10"
              max="5000"
              value={size}
              onChange={(e) => setSize(e.target.value)}
            />
          </label>
          <label className="field">
            Seed
            <input
              type="number"
              value={seed}
              onChange={(e) => setSeed(e.target.value)}
            />
          </label>
          <button onClick={go} disabled={busy}>
            {busy ? "Running" : "Run batch"}
          </button>
        </div>
      </header>

      {error && (
        <section>
          <div className="sheet">
            <p className="empty">{error}</p>
          </div>
        </section>
      )}

      {data && (
        <>
          <BatchGrid data={data} selected={selected} onPick={setSelected} />
          <Ledger data={data} />

          <section className="split">
            <AskEngine reasons={reasons} />
            <AuditTrail paymentId={selected} payment={picked} />
          </section>

          <ReasonBreakdown rows={data.by_reason} />
        </>
      )}

      <footer>
        The payment batch and its recovery odds are simulated, so the rupee
        figures are model output rather than production results. The failure
        taxonomy, the rules, the limits and the audit trail are real, and
        swapping the simulator for Razorpay test-mode APIs touches one function.
      </footer>
    </div>
  );
}
