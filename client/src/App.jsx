import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api.js";
import BilanView from "./components/BilanView.jsx";
import Busy from "./components/Busy.jsx";
import Failure from "./components/Failure.jsx";
import CleaningView from "./components/CleaningView.jsx";
import EtlView from "./components/EtlView.jsx";
import KpiView from "./components/KpiView.jsx";
import MemoryView from "./components/MemoryView.jsx";
import Pipeline from "./components/Pipeline.jsx";
import ProfilingView from "./components/ProfilingView.jsx";
import Questions from "./components/Questions.jsx";
import Sidebar from "./components/Sidebar.jsx";

const MOVING = new Set(["created", "profiling", "profiled", "cleaning", "cleaned", "verifying", "verified", "etl", "etl_done", "semantic_kpi"]);
const TABS = [
  ["profiling", "Profilage"],
  ["cleaning", "Nettoyage"],
  ["bilan", "Bilan"],
  ["etl", "ETL (Agent 3)"],
  ["kpi", "KPI (Agent 4)"],
  ["memory", "Mémoire (state)"],
];

// Onglet à montrer quand le run change d'étape.
function tabFor(status) {
  if (status === "waiting_user" || status === "cleaned" || status === "cleaning" || status === "verifying") return "cleaning";
  if (status === "etl" || status === "etl_done") return "etl";
  if (status === "semantic_kpi") return "kpi";
  if (status === "done") return "bilan";
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
        if (d.live?.running || (MOVING.has(status) && !d.live?.error)) timer = setTimeout(tick, 900);
      } catch (e) {
        if (!stop) setError(e.message);
      }
    };
    tick();
    return () => { stop = true; clearTimeout(timer); };
  }, [runId, loadRuns]);

  const start = (id) => { setError(null); setData(null); setRunId(id); };
  const resume = () => { const id = runId; setRunId(null); setTimeout(() => setRunId(id), 0); };

  const state = data?.state;
  const status = state?.run?.status;
  const failure = data?.live?.error || (status === "error" && (state?.errors || []).slice(-1)[0]?.error);

  return (
    <>
      <header className="top">
        <div>
          <h1>BI Flow <span className="muted">· Profilage → Nettoyage</span></h1>
          <p className="muted">
            Déposez vos fichiers CSV ou Excel : l'agent de profilage diagnostique chaque table, l'agent de nettoyage
            corrige à partir de ce diagnostic, puis un second profilage mesure le gain. Le state passe d'un agent à l'autre.
          </p>
        </div>
        {config && (
          <span className={"badge " + (config.groq_key_configured ? "b-ok" : "b-warn")}>
            {config.groq_key_configured ? "LLM prêt" : "Clé Groq à saisir pour le LLM"}
          </span>
        )}
      </header>

      <main className="layout">
        <Sidebar config={config} runs={runs} current={runId} onStart={start} onOpen={start} />

        <section className="panel results" aria-live="polite">
          {error && <p className="error">{error}</p>}
          {!runId && <div className="empty muted">Déposez des fichiers puis cliquez sur « Lancer les agents ».</div>}
          {runId && !state && !failure && <Busy text="Préparation du run…" />}
          {!state && failure && <Failure message={failure} log={data?.live?.log} />}
          {state && (
            <>
              <Pipeline state={state} live={data.live} />
              {failure && <Failure message={failure} log={data.live?.log} />}
              <div className="tabs main-tabs" role="tablist">
                {TABS.map(([key, label]) => (
                  <button key={key} role="tab" aria-selected={tab === key} onClick={() => setTab(key)}>
                    {label}
                    {key === "cleaning" && status === "waiting_user" && <span className="count warn">à valider</span>}
                    {key === "etl" && state.etl?.status === "error" && <span className="count warn">erreur</span>}
                    {key === "kpi" && state.kpi?.status === "error" && <span className="count warn">erreur</span>}
                  </button>
                ))}
              </div>
              <div role="tabpanel" className="tab-panel">
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
                {tab === "memory" && <MemoryView runId={runId} state={state} />}
              </div>
            </>
          )}
        </section>
      </main>
    </>
  );
}
