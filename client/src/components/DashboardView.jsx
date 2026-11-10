import { fmt } from "../format.js";

const MODE = { generic: "générique", trade: "import / export" };
const TYPE = {
  card: "carte", clusteredBarChart: "barres", clusteredColumnChart: "colonnes", lineChart: "courbe", areaChart: "aires",
  donutChart: "anneau", pieChart: "secteurs", treemap: "treemap", gauge: "jauge", tableEx: "tableau", slicer: "segment",
  lineClusteredColumnComboChart: "colonnes + courbe", multiRowCard: "carte",
};

// Valeur d'un KPI de l'Agent 4 pour une carte : nombre formaté selon l'unité, texte, ou premier élément d'un classement.
function cardValue(r) {
  const v = r?.valeur;
  if (v == null) return "—";
  if (typeof v === "string") return v;
  if (Array.isArray(v)) return v[0] ? `${v[0].nom} · ${fmt(v[0].part_pct)} %` : "—";
  if (typeof v === "object") {
    const g = v.croissance_pct || {};
    const last = Object.keys(g).pop();
    return last ? `${fmt(g[last])} % (${last})` : "—";
  }
  const unit = (r.unite || "").toLowerCase();
  if (unit === "%") return `${fmt(v)} %`;
  if (unit === "jours") return `${fmt(v)} j`;
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toLocaleString("fr-FR", { maximumFractionDigits: 2 })} M`;
  if (Math.abs(v) >= 1e4) return `${(v / 1e3).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} k`;
  return fmt(v);
}

// Résultat de l'Agent 5 (section `dashboard` du state) : projet Power BI (PBIP) généré à partir de la table à plat,
// du schéma en étoile et du catalogue de l'Agent 4. Plus d'aperçu HTML : l'appli montre les pages du rapport et leurs KPI.
export default function DashboardView({ runId, dashboard, kpi }) {
  const d = dashboard || {};
  const kpis = d.kpis || [];
  const skipped = Object.fromEntries((d.kpis_skipped || []).map((k) => [k.kpi_id, k.reason]));
  const results = kpi?.kpi_results || {};
  const pages = d.pages || [];
  const base = `/api/runs/${runId}`;

  if (d.status === "ERROR" || !d.artifacts?.pbip_dir) {
    return (
      <div>
        <h2>Agent 5 · Tableau de bord Power BI</h2>
        <p className="error">Aucun tableau de bord généré. {(d.errors || []).join(" · ") || "Voir l'onglet Mémoire pour le détail."}</p>
      </div>
    );
  }

  const sent = kpis.filter((k) => k.transmitted).length;
  const notes = [...(d.prep_warnings || []), ...(d.warnings || [])];
  return (
    <div>
      <div className="res-head">
        <div>
          <h2>Agent 5 · Tableau de bord Power BI</h2>
          <div className="muted small">Mode {MODE[d.input_mode] || d.input_mode} · {pages.length} page(s) · {sent}/{kpis.length} KPI transmis · critique {String(d.artifacts?.critic_status || "—").toLowerCase()}</div>
        </div>
        <div className="actions">
          <a className="btn primary" href={`${base}/dashboard.zip`}>Télécharger le projet Power BI (.zip)</a>
        </div>
      </div>

      <div className="steps3">
        <div><span>1</span>Dézipper le projet</div>
        <div><span>2</span>Ouvrir <code>dashboard_generator.pbip</code></div>
        <div><span>3</span><b>Accueil › Actualiser</b> (sinon « (Vide) »)</div>
      </div>
      <p className="muted small">Dossier déplacé ? <code>python relier_power_bi.py</code> dans le dossier dézippé. Détail : <code>LIRE_MOI_POWER_BI.md</code> dans le zip.
        Le rendu dans Power BI Desktop n'est pas vérifiable par Python (statut « {d.status} »).</p>

      {pages.map((p) => {
        const cards = p.visuals.filter((v) => v.type === "card");
        const charts = p.visuals.filter((v) => v.type !== "card");
        return (
          <section key={p.name} className="pbi-page">
            <div className="pbi-head">
              <div><span className="pbi-tag">Page</span><b>{p.title}</b></div>
              <span className="muted small">{p.description}</span>
            </div>
            {cards.length > 0 && (
              <div className="pbi-cards">
                {cards.map((c) => (
                  <div key={c.title} className="pbi-card" title={results[c.kpi]?.definition || ""}>
                    <b>{cardValue(results[c.kpi])}</b><span>{c.title}</span>
                  </div>
                ))}
              </div>
            )}
            <div className="chips">
              {charts.map((v) => <span key={v.title} className="chip-v"><i>{TYPE[v.type] || v.type}</i>{v.title}</span>)}
            </div>
          </section>
        );
      })}

      <details className="callout">
        <summary><b>KPI transmis au tableau de bord</b> <span className="muted small">({sent}/{kpis.length})</span></summary>
        <ul className="kpi-chips">
          {kpis.map((k) => (
            <li key={k.id} className={k.transmitted ? "ok" : "ko"} title={skipped[k.id] || ""}>{k.name}{!k.transmitted && " · écarté"}</li>
          ))}
        </ul>
      </details>

      {notes.length > 0 && (
        <details className="callout">
          <summary><b>Notes du générateur</b> <span className="muted small">({notes.length})</span></summary>
          <ul className="prof-issues">
            {notes.map((w, i) => <li key={i}><span className="sev sev-info">Note</span><span className="small">{w}</span></li>)}
          </ul>
        </details>
      )}
    </div>
  );
}
