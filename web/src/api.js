// In dev the UI runs on 5173 and the API on 8000. In production FastAPI serves
// the built files, so the API is same-origin and the base is empty.
const BASE = import.meta.env.DEV ? "http://127.0.0.1:8000" : "";

async function call(path, options) {
  const res = await fetch(BASE + path, options);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

export const getReasons = () => call("/reasons");

export const runBatch = (n, seed) =>
  call("/recover/batch", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ n, seed }),
  });

export const decide = (payload) =>
  call("/decide", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });

export const getAudit = (id, policy) =>
  call(`/audit/${id}?policy=${policy}`);

export const rupees = (paise) =>
  new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format((paise ?? 0) / 100);

export const wait = (minutes) => {
  if (minutes === 0) return "immediately";
  if (minutes < 60) return `in ${minutes} min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `in ${h}h ${m}m` : `in ${h}h`;
};
