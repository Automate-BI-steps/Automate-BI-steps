"""Orchestrateur pas à pas du pipeline : un appel = un agent, le state est persisté entre chaque.

Utilisé par le backend FastAPI (api/app.py) ; utilisable aussi à la main :

    python -m src.orchestrator init    <run_dir> [--context "..."] [--planner rules|llm] [--model M] [--impute]
    python -m src.orchestrator profile <run_dir>
    python -m src.orchestrator clean   <run_dir>            # s'arrête si des valeurs extrêmes attendent l'utilisateur
    python -m src.orchestrator answer  <run_dir> --answers '{"q1": "cap"}'
    python -m src.orchestrator verify  <run_dir>
    python -m src.orchestrator all     <run_dir> [--outliers keep|cap|empty|advice]

Mémoire : <run_dir>/state.json contient le state LangGraph complet (src/state.py). Chaque étape
le relit, appelle les mêmes nœuds que src/graph.py, fusionne sa section avec les mêmes reducers et
le réécrit de façon atomique, avec une entrée `timeline` pour le passage de relais. Les fichiers
déposés sont dans <run_dir>/uploads ; les feuilles Excel y sont converties en CSV pour le profilage.
Chaque commande écrit sur la sortie standard une ligne JSON : {"ok": ..., "status": ..., ...}.
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.agents.cleaning.agent import table_name
from src.agents.cleaning.node import finish_cleaning, run_profiling, start_cleaning, verify_cleaning, waiting_update
from src.agents.etl.node import run_etl
from src.agents.semantic_kpi.node import run_semantic_kpi
from src.graph import profiling_handoff
from src.state import merge_section

ACCUMULATE = {"errors", "timeline"}  # reducers operator.add
SESSION_FILE = "cleaning_session.pkl"  # nettoyage en pause (attente de l'utilisateur)
EXCEL = {".xlsx", ".xlsm", ".xls"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- state persistant
def load_state(run_dir: Path) -> dict:
    return json.loads((run_dir / "state.json").read_text(encoding="utf-8"))


def save_state(run_dir: Path, state: dict) -> None:
    tmp = run_dir / "state.json.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, run_dir / "state.json")


def apply_update(state: dict, update: dict) -> dict:
    """Fusionne la sortie d'un nœud comme LangGraph : listes cumulées, sections fusionnées."""
    for key, value in update.items():
        if key in ACCUMULATE:
            state[key] = state.get(key, []) + list(value or [])
        elif isinstance(value, dict) and key != "input":
            state[key] = merge_section(state.get(key), value)
        else:
            state[key] = value
    return state


def set_run(state: dict, status: str, agent: str | None) -> None:
    state["run"] = {**state.get("run", {}), "status": status, "current_agent": agent, "updated_at": _now()}


# --------------------------------------------------------------------------- étapes
def cmd_init(run_dir: Path, args) -> dict:
    """Prépare les entrées : CSV copiés tels quels, chaque feuille Excel convertie en CSV UTF-8."""
    uploads, src = run_dir / "uploads", run_dir / "input"
    src.mkdir(parents=True, exist_ok=True)
    converted = []
    for f in sorted(uploads.iterdir()):
        if f.suffix.lower() in EXCEL:
            sheets = pd.read_excel(f, sheet_name=None, dtype=str, keep_default_na=False)
            for sheet, df in sheets.items():
                name = table_name(f.stem if len(sheets) == 1 else f"{f.stem}_{sheet}")
                df.to_csv(src / f"{name}.csv", index=False, encoding="utf-8")
                converted.append({"from": f"{f.name} / feuille {sheet}", "to": f"{name}.csv"})
        elif f.suffix.lower() == ".csv":
            shutil.copy2(f, src / f.name)
    state = {
        "run": {"run_id": run_dir.name, "created_at": _now(), "run_dir": str(run_dir)},
        "input": {"source_dir": str(src), "business_context": args.context or None, "work_dir": str(run_dir),
                  "planner": args.planner, "model": args.model or None, "impute": bool(args.impute),
                  "uploads": sorted(p.name for p in uploads.iterdir()), "converted_sheets": converted},
        "errors": [],
        "timeline": [{"at": _now(), "agent": "orchestrator", "event": "created",
                      "summary": f"{len(list(src.glob('*.csv')))} table(s) CSV prêtes pour le profilage"
                                 + (f" dont {len(converted)} feuille(s) Excel converties" if converted else "") + "."}],
    }
    set_run(state, "created", None)
    save_state(run_dir, state)
    return {"ok": True, "status": "created"}


def cmd_profile(run_dir: Path, args) -> dict:
    state = load_state(run_dir)
    set_run(state, "profiling", "profiling")
    save_state(run_dir, state)
    result = run_profiling(state["input"], run_dir)  # rapports dans <run_dir>/profiling
    apply_update(state, {"profiling": result.get("profiling", {}), "errors": result.get("errors", [])})
    apply_update(state, profiling_handoff(state))
    ok = bool(state["profiling"].get("summary", {}).get("ready_for_cleaning"))
    set_run(state, "profiled" if ok else "error", None)
    save_state(run_dir, state)
    return {"ok": ok, "status": state["run"]["status"]}


def cmd_clean(run_dir: Path, args) -> dict:
    state = load_state(run_dir)
    set_run(state, "cleaning", "cleaning")
    save_state(run_dir, state)
    # la clé saisie dans l'interface arrive par l'environnement : elle n'est jamais écrite dans le state
    session = start_cleaning(state, api_key=os.environ.get("GROQ_API_KEY") or None)
    if session.questions:
        session.agent.planner = None  # plus utile pour la suite, et ne pas écrire la clé sur disque
        with open(run_dir / SESSION_FILE, "wb") as f:
            pickle.dump(session, f)
        apply_update(state, waiting_update(session, state))
        set_run(state, "waiting_user", "cleaning")
        save_state(run_dir, state)
        return {"ok": True, "status": "waiting_user"}
    apply_update(state, finish_cleaning(session, state))
    set_run(state, "cleaned", None)
    save_state(run_dir, state)
    return {"ok": True, "status": "cleaned"}


def cmd_answer(run_dir: Path, args) -> dict:
    state = load_state(run_dir)
    path = run_dir / SESSION_FILE
    if not path.is_file():
        return {"ok": False, "status": state.get("run", {}).get("status"), "error": "Aucun nettoyage en attente"}
    with open(path, "rb") as f:
        session = pickle.load(f)  # fichier écrit par cmd_clean pour ce même run
    answers = json.loads(args.answers or "{}")
    set_run(state, "cleaning", "cleaning")
    apply_update(state, {"timeline": [{"at": _now(), "agent": "user", "event": "answered",
                                       "summary": f"Réponses : {answers}"}]})
    save_state(run_dir, state)
    apply_update(state, finish_cleaning(session, state, answers))
    path.unlink()
    set_run(state, "cleaned", None)
    save_state(run_dir, state)
    return {"ok": True, "status": "cleaned"}


def cmd_verify(run_dir: Path, args) -> dict:
    state = load_state(run_dir)
    set_run(state, "verifying", "verification")
    save_state(run_dir, state)
    apply_update(state, verify_cleaning(state))
    set_run(state, "verified", None)
    save_state(run_dir, state)
    return {"ok": True, "status": "verified"}


def cmd_etl(run_dir: Path, args) -> dict:
    """Agent 3 : schéma en étoile à partir des tables nettoyées (code non modifié, voir src/agents/etl/)."""
    state = load_state(run_dir)
    set_run(state, "etl", "etl")
    save_state(run_dir, state)
    apply_update(state, run_etl(state))
    ok = state.get("etl", {}).get("status") == "done"
    set_run(state, "etl_done" if ok else "error", None if ok else "etl")
    save_state(run_dir, state)
    return {"ok": ok, "status": state["run"]["status"]}


def cmd_kpi(run_dir: Path, args) -> dict:
    """Agent 4 : calcul des KPI (code non modifié, voir src/agents/semantic_kpi/)."""
    state = load_state(run_dir)
    set_run(state, "semantic_kpi", "semantic_kpi")
    save_state(run_dir, state)
    apply_update(state, run_semantic_kpi(state))
    ok = state.get("kpi", {}).get("status") == "done"
    set_run(state, "done" if ok else "error", None if ok else "semantic_kpi")
    save_state(run_dir, state)
    return {"ok": ok, "status": state["run"]["status"]}


def cmd_all(run_dir: Path, args) -> dict:
    if not (run_dir / "state.json").is_file():
        cmd_init(run_dir, args)
    for step in (cmd_profile, cmd_clean):
        res = step(run_dir, args)
        if not res["ok"]:
            return res
    if res["status"] == "waiting_user":
        args.answers = json.dumps({q["id"]: args.outliers for q in load_state(run_dir)["cleaning"]["questions"]})
        cmd_answer(run_dir, args)
    res = cmd_verify(run_dir, args)
    if not res["ok"]:
        return res
    # etl et kpi ne dépendent pas l'un de l'autre (tous les deux lisent les tables nettoyées,
    # voir src/graph.py) : un échec de l'un ne doit pas empêcher l'autre de tourner.
    cmd_etl(run_dir, args)
    return cmd_kpi(run_dir, args)


COMMANDS = {"init": cmd_init, "profile": cmd_profile, "clean": cmd_clean, "answer": cmd_answer,
            "verify": cmd_verify, "etl": cmd_etl, "kpi": cmd_kpi, "all": cmd_all}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Orchestrateur pas à pas (profilage -> nettoyage -> contrôle)")
    p.add_argument("command", choices=COMMANDS)
    p.add_argument("run_dir", type=Path)
    p.add_argument("--context", default="")
    p.add_argument("--planner", choices=["rules", "llm"], default="rules")
    p.add_argument("--model", default="")
    p.add_argument("--impute", action="store_true")
    p.add_argument("--answers", default="")
    p.add_argument("--outliers", choices=["keep", "cap", "empty", "advice"], default="keep")
    args = p.parse_args()
    run_dir = args.run_dir.resolve()
    try:
        result = COMMANDS[args.command](run_dir, args)
    except Exception as exc:  # noqa: BLE001 - l'erreur est écrite dans le state pour l'interface
        traceback.print_exc()
        result = {"ok": False, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
        if (run_dir / "state.json").is_file():
            state = load_state(run_dir)
            agent = state.get("run", {}).get("current_agent")
            apply_update(state, {"errors": [{"agent": agent, "node": args.command, "table": None,
                                             "error": result["error"]}]})
            set_run(state, "error", agent)
            save_state(run_dir, state)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
