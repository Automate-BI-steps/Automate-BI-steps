import { useState } from "react";

import { SEVERITY_LABEL, fmt, scoreClass } from "../format.js";
import ScoreBars from "./ScoreBars.jsx";

const TONE = {
  fixed: "ok", fixed_at_read: "ok", resolved: "ok", expected: "info", kept: "info",
  partly_fixed: "warn", flagged: "warn", rejected: "warn", open: "bad",
};

export function ResolutionChip({ status, label }) {
  return <span className={"rchip r-" + (TONE[status] || "info")}>{label}</span>;
}

// Bilan : le second profilage des tables nettoyées comparé au premier, problème par problème.
export default function BilanView({ cleaning }) {
  const ver = cleaning.verification;
  const res = cleaning.issue_resolution || { items: [], counts: {} };
  const [filter, setFilter] = useState("all");
  const items = filter === "all" ? res.items
    : filter === "done" ? res.items.filter((i) => ["ok", "info"].includes(TONE[i.status]))
      : res.items.filter((i) => !["ok", "info"].includes(TONE[i.status]));
  const tables = Object.entries(ver.by_table || {}).map(([name, s]) => ({ name, value: s.after, before: s.before }));
  const gain = ver.score_after != null && ver.score_before != null ? ver.score_after - ver.score_before : null;

  return (
    <div>
      <h2>Bilan : avant / après nettoyage</h2>
      <p className="note">Les tables nettoyées ont été re-profilées par l'agent de profilage (règles seules), {ver.score_method}.</p>

      <div className="hero">
        <div className="hero-card">
          <span className="muted">Score qualité</span>
          <div className="hero-val">
            <b className={scoreClass(ver.score_before)}>{fmt(ver.score_before)}</b>
            <span className="arrow">→</span>
            <b className={scoreClass(ver.score_after)}>{fmt(ver.score_after)}</b>
          </div>
          {gain != null && <span className={"delta " + (gain >= 0 ? "up" : "down")}>{gain >= 0 ? "+" : ""}{fmt(Math.round(gain * 10) / 10)} points</span>}
        </div>
        <div className="hero-card">
          <span className="muted">Problèmes détectés</span>
          <div className="hero-val"><b>{fmt(ver.issues_before)}</b><span className="arrow">→</span><b>{fmt(ver.issues_after)}</b></div>
          <span className="muted small">sur les colonnes d'origine</span>
        </div>
        <div className="hero-card">
          <span className="muted">Problèmes du profilage réglés</span>
          <div className="hero-val"><b>{res.handled}</b><span className="arrow">/</span><b>{res.total}</b></div>
          <div className="progress"><span style={{ width: `${res.total ? (100 * res.handled) / res.total : 0}%` }} /></div>
        </div>
      </div>

      <h3>Score par table</h3>
      <ScoreBars items={tables} />

      <h3>Chaque problème du profilage et son traitement</h3>
      <div className="filters">
        {[["all", "Tous", res.total], ["done", "Réglés", res.handled], ["todo", "À regarder", res.total - res.handled]].map(([k, label, n]) => (
          <button key={k} className={"filter" + (filter === k ? " on" : "")} onClick={() => setFilter(k)}>{label} <span>{n}</span></button>
        ))}
      </div>
      <div className="scroll">
        <table>
          <thead><tr><th>ID</th><th>Table · colonne</th><th>Problème</th><th>Gravité</th><th>Traité par</th><th>Résultat</th></tr></thead>
          <tbody>
            {items.map((i) => (
              <tr key={i.issue_id}>
                <td className="row-id">{i.issue_id}</td>
                <td><b>{i.table}</b>{i.column && <> · {i.column}</>}</td>
                <td><span className="col">{i.issue_type}</span><div className="small muted">{i.description}</div></td>
                <td><span className={"sev sev-" + i.severity}>{SEVERITY_LABEL[i.severity]}</span></td>
                <td className="small">
                  {i.steps.length ? [...new Map(i.steps.map((s) => [s.tool, s])).values()].map((s) => (
                    <div key={s.tool}>{s.label}{s.values_changed ? <span className="muted"> · {fmt(s.values_changed)}</span> : ""}</div>
                  )) : <span className="muted">—</span>}
                </td>
                <td><ResolutionChip status={i.status} label={i.status_label} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {ver.added_column_issues?.length > 0 && (
        <>
          <h3>Colonnes ajoutées par le nettoyage</h3>
          <p className="note">Ces remarques portent sur les nouvelles colonnes (conditionnement décomposé) et ne comptent pas dans le score :</p>
          <ul className="prof-issues">
            {ver.added_column_issues.map((i, k) => (
              <li key={k}><span className={"sev sev-" + i.severity}>{SEVERITY_LABEL[i.severity]}</span><span><b>{i.table}</b> · {i.description}</span></li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
