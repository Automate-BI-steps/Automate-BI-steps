"""Nœuds LangGraph de l'Agent 2 : nettoyage piloté par le rapport de profilage.

    profiling --(state.profiling : JSON + chemins des CSV)--> cleaning --> verification

- cleaning      : lit les CSV listés par le profilage (avec l'encodage et le séparateur détectés),
                  donne les problèmes du profilage au planificateur LLM, nettoie table par table.
- verification  : relance le profilage (sans LLM) sur les tables nettoyées et relie chaque
                  problème d'origine à son traitement (profiling_link.resolve_issues).

Le nettoyage peut s'arrêter pour demander l'accord de l'utilisateur (valeurs extrêmes) :
`start_cleaning` renvoie alors la session en attente, que l'orchestrateur conserve sur disque,
puis `finish_cleaning` applique les réponses. `cleaning_node` enchaîne les deux sans interface.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .agent import DEFAULT_HISTORY, DEFAULT_INPUT, CleaningSession
from .profiling_link import resolve_issues

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WORK_DIR = ROOT / "outputs" / "cleaning"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _work_dir(state: dict) -> Path:
    return Path(state.get("input", {}).get("work_dir") or DEFAULT_WORK_DIR)


@contextmanager
def _profiling_settings(out_dir: Path | None, use_llm: bool):
    """Règle le dossier de sortie et le LLM de l'Agent 1 sans modifier son code (variables de module)."""
    from src import config
    from src.agents.profiling import nodes as prof_nodes

    old_dir, old_key = prof_nodes.OUTPUT_DIR, os.environ.get("GROQ_API_KEY")
    if out_dir is not None:
        prof_nodes.OUTPUT_DIR = Path(out_dir)  # export_report écrit dans OUTPUT_DIR / "profiling"
    if not use_llm:
        os.environ["GROQ_API_KEY"] = ""
        config.get_llm.cache_clear()
    try:
        yield
    finally:
        prof_nodes.OUTPUT_DIR = old_dir
        if not use_llm:
            if old_key is None:
                os.environ.pop("GROQ_API_KEY", None)
            else:
                os.environ["GROQ_API_KEY"] = old_key
            config.get_llm.cache_clear()


def run_profiling(inp: dict, out_dir: Path | None = None, use_llm: bool = True) -> dict:
    """Lance le graphe de l'Agent 1 tel quel ; rapports dans <out_dir>/profiling si out_dir est donné."""
    from src.agents.profiling import build_profiling_graph  # import tardif : évite une boucle d'import

    with _profiling_settings(out_dir, use_llm):
        return build_profiling_graph().invoke({"input": inp})


def input_files(state: dict) -> list[Path]:
    """CSV transmis par le profilage (lus sans erreur), sinon le dossier source."""
    files = state.get("profiling", {}).get("metadata", {}).get("files", [])
    paths = [Path(f["path"]) for f in files if f.get("read_status") != "error"]
    return paths or [Path(state.get("input", {}).get("source_dir") or DEFAULT_INPUT)]


def start_cleaning(state: dict, api_key: str | None = None) -> CleaningSession:
    inp = state.get("input", {})
    work = _work_dir(state)
    return CleaningSession(input_files(state), work / "clean", Path(inp.get("history_dir") or DEFAULT_HISTORY),
                           impute=bool(inp.get("impute")), planner_mode=inp.get("planner", "rules"),
                           model=inp.get("model"), api_key=api_key, profiling=state.get("profiling"))


def waiting_update(session: CleaningSession, state: dict) -> dict:
    """Section `cleaning` quand des valeurs extrêmes attendent la décision de l'utilisateur."""
    return {"cleaning": {"status": "waiting_user", "run_id": session.tracer.run_id,
                         "profiling_run_id": state.get("profiling", {}).get("metadata", {}).get("run_id"),
                         "planner": session.tracer.planner, "questions": session.questions},
            "timeline": [{"at": _now(), "agent": "cleaning", "event": "waiting_user",
                          "summary": f"{len(session.questions)} question(s) sur des valeurs extrêmes : "
                                     "l'agent attend l'accord de l'utilisateur."}]}


def finish_cleaning(session: CleaningSession, state: dict, answers: dict[str, str] | None = None,
                    default: str = "keep") -> dict:
    answers = {q["id"]: (answers or {}).get(q["id"], default) for q in session.questions}
    s = session.finish(answers)
    clean_dir = Path(s["clean_data_dir"])
    tables = [{"name": n, "path": str(clean_dir / f"{n}.csv"), "source": t.get("source"),
               "rows_before": t.get("rows_before"), "rows_after": t.get("rows_after"),
               "cols_before": t.get("cols_before"), "cols_after": t.get("cols_after")}
              for n, t in s["tables"].items()]
    run_dir = Path(s["report"]).parent
    v = s["view"]["totals"]
    return {"cleaning": {
        "status": "done", "run_id": s["run_id"],
        "profiling_run_id": state.get("profiling", {}).get("metadata", {}).get("run_id"),
        "planner": s["planner"], "clean_data_dir": str(clean_dir), "tables": tables,
        "questions": session.questions, "answers": answers,
        "summary": {"steps_applied": s["steps_applied"], "steps_rejected": s["steps_rejected"],
                    "issues_flagged": s["issues_flagged"], "status": s["status"], **v},
        "steps": s["steps"],
        "artifacts": {"view_json": str(run_dir / "view.json"), "report_html": s["report"],
                      "trace_md": s["trace"], "trace_json": str(run_dir / "trace.json"),
                      "clean_data_dir": str(clean_dir)}},
        "timeline": [{"at": _now(), "agent": "cleaning", "event": "done",
                      "summary": f"{v['tables_to_process']}/{v['tables']} table(s) traitée(s), "
                                 f"{v['corrections']} correction(s), {v['rows_removed']} ligne(s) supprimée(s). "
                                 f"Tables propres transmises : {clean_dir}."}]}


def verify_cleaning(state: dict) -> dict:
    """Second profilage (règles seules) des tables nettoyées + bilan problème par problème."""
    cleaning = state["cleaning"]
    after = run_profiling({"source_dir": cleaning["clean_data_dir"]}, _work_dir(state) / "verification", use_llm=False)
    prof_after = after.get("profiling", {})
    before_sum = state.get("profiling", {}).get("summary", {})
    resolution = resolve_issues(state.get("profiling", {}), {"steps": cleaning.get("steps", [])}, prof_after)

    # Les colonnes ajoutées par le nettoyage (conditionnement décomposé) sont jugées à part :
    # le score « après » porte sur les mêmes colonnes que le score « avant ».
    added = {(st["table"], c) for st in cleaning.get("steps", [])
             if st["tool"] == "split_packaging" and st["status"] == "applied" for c in st["params"].get("columns", [])}
    same_scope = [i for i in prof_after.get("issues", []) if (i["table"], i["column"]) not in added]
    new_cols = [i for i in prof_after.get("issues", []) if (i["table"], i["column"]) in added]
    rows = {t["table_name"]: t["row_count"] for t in prof_after.get("tables", [])}
    scores = {t: _score([i for i in same_scope if i["table"] == t]) for t in rows}
    total = sum(rows.values())
    verification = {
        "score_before": before_sum.get("global_quality_score"),
        "score_after": round(sum(scores[t] * n for t, n in rows.items()) / total, 1) if total else 0.0,
        "issues_before": before_sum.get("issues_count"), "issues_after": len(same_scope),
        "by_table": {t: {"before": before_sum.get("quality_score_by_table", {}).get(t), "after": sc}
                     for t, sc in scores.items()},
        "remaining_issues": same_scope, "added_column_issues": new_cols,
        "score_method": "même calcul que l'Agent 1, sur les colonnes d'origine ; "
                        "les colonnes ajoutées par le nettoyage sont listées à part",
        "report_json": prof_after.get("artifacts", {}).get("json_report"),
    }
    return {"cleaning": {"issue_resolution": resolution, "verification": verification},
            "errors": after.get("errors", []),
            "timeline": [{"at": _now(), "agent": "verification", "event": "done",
                          "summary": f"Score qualité {verification['score_before']} -> {verification['score_after']} / 100 ; "
                                     f"{resolution['handled']}/{resolution['total']} problème(s) du profilage réglé(s)."}]}


def _score(issues: list[dict]) -> float:
    from src.agents.profiling.nodes import SEVERITY_PENALTY

    return float(max(0, 100 - sum(SEVERITY_PENALTY[i["severity"]] for i in issues if not i["is_expected"])))


def cleaning_node(state: dict) -> dict:
    """Nœud LangGraph sans interface : les valeurs extrêmes suivent input.outliers (défaut : garder)."""
    try:
        session = start_cleaning(state)
        return finish_cleaning(session, state, default=state.get("input", {}).get("outliers", "keep"))
    except Exception as exc:  # noqa: BLE001 - l'erreur est remontée dans le state
        return {"cleaning": {"status": "error"},
                "errors": [{"agent": "cleaning", "node": "cleaning", "table": None, "error": f"{type(exc).__name__}: {exc}"}]}


def verification_node(state: dict) -> dict:
    if state.get("cleaning", {}).get("status") != "done":
        return {}
    try:
        return verify_cleaning(state)
    except Exception as exc:  # noqa: BLE001
        return {"errors": [{"agent": "cleaning", "node": "verification", "table": None,
                            "error": f"{type(exc).__name__}: {exc}"}]}
