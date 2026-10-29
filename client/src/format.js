export const fmt = (x) =>
  typeof x === "number" ? x.toLocaleString("fr-FR", { maximumFractionDigits: 2 }) : x === null || x === undefined ? "—" : String(x);

export const plural = (n, one, many) => `${fmt(n)} ${n > 1 ? many : one}`;

export const dateTime = (iso) => (iso ? new Date(iso).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "medium" }) : "");

export const SEVERITIES = ["critical", "high", "medium", "low", "info"];
export const SEVERITY_LABEL = { critical: "Critique", high: "Élevée", medium: "Moyenne", low: "Faible", info: "Info" };

export const scoreClass = (s) => (s === null || s === undefined ? "" : s >= 90 ? "good" : s >= 70 ? "mid" : "bad");
