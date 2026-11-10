// Frise des agents (une ligne) : qui a fini, qui travaille, qui attend (lu dans state.run et state.timeline).
// Un clic sur une étape ouvre son onglet ; la note de l'étape en cours s'affiche sous la frise.
const STEPS = [
  { key: "profiling", tab: "profiling", title: "Profilage", by: "Agent 1" },
  { key: "cleaning", tab: "cleaning", title: "Nettoyage", by: "Agent 2" },
  { key: "verification", tab: "bilan", title: "Contrôle", by: "2d profilage" },
  { key: "etl", tab: "etl", title: "ETL", by: "Agent 3" },
  { key: "semantic_kpi", tab: "kpi", title: "KPI", by: "Agent 4" },
  { key: "dashboard", tab: "dashboard", title: "Dashboard", by: "Agent 5" },
  { key: "auditor", tab: "auditor", title: "Audit", by: "Agent 6" },
];

function stepState(step, state) {
  const { status, current_agent: agent } = state.run || {};
  if (status === "error" && agent === step.key) return "error";
  // un run repris (réponses de l'utilisateur) peut repasser par une étape : on regarde le dernier événement
  const last = [...(state.timeline || [])].reverse().find((n) => n.agent === step.key);
  if (agent === step.key && status !== "error") return status === "waiting_user" ? "waiting" : "active";
  if (last?.event === "done") return "done";
  if (last?.event === "error") return "error";
  return "todo";
}

const LABEL = { done: "terminé", active: "en cours", waiting: "votre accord", todo: "à venir", error: "erreur" };

export default function Pipeline({ state, live, onSelect }) {
  const notes = state.timeline || [];
  const states = STEPS.map((s) => stepState(s, state));
  const done = states.filter((s) => s === "done").length;
  const current = STEPS.find((_, i) => states[i] === "active" || states[i] === "waiting");
  const note = current && [...notes].reverse().find((n) => n.agent === current.key);
  return (
    <div className="card pipe-card">
      <ol className="stepper" aria-label={`Avancement : ${done} étape(s) sur ${STEPS.length}`}>
        {STEPS.map((s, i) => {
          const st = states[i];
          const last = [...notes].reverse().find((n) => n.agent === s.key);
          return (
            <li key={s.key} className={"st " + st}>
              <button onClick={() => onSelect?.(s.tab)} title={last?.summary || LABEL[st]}>
                <span className="st-dot">{st === "done" ? "✓" : st === "error" ? "!" : st === "active" ? <span className="mini-spinner" /> : i + 1}</span>
                <span className="st-title">{s.title}</span>
                <span className="st-sub">{s.by} · {LABEL[st]}</span>
              </button>
            </li>
          );
        })}
      </ol>
      <div className="pipe-foot">
        <div className="progress" aria-hidden="true"><span style={{ width: `${(done / STEPS.length) * 100}%` }} /></div>
        <span className="small muted">
          {current ? <><b>{current.title}</b> {live?.running ? "en cours" : ""} : {note?.summary || "…"}</>
            : done === STEPS.length ? "Toutes les étapes sont terminées." : `${done}/${STEPS.length} étapes terminées.`}
        </span>
      </div>
    </div>
  );
}
