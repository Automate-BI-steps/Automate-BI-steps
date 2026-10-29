// Frise des agents : qui a fini, qui travaille, qui attend (lu dans state.run et state.timeline).
const STEPS = [
  { key: "profiling", title: "Agent 1 · Profilage", by: "Chiheb" },
  { key: "cleaning", title: "Agent 2 · Nettoyage", by: "Bedis" },
  { key: "verification", title: "Contrôle", by: "second profilage" },
  { key: "etl", title: "Agent 3 · ETL", by: "schéma en étoile" },
  { key: "semantic_kpi", title: "Agent 4 · Semantic & KPI", by: "calcul des KPI" },
];

function stepState(step, state) {
  const { status, current_agent: agent } = state.run || {};
  if (status === "error" && agent === step.key) return "error";
  // un run repris (réponses de l'utilisateur) peut repasser par une étape : on regarde le dernier événement
  const last = [...(state.timeline || [])].reverse().find((n) => n.agent === step.key);
  if (agent === step.key && status !== "error") return status === "waiting_user" ? "waiting" : "active";
  if (last?.event === "done") return "done";
  return "todo";
}

const LABEL = { done: "terminé", active: "en cours", waiting: "attend votre accord", todo: "à venir", error: "erreur" };

export default function Pipeline({ state, live }) {
  const notes = state.timeline || [];
  return (
    <ol className="pipeline">
      {STEPS.map((s, i) => {
        const st = stepState(s, state);
        const note = [...notes].reverse().find((n) => n.agent === s.key);
        return (
          <li key={s.key} className={"p-step " + st}>
            <div className="p-dot">{st === "done" ? "✓" : st === "error" ? "!" : i + 1}</div>
            <div className="p-body">
              <div className="p-title">{s.title} <span className="muted">· {s.by}</span></div>
              <div className="p-status">
                {LABEL[st]}{st === "active" && live?.running && <span className="mini-spinner" />}
              </div>
              {note && <div className="p-note">{note.summary}</div>}
            </div>
            {i < STEPS.length - 1 && (
              <div className="p-handoff" title="Le state est transmis à l'agent suivant">
                state.json →
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
