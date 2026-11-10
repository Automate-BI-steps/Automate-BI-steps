import { useState } from "react";

import { api } from "../api.js";
import { dateTime, fmt, scoreClass } from "../format.js";

const STATUS = {
  created: "créé", profiling: "profilage", profiled: "profilé", cleaning: "nettoyage", waiting_user: "à valider",
  cleaned: "nettoyé", verifying: "contrôle", verified: "contrôlé", etl: "ETL", etl_done: "ETL terminé", semantic_kpi: "KPI", kpi_done: "KPI calculés",
  dashboard: "tableau de bord", dashboard_done: "tableau de bord prêt", auditor: "audit", done: "terminé", error: "erreur",
};
const STEPS = [
  ["Profilage", "diagnostic de chaque table"], ["Nettoyage", "corrections tracées, vous validez les valeurs extrêmes"],
  ["ETL", "schéma en étoile"], ["KPI", "indicateurs commerciaux, financiers et logistiques"],
  ["Dashboard", "projet Power BI par rôle"], ["Audit", "chaque chiffre recalculé, rapport PDF"],
];

const KEY_SRC = { agent: "clé propre", shared: "clé partagée" };

// Une clé LLM par agent (variables GROQ_API_KEY_AGENTn du .env) : l'état de chacune, jamais la clé.
export function LlmKeys({ agents = [], enabled = true }) {
  const ready = agents.filter((a) => a.key).length;
  return (
    <details className="llm-keys">
      <summary>
        <span className={"dot-status " + (enabled && ready ? "ok" : "warn")}>
          {enabled ? `LLM : ${ready}/${agents.length} agents avec clé` : "LLM coupé (BI_FLOW_USE_LLM)"}
        </span>
      </summary>
      <ul>
        {agents.map((a) => (
          <li key={a.agent} title={a.var}>
            <span>A{a.agent} {a.name}</span><i className={a.key || "none"}>{KEY_SRC[a.key] || "sans clé"}</i>
          </li>
        ))}
      </ul>
      <small>Une clé par agent dans <code>.env</code> : <code>GROQ_API_KEY_AGENT1</code>… (repli : <code>GROQ_API_KEY</code>).</small>
    </details>
  );
}

// Historique des runs (barre de gauche).
export function RunHistory({ runs, current, onOpen }) {
  return (
    <div className="history-wrap">
      <div className="rail-title">Derniers runs</div>
      <ul className="history">
        {!runs.length && <li className="muted small">Aucun run pour l'instant</li>}
        {runs.map((r) => (
          <li key={r.run_id}>
            <button className={r.run_id === current ? "active" : ""} onClick={() => onOpen(r.run_id)} aria-current={r.run_id === current ? "true" : undefined}>
              <span className="h-files">{r.files.slice(0, 2).join(", ")}{r.files.length > 2 ? "…" : ""}</span>
              <small>
                <i className={"h-dot " + (r.status === "done" ? "ok" : r.status === "error" ? "bad" : r.status === "waiting_user" ? "warn" : "run")} />
                {dateTime(r.created_at)} · {STATUS[r.status] || r.status}
                {r.score_before != null && (
                  <> · <b className={scoreClass(r.score_before)}>{fmt(r.score_before)}</b>
                    {r.score_after != null && <> → <b className={scoreClass(r.score_after)}>{fmt(r.score_after)}</b></>}</>
                )}
              </small>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

// Écran « Nouveau run » : dépôt des fichiers, options repliées, lancement.
export function NewRun({ config, onStart }) {
  const [files, setFiles] = useState([]);
  const [fileError, setFileError] = useState("");
  const [over, setOver] = useState(false);
  const [planner, setPlanner] = useState(null); // null : suit le choix du serveur (LLM dès que l'Agent 2 a une clé)
  const mode = planner ?? config?.default_planner ?? "rules";
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
    fd.append("planner", mode);
    fd.append("model", mode === "llm" ? model || config.default_model : "");
    fd.append("groq_api_key", key);
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
  const size = files.reduce((s, f) => s + f.size, 0);

  return (
    <div className="new-grid">
      <section className="card">
        <h2 className="step-h"><span>1</span> Déposez vos tables</h2>
        <label
          className={"dropzone" + (over ? " over" : "")}
          onDragEnter={(e) => { e.preventDefault(); setOver(true); }}
          onDragOver={(e) => { e.preventDefault(); setOver(true); }}
          onDragLeave={() => setOver(false)}
          onDrop={drop}
        >
          <input type="file" multiple hidden accept={config?.allowed_extensions.join(",")}
                 onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
          <span className="dz-icon" aria-hidden="true">⇪</span>
          <strong>Glissez vos fichiers ici ou cliquez pour choisir</strong>
          <span className="muted small">{config?.allowed_extensions.join(", ")} · {config?.max_file_mb} Mo max par fichier · {config?.max_files} fichiers max</span>
        </label>
        {fileError && <p className="error">{fileError}</p>}
        {files.length > 0 && (
          <ul className="file-list">
            {files.map((f, i) => (
              <li key={f.name}>
                <span className="f-ext">{f.name.split(".").pop()}</span>
                <span className="f-name" title={f.name}>{f.name}</span>
                <small className="muted">{f.size < 1024 ? "< 1 Ko" : `${Math.round(f.size / 1024)} Ko`}</small>
                <button aria-label={`Retirer ${f.name}`} onClick={() => setFiles(files.filter((_, j) => j !== i))}>×</button>
              </li>
            ))}
          </ul>
        )}

        <h2 className="step-h"><span>2</span> Options <small className="muted">(facultatif)</small></h2>
        <label className="field">Contexte métier
          <input value={context} onChange={(e) => setContext(e.target.value)} placeholder="ex. ventes B2B, commandes et livraisons" />
        </label>
        <div className="field">Analyse du nettoyage
          <div className="segmented" role="radiogroup" aria-label="Analyse du nettoyage">
            {[["rules", "Règles seules (rapide)"], ["llm", "Avec le LLM"]].map(([v, label]) => (
              <label key={v}>
                <input type="radio" name="planner" value={v} checked={mode === v} onChange={() => setPlanner(v)} />
                <span>{label}</span>
              </label>
            ))}
          </div>
        </div>
        {mode === "llm" && config && (
          <div className="row2">
            <label className="field">Modèle
              <select value={model || config.default_model} onChange={(e) => setModel(e.target.value)}>
                {config.models.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </label>
            {!config.groq_key_configured && (
              <label className="field">Clé Groq (ce run seulement)
                <input type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder="gsk_…" autoComplete="off" />
              </label>
            )}
          </div>
        )}
        <details className="advanced">
          <summary>Options avancées</summary>
          <label className="check">
            <input type="checkbox" checked={impute} onChange={(e) => setImpute(e.target.checked)} />
            Compléter les cases vides (médiane ou valeur la plus fréquente)
          </label>
          {config && config.etl_provider === "ollama" && !config.ollama_key_configured && (
            <label className="field">Clé Ollama Cloud pour l'agent ETL (ce run seulement)
              <input type="password" value={ollamaKey} onChange={(e) => setOllamaKey(e.target.value)} placeholder="ex. b7f2…" autoComplete="off" />
              <span className="muted small">Sans clé, l'Agent 3 s'arrête ; le tableau de bord reconstruit quand même son schéma en étoile.</span>
            </label>
          )}
        </details>

        <div className="launch">
          <span className="muted small">{files.length ? `${files.length} fichier(s) · ${Math.max(1, Math.round(size / 1024))} Ko` : "Aucun fichier"}</span>
          <button className="btn primary big" disabled={!files.length || sending} onClick={launch}>
            {sending ? "Envoi…" : "Lancer les 6 agents →"}
          </button>
        </div>
      </section>

      <aside className="card how">
        <h2>Ce qui va se passer</h2>
        <ol className="how-steps">
          {STEPS.map(([t, d], i) => (
            <li key={t}><span className="hs-num">{i + 1}</span><div><b>{t}</b><span className="muted small">{d}</span></div></li>
          ))}
        </ol>
        <p className="muted small">Vous n'intervenez qu'une fois : pour valider le traitement des valeurs extrêmes. Le reste s'enchaîne seul ;
          à la fin, téléchargez le projet Power BI et le rapport d'audit en PDF.</p>
      </aside>
    </div>
  );
}
