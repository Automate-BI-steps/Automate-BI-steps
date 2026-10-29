import { useState } from "react";

import { dateTime } from "../format.js";

const AGENT = {
  orchestrator: ["Orchestrateur", "a-orch"], profiling: ["Agent 1 · Profilage", "a-prof"],
  cleaning: ["Agent 2 · Nettoyage", "a-clean"], user: ["Vous", "a-user"], verification: ["Contrôle", "a-verif"],
  etl: ["Agent 3 · ETL", "a-etl"], semantic_kpi: ["Agent 4 · Semantic & KPI", "a-kpi"],
};

// Qui écrit et qui lit chaque section du state (src/state.py).
const SECTIONS = [
  ["run", "Orchestrateur", "Interface", "identité du run, agent en cours"],
  ["input", "Vous (dépôt)", "Agents 1 et 2", "dossier des CSV, contexte métier, options"],
  ["profiling", "Agent 1", "Agent 2, interface", "rapport JSON : tables, colonnes, relations, problèmes, score"],
  ["cleaning", "Agent 2", "Agents suivants", "tables nettoyées, journal, questions, bilan"],
  ["etl", "Agent 3", "Interface", "schéma en étoile matérialisé (faits + dimensions)"],
  ["kpi", "Agent 4", "Interface", "KPI calculés, mapping des colonnes, contrôles, analyse"],
  ["timeline", "Tous", "Tous", "notes de passage de relais d'un agent à l'autre"],
  ["errors", "Tous", "Orchestrateur", "erreurs d'exécution"],
];

const size = (v) => {
  if (v === undefined) return "vide";
  const n = JSON.stringify(v).length;
  return n < 1024 ? `${n} o` : `${Math.round(n / 1024)} Ko`;
};

// Mémoire partagée du run : ce que chaque agent a reçu, produit et transmis.
export default function MemoryView({ runId, state }) {
  return (
    <div>
      <div className="res-head">
        <div>
          <h2>Mémoire du run (state)</h2>
          <div className="muted">
            Un seul state est transmis d'un agent à l'autre et enregistré à chaque passage de relais
            (outputs/runs/{runId}/state.json). Un agent repris plus tard, après votre validation par exemple, repart de ce fichier.
          </div>
        </div>
        <div className="actions">
          <a className="ghost" href={`/api/runs/${runId}/state.json`}>Télécharger state.json</a>
        </div>
      </div>

      <h3>Passages de relais</h3>
      <ol className="timeline">
        {(state.timeline || []).map((t, i) => {
          const [label, cls] = AGENT[t.agent] || [t.agent, "a-orch"];
          return (
            <li key={i}>
              <span className={"t-agent " + cls}>{label}</span>
              <div>
                <div className="t-summary">{t.summary}</div>
                <div className="muted small">{dateTime(t.at)} · {t.event}</div>
              </div>
            </li>
          );
        })}
      </ol>

      <h3>Sections du state</h3>
      <div className="scroll">
        <table>
          <thead><tr><th>Section</th><th>Écrite par</th><th>Lue par</th><th>Contenu</th><th>Taille</th></tr></thead>
          <tbody>
            {SECTIONS.map(([k, w, r, what]) => (
              <tr key={k} className={state[k] === undefined ? "muted" : ""}>
                <td><span className="col">{k}</span></td><td>{w}</td><td>{r}</td><td>{what}</td><td className="num">{size(state[k])}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>Explorer le state</h3>
      <div className="json-tree">
        {Object.entries(state).map(([k, v]) => <Node key={k} name={k} value={v} depth={0} />)}
      </div>
    </div>
  );
}

function Node({ name, value, depth }) {
  const [open, setOpen] = useState(depth < 1 && name !== "profiling" && name !== "cleaning" ? true : false);
  const isObj = value !== null && typeof value === "object";
  if (!isObj) {
    const cls = typeof value === "string" ? "j-str" : typeof value === "number" ? "j-num" : "j-lit";
    const text = typeof value === "string" ? `"${value.length > 160 ? value.slice(0, 160) + "…" : value}"` : String(value);
    return <div className="j-row"><span className="j-key">{name}</span>: <span className={cls}>{text}</span></div>;
  }
  const entries = Array.isArray(value) ? value.map((v, i) => [i, v]) : Object.entries(value);
  const shown = entries.slice(0, 100);
  return (
    <div className="j-row">
      <button className="j-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? "▾" : "▸"} <span className="j-key">{name}</span>
        <span className="muted"> {Array.isArray(value) ? `[${value.length}]` : `{${entries.length}}`}</span>
      </button>
      {open && (
        <div className="j-children">
          {shown.map(([k, v]) => <Node key={k} name={k} value={v} depth={depth + 1} />)}
          {entries.length > shown.length && <div className="muted small">… {entries.length - shown.length} de plus</div>}
        </div>
      )}
    </div>
  );
}
