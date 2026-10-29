import { fmt } from "../format.js";

// Résultat de l'Agent 4 (section `kpi` du state) : KPI calculés à partir des tables nettoyées.
// Code de l'agent non modifié (src/agents/semantic_kpi/). Voir le commentaire dans
// src/agents/semantic_kpi/node.py sur l'écart de schéma avec la sortie de l'Agent 3 (ETL) :
// l'agent reconstruit sa propre table de faits à partir des CSV, il ne reçoit pas celle de l'ETL.
export default function KpiView({ kpi }) {
  const results = Object.entries(kpi.kpi_results || {});

  if (kpi.status === "error" && !results.length) {
    return (
      <div>
        <h2>Agent 4 · Semantic & KPI</h2>
        <p className="error">Aucun KPI calculé. {(kpi.errors || []).join(" · ") || "Voir le détail ci-dessous."}</p>
      </div>
    );
  }

  return (
    <div>
      <div className="res-head">
        <div>
          <h2>Agent 4 · KPI</h2>
          <div className="muted">Source : {kpi.data_path} · colonnes comprises par {kpi.mapping_methode || "mots-clés"}</div>
        </div>
      </div>

      <div className="kpis">
        <Kpi value={results.length} label="KPI calculé(s)" />
        <Kpi value={(kpi.kpi_checks || []).filter((c) => c.statut !== "ok").length} label="avertissement(s) de contrôle" />
        <Kpi value={(kpi.kpi_analysis || []).filter((a) => a.niveau === "attention").length} label="point(s) d'attention" />
      </div>

      {kpi.kpi_summary && <div className="llm-box"><b>Résumé</b><p>{kpi.kpi_summary}</p></div>}

      <h3>Colonnes comprises (rôle → colonne)</h3>
      <div className="scroll">
        <table>
          <thead><tr><th>Rôle</th><th>Colonne</th></tr></thead>
          <tbody>
            {Object.entries(kpi.semantic_mapping || {}).map(([role, col]) => (
              <tr key={role}><td><span className="col">{role}</span></td><td>{col}</td></tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>KPI calculés</h3>
      <div className="scroll">
        <table>
          <thead><tr><th>KPI</th><th>Valeur</th><th>Définition</th><th>Formule</th><th>Pourquoi</th></tr></thead>
          <tbody>
            {results.map(([id, r]) => (
              <tr key={id}>
                <td><b>{r.nom}</b></td>
                <td className="num"><KpiValue value={r.valeur} unite={r.unite} /></td>
                <td className="muted small">{r.definition}</td>
                <td className="muted small">{r.formule}</td>
                <td className="muted small">{r.pourquoi}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {(kpi.kpi_checks || []).length > 0 && (
        <>
          <h3>Contrôles de cohérence</h3>
          <ul className="checks">
            {kpi.kpi_checks.map((c, i) => (
              <li key={i} className={c.statut === "ok" ? "ok" : "info"}><span>{c.controle}</span><span className="muted">{c.detail}</span></li>
            ))}
          </ul>
        </>
      )}

      {(kpi.kpi_analysis || []).length > 0 && (
        <>
          <h3>Analyse</h3>
          <ul className="prof-issues">
            {kpi.kpi_analysis.map((a, i) => (
              <li key={i}>
                <span className={"sev " + (a.niveau === "attention" ? "sev-high" : "sev-info")}>
                  {a.niveau === "attention" ? "Attention" : "Info"}
                </span>
                <span>{a.constat}{a.interpretation && <div className="muted small">{a.interpretation}</div>}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      {(kpi.errors || []).length > 0 && (
        <>
          <h3>Erreurs</h3>
          <ul className="prof-issues">
            {kpi.errors.map((e, i) => <li key={i}><span className="sev sev-high">Erreur</span><span>{e}</span></li>)}
          </ul>
        </>
      )}
    </div>
  );
}

function Kpi({ value, label }) {
  return <div className="kpi"><b>{value}</b><span>{label}</span></div>;
}

function KpiValue({ value, unite }) {
  if (Array.isArray(value)) {
    return <>{value.slice(0, 3).map((p) => `${p.nom} (${fmt(p.part_pct)} %)`).join(" · ")}</>;
  }
  if (value && typeof value === "object") {
    const annees = Object.keys(value.ca_par_annee || {});
    return <>{annees.map((a) => fmt(value.ca_par_annee[a])).join(" → ")}</>;
  }
  return <>{fmt(value)}{unite && value != null ? ` ${unite}` : ""}</>;
}
