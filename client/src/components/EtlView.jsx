import { useState } from "react";

import { fmt } from "../format.js";

// Résultat de l'Agent 3 (section `etl` du state) : schéma en étoile matérialisé à partir
// des tables nettoyées par l'Agent 2. Code de l'agent non modifié (src/agents/etl/).
export default function EtlView({ etl }) {
  const tables = Object.entries(etl.tables || {});
  const [active, setActive] = useState(0);
  const current = tables[Math.min(active, tables.length - 1)];
  const plan = etl.plan;
  const planTables = plan?.tables || [];

  if (etl.status === "error" && !tables.length) {
    const err = etl.fact_reasoning_error || etl.dimension_selection_error || "";
    const keyMissing = err.includes("OLLAMA_API_KEY");
    const badScore = err.includes("does not match the required formula");
    const n = etl.fact_reasoning_attempts;
    return (
      <div>
        <h2>Agent 3 · ETL</h2>
        <p className="error">Aucun schéma produit. {err || "Voir le détail ci-dessous."}</p>
        {keyMissing && (
          <p className="note">
            Le raisonnement sur la table de faits a besoin d'une clé Ollama Cloud (OLLAMA_API_KEY) :
            sans elle, l'agent ne produit pas de schéma (comportement du code de l'agent, pas une panne).
          </p>
        )}
        {badScore && (
          <p className="note">
            La clé fonctionne : c'est le modèle LLM qui s'est trompé dans le calcul du score de la table de faits,
            et le code de l'agent refuse toute réponse dont le score n'est pas recalculé à ±0,001.
            {n > 1 && ` Le calcul a été réessayé ${n} fois sans succès.`} Relancer un run peut suffire.
          </p>
        )}
      </div>
    );
  }

  return (
    <div>
      <div className="res-head">
        <div>
          <h2>Agent 3 · Schéma en étoile</h2>
          <div className="muted">
            Source : {etl.input_dir} · table de faits confirmée : <b>{etl.confirmed_fact || "—"}</b>
          </div>
        </div>
        <span className={"badge " + (etl.ollama_reasoning_used ? "b-ok" : "b-warn")}>
          {etl.ollama_reasoning_used ? "Raisonnement LLM (Ollama)" : "Repli déterministe (pas de clé Ollama)"}
        </span>
      </div>

      <div className="kpis">
        <Kpi value={tables.length} label="table(s) matérialisée(s)" />
        <Kpi value={etl.selected_dimensions?.length || 0} label="dimension(s) sélectionnée(s)" />
        <Kpi value={fmt((etl.validation?.errors || []).length)} label="erreur(s) de matérialisation" />
        <Kpi value={fmt((etl.validation?.warnings || []).length)} label="avertissement(s)" />
      </div>

      {plan?.grain && <p className="note">Grain de la table de faits : {plan.grain}</p>}

      {(etl.validation?.errors || []).length > 0 && (
        <>
          <h3>Erreurs de matérialisation</h3>
          <ul className="prof-issues">
            {etl.validation.errors.map((e, i) => <li key={i}><span className="sev sev-high">Erreur</span><span>{e}</span></li>)}
          </ul>
        </>
      )}
      {(etl.validation?.warnings || []).length > 0 && (
        <>
          <h3>Avertissements</h3>
          <ul className="prof-issues">
            {etl.validation.warnings.map((w, i) => <li key={i}><span className="sev sev-low">Info</span><span>{w}</span></li>)}
          </ul>
        </>
      )}

      {tables.length > 0 && (
        <>
          <h3>Tables matérialisées</h3>
          <div className="tabs" role="tablist">
            {tables.map(([name], i) => {
              const planned = planTables.find((t) => t.name === name);
              return (
                <button key={name} role="tab" aria-selected={i === active} onClick={() => setActive(i)}>
                  {name}{planned && <span className="muted small"> · {planned.table_type}</span>}
                </button>
              );
            })}
          </div>
          {current && <TableDetail name={current[0]} info={current[1]} planned={planTables.find((t) => t.name === current[0])} />}
        </>
      )}
    </div>
  );
}

function Kpi({ value, label }) {
  return <div className="kpi"><b>{value}</b><span>{label}</span></div>;
}

function TableDetail({ name, info, planned }) {
  return (
    <>
      <p className="note">
        {name} · {fmt(info.rows)} lignes × {info.columns?.length} colonnes
        {planned && <> · source : {(planned.source_tables || []).join(" + ")}</>}
        {planned?.primary_key?.length > 0 && <> · clé : {planned.primary_key.join(" + ")}</>}
      </p>
      <div className="scroll">
        <table>
          <thead><tr><th>Colonne</th><th>Origine</th><th>Rôle</th></tr></thead>
          <tbody>
            {(info.columns || []).map((c) => {
              const col = planned?.columns?.find((x) => x.name === c);
              return (
                <tr key={c}>
                  <td><b>{c}</b></td>
                  <td className="muted small">{col ? `${col.source_table}.${col.source_column}` : "—"}</td>
                  <td><span className="col">{col?.role || "—"}</span></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
