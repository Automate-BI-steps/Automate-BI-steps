import { useMemo, useState } from "react";

import { SEVERITIES, SEVERITY_LABEL, fmt, scoreClass } from "../format.js";
import ScoreBars from "./ScoreBars.jsx";

// Rapport de l'Agent 1 (section `profiling` du state), tel que l'agent de nettoyage le reçoit.
export default function ProfilingView({ runId, profiling }) {
  const { metadata, summary, tables = [], issues = [], relationships = [] } = profiling;
  const [severity, setSeverity] = useState("all");
  const [table, setTable] = useState(tables[0]?.table_name);
  const shown = severity === "all" ? issues : issues.filter((i) => i.severity === severity);
  const current = tables.find((t) => t.table_name === table) || tables[0];
  const scores = Object.entries(summary.quality_score_by_table || {}).map(([name, value]) => ({ name, value }));

  return (
    <div>
      <div className="res-head">
        <div>
          <h2>Diagnostic de l'agent de profilage</h2>
          <div className="muted">
            {metadata.run_id} · LLM : {metadata.llm?.enabled ? `${metadata.llm.model} (${metadata.llm.status})` : "désactivé"}
          </div>
        </div>
        <div className="actions">
          <a className="ghost" href={`/api/runs/${runId}/files/profiling.json`} target="_blank" rel="noopener">JSON transmis au nettoyage</a>
          <a className="ghost" href={`/api/runs/${runId}/files/profiling.md`} target="_blank" rel="noopener">Rapport Markdown</a>
        </div>
      </div>

      <div className="kpis">
        <Kpi value={summary.tables_count} label="tables" />
        <Kpi value={fmt(summary.total_rows)} label="lignes" />
        <Kpi value={summary.issues_count} label="problèmes détectés" />
        <Kpi value={<span className={scoreClass(summary.global_quality_score)}>{fmt(summary.global_quality_score)}</span>} label="score qualité / 100" />
      </div>

      {summary.narrative && <div className="llm-box"><b>Synthèse</b><p>{summary.narrative}</p></div>}

      <div className="grid2">
        <div>
          <h3>Problèmes par gravité</h3>
          <SeverityBar counts={summary.issues_by_severity || {}} />
          <h3>Problèmes par type</h3>
          <ul className="type-list">
            {Object.entries(summary.issues_by_type || {}).map(([t, n]) => (
              <li key={t}><span className="col">{t}</span> <b>{n}</b></li>
            ))}
          </ul>
        </div>
        <div>
          <h3>Score qualité par table</h3>
          <ScoreBars items={scores} />
        </div>
      </div>

      <h3>Problèmes à transmettre à l'agent de nettoyage</h3>
      <div className="filters" role="radiogroup" aria-label="Gravité">
        {["all", ...SEVERITIES].map((s) => {
          const n = s === "all" ? issues.length : summary.issues_by_severity?.[s] || 0;
          if (s !== "all" && !n) return null;
          return (
            <button key={s} className={"filter sev-" + s + (severity === s ? " on" : "")} onClick={() => setSeverity(s)}>
              {s === "all" ? "Tous" : SEVERITY_LABEL[s]} <span>{n}</span>
            </button>
          );
        })}
      </div>
      <div className="scroll">
        <table>
          <thead>
            <tr><th>ID</th><th>Gravité</th><th>Table · colonne</th><th>Type</th><th>Description</th><th>Lignes</th><th>Action suggérée</th><th>Source</th></tr>
          </thead>
          <tbody>
            {shown.map((i) => (
              <tr key={i.issue_id} className={i.is_expected ? "expected" : ""}>
                <td className="row-id">{i.issue_id}</td>
                <td><span className={"sev sev-" + i.severity}>{SEVERITY_LABEL[i.severity]}</span></td>
                <td><b>{i.table}</b>{i.column && <> · {i.column}</>}</td>
                <td><span className="col">{i.issue_type}</span></td>
                <td>{i.description}{i.examples?.length > 0 && <div className="muted small">ex. {i.examples.slice(0, 3).map(String).join(" · ")}</div>}</td>
                <td className="num">{fmt(i.affected_rows)}<div className="muted small">{fmt(i.affected_pct)} %</div></td>
                <td>{i.suggested_action}{i.is_expected && <div className="muted small">attendu</div>}</td>
                <td>{i.detected_by === "llm" ? <span className="badge">LLM</span> : "règle"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {relationships.length > 0 && (
        <>
          <h3>Relations détectées entre tables</h3>
          <ul className="rels">
            {relationships.map((r) => (
              <li key={r.relationship_id}>
                <span className="col">{r.from_table}.{r.from_column}</span> → <span className="col">{r.to_table}.{r.to_column}</span>
                <span className="muted"> {r.cardinality} · {fmt(r.match_pct)} % de correspondance
                  {r.orphan_count ? ` · ${r.orphan_count} orphelin(s)` : ""} · confiance {fmt(r.confidence)}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      <h3>Profil des colonnes</h3>
      <div className="tabs" role="tablist">
        {tables.map((t) => (
          <button key={t.table_name} role="tab" aria-selected={t.table_name === current?.table_name} onClick={() => setTable(t.table_name)}>
            {t.table_name}<span className="count">{t.row_count}</span>
          </button>
        ))}
      </div>
      {current && <TableProfile t={current} file={metadata.files?.find((f) => f.table_name === current.table_name)} />}
    </div>
  );
}

function Kpi({ value, label }) {
  return <div className="kpi"><b>{value}</b><span>{label}</span></div>;
}

function SeverityBar({ counts }) {
  const total = SEVERITIES.reduce((s, k) => s + (counts[k] || 0), 0);
  if (!total) return <p className="note">Aucun problème.</p>;
  return (
    <>
      <div className="stack" role="img" aria-label="Répartition des problèmes par gravité">
        {SEVERITIES.filter((k) => counts[k]).map((k) => (
          <div key={k} className={"sev-fill sev-" + k} style={{ flexGrow: counts[k] }} title={`${SEVERITY_LABEL[k]} : ${counts[k]}`} />
        ))}
      </div>
      <div className="legend">
        {SEVERITIES.filter((k) => counts[k]).map((k) => (
          <span key={k}><i className={"sev-fill sev-" + k} />{SEVERITY_LABEL[k]} {counts[k]}</span>
        ))}
      </div>
    </>
  );
}

function TableProfile({ t, file }) {
  const pk = useMemo(() => (t.primary_key_candidates || []).slice(0, 3).map((k) => k.join(" + ")).join(" · "), [t]);
  return (
    <>
      <p className="note">
        {t.file_name} · {fmt(t.row_count)} lignes × {t.column_count} colonnes
        {file && <> · encodage <b>{file.encoding}</b> · séparateur « {file.delimiter} »</>}
        {pk && <> · clés candidates : {pk}</>}
        {t.duplicate_row_count > 0 && <> · <span className="chip">{t.duplicate_row_count} ligne(s) en double</span></>}
      </p>
      <div className="scroll">
        <table>
          <thead>
            <tr><th>Colonne</th><th>Type détecté</th><th>Type attendu</th><th>Rôle</th><th>Manquants</th><th>Distinctes</th><th>Min</th><th>Max</th><th>Exemples</th></tr>
          </thead>
          <tbody>
            {t.columns.map((c) => (
              <tr key={c.column_name}>
                <td><b>{c.column_name}</b></td>
                <td className={c.detected_type !== c.expected_type ? "warn-cell" : ""}>{c.detected_type}</td>
                <td>{c.expected_type}</td>
                <td>{c.semantic_role}</td>
                <td>
                  <div className="minibar"><span style={{ width: `${Math.min(100, c.null_pct)}%` }} /></div>
                  <span className="muted small">{fmt(c.null_pct)} %</span>
                </td>
                <td className="num">{fmt(c.distinct_count)}</td>
                <td>{fmt(c.min ?? c.min_date ?? c.min_length)}</td>
                <td>{fmt(c.max ?? c.max_date ?? c.max_length)}</td>
                <td className="muted small">{(c.sample_values || []).slice(0, 3).join(" · ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
