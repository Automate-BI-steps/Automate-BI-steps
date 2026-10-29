import { useState } from "react";

import { fmt, plural } from "../format.js";
import { ResolutionChip } from "./BilanView.jsx";

const tableKey = (name) => String(name).replace(/[^0-9a-zA-Z_]+/g, "_").replace(/^_+|_+$/g, "").toLowerCase();

// Résultat de l'Agent 2 : vue simplifiée avant / après (view.json), une table à la fois.
export default function CleaningView({ runId, view: v, resolution }) {
  const [active, setActive] = useState(0);
  const t = v.totals;
  const pl = v.planner || {};
  const tb = v.tables[Math.min(active, v.tables.length - 1)];
  const issuesOf = (name) => (resolution?.items || []).filter((i) => tableKey(i.table) === name);

  return (
    <div>
      <div className="res-head">
        <div>
          <h2>Nettoyage terminé</h2>
          <div className="muted">{v.files.join(", ")}</div>
        </div>
        <div className="actions">
          <a className="primary" href={`/api/runs/${runId}/download`}>Télécharger les tables nettoyées</a>
          <a className="ghost" href={`/api/runs/${runId}/files/report`} target="_blank" rel="noopener">Rapport imprimable</a>
        </div>
      </div>

      <div className="kpis">
        <Kpi value={`${t.tables_to_process} / ${t.tables}`} label="tables à traiter" />
        <Kpi value={fmt(t.corrections)} label="corrections" />
        <Kpi value={fmt(t.rows_removed)} label="lignes supprimées" />
        <Kpi value={`${fmt(t.etl_ready ?? 0)} / ${t.tables}`} label="tables prêtes pour l'ETL" />
      </div>

      {pl.warning ? <p className="note warn">{pl.warning}</p> : pl.model ? <p className="note">Analyse faite par {pl.model}, avec le diagnostic du profilage.</p> : null}
      {v.clean_tables.length > 0 && <p className="note">Déjà propres, rien à faire : {v.clean_tables.join(", ")}.</p>}

      {!v.tables.length ? (
        <p className="empty muted">Aucune correction nécessaire : toutes les tables sont propres.</p>
      ) : (
        <>
          <div className="tabs" role="tablist">
            {v.tables.map((x, i) => (
              <button key={x.name} role="tab" aria-selected={x === tb} onClick={() => setActive(i)}>
                {x.name}<span className="count">{fmt(x.actions.reduce((s, a) => s + a.count, 0))}</span>
              </button>
            ))}
          </div>
          <TableView tb={tb} issues={issuesOf(tb.name)} />
        </>
      )}
    </div>
  );
}

function Kpi({ value, label }) {
  return <div className="kpi"><b>{value}</b><span>{label}</span></div>;
}

function TableView({ tb, issues }) {
  const cls = (ok) => (ok === true ? "ok" : ok === false ? "ko" : "info");
  const g = tb.grid;
  const rm = tb.removed;
  return (
    <>
      <p className="note">{tb.source} · {plural(tb.rows_before, "ligne", "lignes")} avant, {plural(tb.rows_after, "ligne", "lignes")} après</p>

      {tb.llm?.analysis && <div className="llm-box"><b>Analyse du LLM</b><p>{tb.llm.analysis}</p></div>}

      {issues.length > 0 && (
        <>
          <h3>Ce que le profilage avait signalé</h3>
          <ul className="prof-issues">
            {issues.map((i) => (
              <li key={i.issue_id}>
                <ResolutionChip status={i.status} label={i.status_label} />
                <span><b>{i.issue_id}</b> {i.column ? `${i.column} · ` : ""}{i.description}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      <h3>Ce qui a été corrigé</h3>
      <ul className="done">
        {tb.actions.map((a) => (
          <li key={a.text}><div className="act"><span>{a.text}</span>{a.columns.map((c) => <span key={c} className="col">{c}</span>)}</div></li>
        ))}
      </ul>
      {tb.renamed?.length > 0 && (
        <p className="note">Colonnes renommées : {tb.renamed.map((r) => <span key={r.from} className="col">{r.from} → {r.to}</span>)}</p>
      )}

      {(tb.derived || []).map((d) => (
        <div key={d.source}>
          <h3>Nouvelles colonnes à partir de {d.source}</h3>
          <p className="note">
            {plural(d.count, "valeur décomposée", "valeurs décomposées")} : la quantité totale est convertie en grammes ou en millilitres.
            {d.unparsed.length > 0 && ` Non reconnues, laissées telles quelles : ${d.unparsed.join(", ")}.`}
          </p>
          <div className="scroll">
            <table>
              <thead><tr><th className="src">{d.source}</th>{d.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
              <tbody>
                {d.rows.map((r, i) => (
                  <tr key={i}><td className="src">{r[0]}</td>{r.slice(1).map((x, j) => <td key={j} className={x === "(vide)" ? "muted" : ""}>{x}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}

      {tb.checks?.length > 0 && (
        <>
          <h3>Prêt pour l'ETL <span className={"badge " + (tb.etl_ready ? "b-ok" : "b-bad")}>{tb.etl_ready ? "oui" : "à reprendre"}</span></h3>
          <ul className="checks">
            {tb.checks.map((c) => (
              <li key={c.label} className={cls(c.ok)}><span>{c.label}</span>{c.detail && <span className="muted">{c.detail}</span>}</li>
            ))}
          </ul>
        </>
      )}

      {tb.outliers.filter((o) => o.status === "kept").map((o) => (
        <p key={o.column} className="note">Valeurs extrêmes gardées telles quelles ({o.column}) : {o.text}.</p>
      ))}
      {tb.llm_refused.map((r) => (
        <p key={r.label + r.column} className="note">Non appliqué sur conseil du LLM : {r.label.toLowerCase()} ({r.column || "table"}). {r.reason}</p>
      ))}

      {g.rows.length > 0 && (
        <>
          <h3>Avant → après</h3>
          <div className="legend"><span><i className="changed" />valeur corrigée</span><span>barré : ancienne valeur</span><span>· : espace en trop</span></div>
          <div className="scroll">
            <table>
              <thead><tr><th>Ligne</th>{g.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
              <tbody>
                {g.rows.map((r) => (
                  <tr key={r.row}>
                    <td className="row-id">{r.row}</td>
                    {r.cells.map((c, i) => c.changed
                      ? <td key={i} className="ch"><span className="old">{c.before}</span><span className="new">{c.after}</span></td>
                      : <td key={i}>{c.after}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {g.more > 0 && <p className="note">… et {plural(g.more, "autre ligne modifiée", "autres lignes modifiées")} (toutes sont dans les tables téléchargées).</p>}
        </>
      )}

      {rm.rows.length > 0 && (
        <>
          <h3>Lignes supprimées</h3>
          <div className="scroll">
            <table>
              <thead><tr><th>Ligne</th><th>Raison</th>{rm.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
              <tbody>
                {rm.rows.map((r) => (
                  <tr key={r.row}><td className="row-id">{r.row}</td><td><span className="chip">{r.reason}</span></td>{r.values.map((x, i) => <td key={i}>{x}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
          {rm.more > 0 && <p className="note">… et {plural(rm.more, "autre ligne supprimée", "autres lignes supprimées")}.</p>}
        </>
      )}
    </>
  );
}
