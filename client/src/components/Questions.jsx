import { useState } from "react";

import { postJson } from "../api.js";
import { fmt } from "../format.js";

const CHOICES = { empty: "Mettre à vide", cap: "Ramener à la limite", keep: "Garder" };
const advice = (q) => q.llm_advice || q.recommended || "keep";

// Seule validation demandée à l'utilisateur : les valeurs extrêmes, avant l'outil cap_outliers.
export default function Questions({ runId, questions, onDone }) {
  const [choices, setChoices] = useState(() => Object.fromEntries(questions.map((q) => [q.id, advice(q)])));
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");

  const send = async () => {
    setSending(true);
    try {
      await postJson(`/api/runs/${runId}/answers`, { answers: choices });
      onDone();
    } catch (e) {
      setError(e.message);
      setSending(false);
    }
  };
  const setAll = (fn) => setChoices(Object.fromEntries(questions.map((q) => [q.id, fn(q)])));

  return (
    <div>
      <h2>Des valeurs extrêmes ont été trouvées</h2>
      <p className="ask-intro muted">
        Rien n'est modifié sans votre accord. « Mettre à vide » efface les valeurs listées (erreurs de saisie),
        « Ramener à la limite » les remplace par la plus grande (ou plus petite) valeur normale, « Garder » ne change rien.
      </p>
      {questions.map((q) => {
        const limit = q.n_high && !q.n_low ? `Ramener à ${fmt(q.high)}` : q.n_low && !q.n_high ? `Ramener à ${fmt(q.low)}` : CHOICES.cap;
        return (
          <div key={q.id} className="question">
            <div className="q-head">
              <div>
                <div className="q-title">{q.table} · {q.column}</div>
                <div className="muted">Valeur habituelle : {fmt(q.median)} · valeurs normales de {fmt(q.low)} à {fmt(q.high)}</div>
                {q.profiling_issue && (
                  <div className="from-prof">
                    Déjà signalé par le profilage : <b>{q.profiling_issue.issue_id}</b> · {q.profiling_issue.description}
                  </div>
                )}
              </div>
              <div className="segmented seg3" role="radiogroup" aria-label={`${q.table} ${q.column}`}>
                {[["empty", CHOICES.empty], ["cap", limit], ["keep", CHOICES.keep]].map(([v, label]) => (
                  <label key={v}>
                    <input type="radio" name={`q-${q.id}`} value={v} checked={choices[q.id] === v}
                           onChange={() => setChoices({ ...choices, [q.id]: v })} />
                    <span>{label}</span>
                  </label>
                ))}
              </div>
            </div>
            {q.rows?.length > 0 && (
              <div className="scroll">
                <table className="q-rows">
                  <thead><tr><th>Ligne</th><th>Élément</th><th>{q.column}</th><th>Diagnostic</th></tr></thead>
                  <tbody>
                    {q.rows.map((r) => (
                      <tr key={r.row}>
                        <td className="row-id">{r.row}</td>
                        <td>{r.label || "—"}</td>
                        <td className="num">{fmt(r.value)}</td>
                        <td><span className={"chip " + (r.error ? "c-bad" : "c-ok")}>{r.error ? "erreur probable" : "possible"}</span> {r.diagnosis}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="advice">
              {q.llm_advice
                ? <>Le LLM conseille : <b>{CHOICES[q.llm_advice].toLowerCase()}</b>{q.llm_reason ? ` (${q.llm_reason})` : ""}</>
                : <>Conseil : <b>{CHOICES[q.recommended || "keep"].toLowerCase()}</b>
                  {q.recommended === "empty" ? " (erreur de saisie probable : la valeur ne doit pas fausser les totaux)" : " (valeurs rares mais crédibles)"}</>}
            </div>
          </div>
        );
      })}
      {error && <p className="error">{error}</p>}
      <div className="ask-actions">
        <div className="actions">
          <button className="link" onClick={() => setAll(advice)}>Suivre tous les conseils</button>
          <button className="link" onClick={() => setAll(() => "keep")}>Tout garder</button>
        </div>
        <button className="primary" disabled={sending} onClick={send}>{sending ? "Envoi…" : "Valider et continuer"}</button>
      </div>
    </div>
  );
}
