import { useState } from "react";

import { api } from "../api.js";
import { dateTime, fmt, scoreClass } from "../format.js";

const STATUS = {
  created: "créé", profiling: "profilage", profiled: "profilé", cleaning: "nettoyage", waiting_user: "à valider",
  cleaned: "nettoyé", verifying: "contrôle", done: "terminé", error: "erreur",
};

export default function Sidebar({ config, runs, current, onStart, onOpen }) {
  const [files, setFiles] = useState([]);
  const [fileError, setFileError] = useState("");
  const [over, setOver] = useState(false);
  const [planner, setPlanner] = useState("rules");
  const [model, setModel] = useState("");
  const [key, setKey] = useState("");
  const [ollamaKey, setOllamaKey] = useState("");
  const [context, setContext] = useState("");
  const [impute, setImpute] = useState(false);
  const [sending, setSending] = useState(false);

  const addFiles = (list) => {
    if (!config) return;
    const errors = [];
    const next = [...files];
    for (const f of list) {
      const ext = "." + f.name.split(".").pop().toLowerCase();
      if (!config.allowed_extensions.includes(ext)) { errors.push(`${f.name} : format non accepté`); continue; }
      if (f.size > config.max_file_mb * 1024 * 1024) { errors.push(`${f.name} : plus de ${config.max_file_mb} Mo`); continue; }
      if (next.some((x) => x.name === f.name)) continue;
      if (next.length >= config.max_files) { errors.push(`maximum ${config.max_files} fichiers`); break; }
      next.push(f);
    }
    setFiles(next);
    setFileError(errors.join(" · "));
  };

  const launch = async () => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    fd.append("planner", planner);
    fd.append("model", planner === "llm" ? model || config.default_model : "");
    fd.append("groq_api_key", planner === "llm" ? key : "");
    fd.append("ollama_api_key", ollamaKey);
    fd.append("context", context);
    fd.append("impute", impute);
    setSending(true);
    try {
      const { run_id } = await api("/api/runs", { method: "POST", body: fd });
      onStart(run_id);
    } catch (e) {
      setFileError(e.message);
    } finally {
      setSending(false);
    }
  };

  const drop = (ev) => { ev.preventDefault(); setOver(false); addFiles(ev.dataTransfer.files); };

  return (
    <aside className="panel">
      <h2>1. Vos fichiers</h2>
      <label
        className={"dropzone" + (over ? " over" : "")}
        onDragEnter={(e) => { e.preventDefault(); setOver(true); }}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={drop}
      >
        <input type="file" multiple hidden accept={config?.allowed_extensions.join(",")}
               onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
        <strong>Glissez vos fichiers ici</strong>
        <span className="muted">ou cliquez pour choisir · {config?.allowed_extensions.join(", ")}</span>
      </label>
      {fileError && <p className="error">{fileError}</p>}
      <ul className="file-list">
        {files.map((f, i) => (
          <li key={f.name}>
            <span title={f.name}>{f.name}</span>
            <small className="muted">{f.size < 1024 ? "< 1 Ko" : `${Math.round(f.size / 1024)} Ko`}</small>
            <button aria-label={`Retirer ${f.name}`} onClick={() => setFiles(files.filter((_, j) => j !== i))}>×</button>
          </li>
        ))}
      </ul>

      <h2>2. Options</h2>
      <label className="field">Contexte métier (aide le LLM)
        <input value={context} onChange={(e) => setContext(e.target.value)} placeholder="ex. ventes B2B import / export" />
      </label>
      <div className="segmented" role="radiogroup" aria-label="Analyse" style={{ marginTop: 12 }}>
        {[["rules", "Règles seules"], ["llm", "Avec le LLM"]].map(([v, label]) => (
          <label key={v}>
            <input type="radio" name="planner" value={v} checked={planner === v} onChange={() => setPlanner(v)} />
            <span>{label}</span>
          </label>
        ))}
      </div>
      {planner === "llm" && config && (
        <>
          <label className="field">Modèle
            <select value={model || config.default_model} onChange={(e) => setModel(e.target.value)}>
              {config.models.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
          {!config.groq_key_configured && (
            <label className="field">Clé Groq (pour ce run, non enregistrée)
              <input type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder="gsk_…" autoComplete="off" />
            </label>
          )}
        </>
      )}
      <label className="check">
        <input type="checkbox" checked={impute} onChange={(e) => setImpute(e.target.checked)} />
        Compléter les cases vides (médiane ou valeur la plus fréquente)
      </label>

      {config && !config.ollama_key_configured && (
        <label className="field">Clé Ollama Cloud (pour l'agent ETL, pour ce run, non enregistrée)
          <input type="password" value={ollamaKey} onChange={(e) => setOllamaKey(e.target.value)}
                 placeholder="ex. b7f2…" autoComplete="off" />
          <span className="muted small">Sans cette clé, l'agent ETL (Agent 3) ne produira pas de schéma en étoile.</span>
        </label>
      )}

      <button className="primary" disabled={!files.length || sending} onClick={launch}>
        {sending ? "Envoi…" : "Lancer les agents"}
      </button>

      <h2>Derniers runs</h2>
      <ul className="history">
        {!runs.length && <li className="muted">Aucun run pour l'instant</li>}
        {runs.map((r) => (
          <li key={r.run_id} className={r.run_id === current ? "active" : ""} onClick={() => onOpen(r.run_id)}>
            <div className="h-files">{r.files.slice(0, 2).join(", ")}{r.files.length > 2 ? "…" : ""}</div>
            <small>
              {dateTime(r.created_at)} · {STATUS[r.status] || r.status}
              {r.score_before != null && (
                <> · score <b className={scoreClass(r.score_before)}>{fmt(r.score_before)}</b>
                  {r.score_after != null && <> → <b className={scoreClass(r.score_after)}>{fmt(r.score_after)}</b></>}</>
              )}
            </small>
          </li>
        ))}
      </ul>
    </aside>
  );
}
