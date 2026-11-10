import { useState } from "react";

import { postJson } from "../api.js";
import { fmt } from "../format.js";

const STATUS = { MATCH: ["concorde", "b-ok"], MISMATCH: ["écart", "b-bad"], NOT_RECOMPUTED: ["non recalculable", "b-warn"] };
const ANSWER = { verified: "vérifiée", filtered: "vérifiée (phrases non soutenues retirées)", template_fallback: "preuves seules", failed: "échec" };
const FILES = [["audit_trail.csv", "Journal d'audit"], ["lineage.csv", "Lignée"], ["reconciliation.csv", "Réconciliation"],
  ["xai_explanations.csv", "Explications"], ["human_review_queue.csv", "À valider"]];

// Résultat de l'Agent 6 (section `auditor` du state) : journal d'audit unifié, lignée, recalcul indépendant des chiffres
// publiés, explications vérifiées et file de validation humaine (src/agents/auditor/, code de Dhouha).
export default function AuditorView({ runId, auditor, config }) {
  const a = auditor || {};
  const [question, setQuestion] = useState("");
  const [asked, setAsked] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  if (a.status === "ERROR" || !a.summary) {
    return (
      <div>
        <h2>Agent 6 · Audit & explications</h2>
        <p className="error">Audit non réalisé. {(a.errors || []).join(" · ") || "Voir l'onglet Mémoire pour le détail."}</p>
      </div>
    );
  }

  const s = a.summary;
  const recon = a.reconciliation || [];
  const ok = recon.filter((r) => r.status === "MATCH").length;
  const bad = recon.filter((r) => r.status === "MISMATCH").length;
  const ask = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setAsked({ q: question, ...(await postJson(`/api/runs/${runId}/auditor/ask`, { question })) });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="res-head">
        <div>
          <h2>Agent 6 · Audit & explications (XAI)</h2>
          <div className="muted">Explications : {s.model} · recherche : {s.retrieval}{config?.auditor_llm === "none" && " · aucun modèle de langage configuré (AUDITOR_LLM_PROVIDER)"}</div>
        </div>
        <div className="actions">
          {a.artifacts?.report_pdf
            ? <a className="btn gold" href={`/api/runs/${runId}/files/rapport_audit.pdf`} download>Télécharger le rapport PDF</a>
            : a.artifacts?.xai_report && <a className="btn ghost" href={`/api/runs/${runId}/files/xai_report.md`} target="_blank" rel="noopener">Rapport (Markdown)</a>}
          <details className="menu">
            <summary className="btn ghost">Données CSV ▾</summary>
            <div className="menu-list">
              {FILES.map(([name, label]) => (
                <a key={name} href={`/api/runs/${runId}/files/${name}`} target="_blank" rel="noopener">{label}</a>
              ))}
            </div>
          </details>
        </div>
      </div>

      <div className="kpis">
        <div className="kpi"><b>{fmt(s.events)}</b><span>événements tracés</span></div>
        <div className="kpi"><b>{ok}/{recon.length}</b><span>chiffres recalculés qui concordent</span></div>
        <div className="kpi"><b className={bad ? "bad" : ""}>{bad}</b><span>écart(s) détecté(s)</span></div>
        <div className="kpi"><b>{s.pending_review}</b><span>point(s) en attente d'un humain</span></div>
      </div>

      <h3>Poser une question à l'Auditor</h3>
      <form className="ask" onSubmit={ask}>
        <input value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={500} aria-label="Question"
          placeholder="Ex. : Why is the average order value 1,525.05 ? What happened to the freight outliers ?" />
        <button className="primary" disabled={busy || question.trim().length < 3}>{busy ? "…" : "Demander"}</button>
      </form>
      {error && <p className="error">{error}</p>}
      {asked && (
        <div className="llm-box">
          <b>{asked.q}</b>
          <p>{asked.answer}</p>
          <span className="muted small">Réponse {ANSWER[asked.status] || asked.status} · preuves citées : {(asked.cited || []).join(", ") || "aucune"}</span>
        </div>
      )}

      <h3>Réconciliation des chiffres publiés</h3>
      <p className="muted small">
        L'Auditor recalcule chaque chiffre avec son propre code à partir de la table à plat de l'Agent 4 et le compare à la valeur publiée.
        Une concordance prouve que l'agrégation suit la définition, pas que la définition ou les données sont justes.
      </p>
      <div className="scroll">
        <table>
          <thead><tr><th>KPI</th><th>Portée</th><th>Période</th><th>Publié</th><th>Recalculé</th><th /></tr></thead>
          <tbody>
            {recon.map((r, i) => (
              <tr key={i}>
                <td><span className="col">{r.kpi_id}</span></td>
                <td className="small">{r.scope_type === "all" ? "global" : `${r.scope_type} : ${r.scope_value}`}</td>
                <td className="small">{r.period}</td>
                <td className="num">{fmt(r.published)}</td>
                <td className="num">{fmt(r.recomputed)}</td>
                <td><span className={"badge " + (STATUS[r.status]?.[1] || "")}>{STATUS[r.status]?.[0] || r.status}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {(a.review_queue || []).length > 0 && (
        <>
          <h3>Points à faire valider par un humain</h3>
          <div className="scroll">
            <table>
              <thead><tr><th>Événement</th><th>Colonne</th><th>Règle</th><th>Statut</th><th>Raison</th></tr></thead>
              <tbody>
                {a.review_queue.map((r) => (
                  <tr key={r.event_id}>
                    <td><span className="col">{r.event_id}</span></td>
                    <td>{r.column}</td>
                    <td className="small">{r.rule_id}</td>
                    <td><span className={"badge " + (r.approval_status === "pending" ? "b-warn" : "b-ok")}>{r.approval_status === "pending" ? "en attente" : "validé"}</span></td>
                    <td className="muted small">{r.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {(a.explanations || []).length > 0 && (
        <>
          <h3>Explications vérifiées</h3>
          <ul className="xai-list">
            {a.explanations.map((x, i) => (
              <li key={i}>
                <details>
                  <summary><span className="col">{x.subject_id}</span> {x.question} <span className="muted small">· {ANSWER[x.status] || x.status}</span></summary>
                  <p className="small">{x.answer}</p>
                  <span className="muted small">Preuves : {x.evidence_ids}</span>
                </details>
              </li>
            ))}
          </ul>
        </>
      )}

      {(a.lineage || []).length > 0 && (
        <>
          <h3>Lignée : visuel → KPI → nettoyage</h3>
          <div className="scroll">
            <table>
              <thead><tr><th>Visuel</th><th>KPI</th><th>Règles de nettoyage</th><th>Problèmes de profilage</th></tr></thead>
              <tbody>
                {a.lineage.map((l, i) => (
                  <tr key={i}>
                    <td>{l.visual_title}</td>
                    <td><span className="col">{l.kpi_id}</span> {l.kpi_name}</td>
                    <td className="small">{String(l.cleaning_rules || "—").split("|").join(" · ")}</td>
                    <td className="small">{String(l.profiler_issues || "—").split("|").join(" · ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <h3>Limites de cet audit</h3>
      <ul className="limits">
        {(a.limitations || []).map((l, i) => <li key={i} className="small">{l}</li>)}
        <li className="small">Le texte d'un modèle de langage est vérifié (ids cités, nombres), pas dans toutes ses nuances ; les preuves déterministes font foi.</li>
      </ul>
    </div>
  );
}
