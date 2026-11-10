import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api.js";
import AuditorView from "./components/AuditorView.jsx";
import BilanView from "./components/BilanView.jsx";
import Busy from "./components/Busy.jsx";
import CleaningView from "./components/CleaningView.jsx";
import DashboardView from "./components/DashboardView.jsx";
import EtlView from "./components/EtlView.jsx";
import Failure from "./components/Failure.jsx";
import KpiView from "./components/KpiView.jsx";
import MemoryView from "./components/MemoryView.jsx";
import Pipeline from "./components/Pipeline.jsx";
import ProfilingView from "./components/ProfilingView.jsx";
import Questions from "./components/Questions.jsx";
import { LlmKeys, NewRun, RunHistory } from "./components/Sidebar.jsx";

const MOVING = new Set(["created", "profiling", "profiled", "cleaning", "cleaned", "verifying", "verified", "etl", "etl_done", "semantic_kpi", "kpi_done", "dashboard", "dashboard_done", "auditor"]);
const TABS = [
  ["profiling", "Profilage", "1"],
  ["cleaning", "Nettoyage", "2"],
  ["bilan", "Bilan", "✓"],
  ["etl", "ETL", "3"],
  ["kpi", "KPI", "4"],
  ["dashboard", "Dashboard", "5"],
  ["auditor", "Audit", "6"],
  ["memory", "Mémoire", "{}"],
];
const STATUS = {
  created: ["créé", ""], profiling: ["profilage", "run"], profiled: ["profilé", "run"], cleaning: ["nettoyage", "run"],
  waiting_user: ["attend votre accord", "wait"], cleaned: ["nettoyé", "run"], verifying: ["contrôle", "run"], verified: ["contrôlé", "run"],
  etl: ["ETL", "run"], etl_done: ["ETL terminé", "run"], semantic_kpi: ["KPI", "run"], kpi_done: ["KPI calculés", "run"],
  dashboard: ["tableau de bord", "run"], dashboard_done: ["tableau de bord prêt", "run"], auditor: ["audit", "run"],
  done: ["terminé", "ok"], error: ["erreur", "bad"],
};

// Onglet à montrer quand le run change d'étape.
function tabFor(status) {
  if (status === "waiting_user" || status === "cleaned" || status === "cleaning" || status === "verifying") return "cleaning";
  if (status === "etl" || status === "etl_done") return "etl";
  if (status === "semantic_kpi" || status === "kpi_done") return "kpi";
  if (status === "dashboard" || status === "dashboard_done") return "dashboard";
  if (status === "auditor" || status === "done") return "auditor";
  return "profiling";
}

export default function App() {
  const [config, setConfig] = useState(null);
  const [runs, setRuns] = useState([]);
  const [runId, setRunId] = useState(null);
  const [data, setData] = useState(null); // { state, live }
  const [view, setView] = useState(null); // vue avant / après de l'agent de nettoyage
  const [tab, setTab] = useState("profiling");
  const [error, setError] = useState(null);
  const [menu, setMenu] = useState(false); // historique replié sur petit écran
  const lastStatus = useRef(null);

  const loadRuns = useCallback(() => api("/api/runs").then(setRuns).catch(() => {}), []);

  useEffect(() => {
    api("/api/config").then(setConfig).catch((e) => setError(e.message));
    loadRuns();
  }, [loadRuns]);

  // Suivi du run : on relit le state tant qu'un agent travaille.
  useEffect(() => {
    if (!runId) return undefined;
    let stop = false;
    let timer;
    lastStatus.current = null;
    setView(null);
    const tick = async () => {
      try {
        const d = await api(`/api/runs/${runId}`);
        if (stop) return;
        setData(d);
        const status = d.state?.run?.status || "created";
        if (status !== lastStatus.current) {
          lastStatus.current = status;
          setTab(tabFor(status));
          loadRuns();
          if (d.state?.cleaning?.status === "done") api(`/api/runs/${runId}/view`).then(setView).catch(() => {});
        }
        if (d.live?.running || (MOVING.has(status) && !d.live?.error)) timer = setTimeout(tick, 1200);
      } catch (e) {
        if (!stop) setError(e.message);
      }
    };
    tick();
    return () => { stop = true; clearTimeout(timer); };
  }, [runId, loadRuns]);

  const open = (id) => { setError(null); setData(null); setRunId(id); setMenu(false); };
  const newRun = () => { setError(null); setData(null); setRunId(null); setMenu(false); };
  const resume = () => { const id = runId; setRunId(null); setTimeout(() => setRunId(id), 0); };

  const state = data?.state;
  const status = state?.run?.status;
  const failure = data?.live?.error || (status === "error" && (state?.errors || []).slice(-1)[0]?.error);
  const files = state?.input?.uploads || [];
  const [statusLabel, statusTone] = STATUS[status] || [status, ""];
  const pbi = state?.dashboard?.artifacts?.project_zip;
  const pdf = state?.auditor?.artifacts?.report_pdf;
  const base = `/api/runs/${runId}`;

  return (
    <div className="app">
      <nav className={"rail" + (menu ? " open" : "")} aria-label="Runs">
        <div className="brand">
          <span className="logo" aria-hidden="true">▲</span>
          <div><b>BI Flow</b><small>6 agents · de vos fichiers à Power BI</small></div>
          <button className="rail-toggle" aria-label="Afficher l'historique" aria-expanded={menu} onClick={() => setMenu(!menu)}>☰</button>
        </div>
        <button className="new-run" onClick={newRun} aria-current={!runId ? "page" : undefined}>＋ Nouveau run</button>
        <RunHistory runs={runs} current={runId} onOpen={open} />
        <div className="rail-foot">
          {config && <LlmKeys agents={config.llm_agents} enabled={config.llm_enabled} />}
        </div>
      </nav>

      <div className="main">
        <header className="topbar">
          <div className="tb-title">
            <h1>{runId ? (files.length ? files.slice(0, 3).join(", ") + (files.length > 3 ? ` +${files.length - 3}` : "") : "Run") : "Nouveau run"}</h1>
            <div className="muted small">
              {runId
                ? <>Run <code>{runId}</code> · <span className={"pill " + statusTone}>{statusLabel || "préparation"}</span>
                  {data?.live?.running && data.live.step && <> · étape <b>{data.live.step}</b> en cours</>}</>
                : "Déposez vos tables : profilage, nettoyage, ETL, KPI, tableau de bord Power BI et audit s'enchaînent."}
            </div>
          </div>
          {runId && state && (
            <div className="tb-actions">
              {pbi && <a className="btn primary" href={`${base}/dashboard.zip`}>Power BI (.zip)</a>}
              {pdf && <a className="btn gold" href={`${base}/files/rapport_audit.pdf`} download>Rapport d'audit (PDF)</a>}
              {state.cleaning?.clean_data_dir && <a className="btn ghost" href={`${base}/download`}>Tout télécharger</a>}
            </div>
          )}
        </header>

        <main className="content" aria-live="polite">
          {error && <p className="error">{error}</p>}
          {!runId && <NewRun config={config} onStart={open} />}
          {runId && !state && !failure && <Busy text="Préparation du run…" />}
          {runId && !state && failure && <Failure message={failure} log={data?.live?.log} />}
          {state && (
            <>
              <Pipeline state={state} live={data.live} onSelect={setTab} />
              {failure && <Failure message={failure} log={data.live?.log} />}
              <div className="tabs main-tabs" role="tablist">
                {TABS.map(([key, label, num]) => (
                  <button key={key} role="tab" aria-selected={tab === key} onClick={() => setTab(key)}>
                    <span className="t-num">{num}</span>{label}
                    {key === "cleaning" && status === "waiting_user" && <span className="count warn">à valider</span>}
                    {key === "etl" && state.etl?.status === "error" && <span className="count warn">erreur</span>}
                    {key === "kpi" && state.kpi?.status === "error" && <span className="count warn">erreur</span>}
                    {key === "dashboard" && state.dashboard?.status === "ERROR" && <span className="count warn">erreur</span>}
                    {key === "auditor" && (state.auditor?.status === "ERROR" || state.auditor?.status === "WARN") && <span className="count warn">{state.auditor.status === "WARN" ? "écart" : "erreur"}</span>}
                  </button>
                ))}
              </div>
              <div role="tabpanel" className="card tab-panel">
                {tab === "profiling" && (state.profiling?.summary
                  ? <ProfilingView runId={runId} profiling={state.profiling} />
                  : <Busy text="L'agent de profilage analyse les fichiers…" />)}
                {tab === "cleaning" && (status === "waiting_user"
                  ? <Questions runId={runId} questions={state.cleaning.questions} onDone={resume} />
                  : view
                    ? <CleaningView runId={runId} view={view} resolution={state.cleaning?.issue_resolution} />
                    : <Busy text={state.profiling?.summary ? "L'agent de nettoyage travaille à partir du rapport de profilage…" : "En attente du profilage…"} />)}
                {tab === "bilan" && (state.cleaning?.verification
                  ? <BilanView cleaning={state.cleaning} />
                  : <Busy text="Le bilan sera prêt après le nettoyage et le second profilage…" />)}
                {tab === "etl" && (state.etl
                  ? <EtlView etl={state.etl} />
                  : <Busy text={state.cleaning?.verification ? "L'agent ETL construit le schéma en étoile…" : "En attente du nettoyage…"} />)}
                {tab === "kpi" && (state.kpi
                  ? <KpiView kpi={state.kpi} />
                  : <Busy text={state.cleaning?.verification ? "L'agent Semantic & KPI calcule les KPI…" : "En attente du nettoyage…"} />)}
                {tab === "dashboard" && (state.dashboard
                  ? <DashboardView runId={runId} dashboard={state.dashboard} kpi={state.kpi} />
                  : <Busy text={state.kpi?.status === "done" ? "L'agent Dashboard génère le projet Power BI…" : "En attente des KPI…"} />)}
                {tab === "auditor" && (state.auditor
                  ? <AuditorView runId={runId} auditor={state.auditor} config={config} />
                  : <Busy text={state.dashboard ? "L'Auditor relie chaque chiffre à ses sources…" : "En attente du tableau de bord…"} />)}
                {tab === "memory" && <MemoryView runId={runId} state={state} />}
              </div>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
