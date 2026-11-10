"""Étape « dashboard » du pipeline : prépare les entrées (prep.py) puis lance le nœud de Hashem tel quel.

    state.kpi --> build_input --> dashboard_node (src/agents/dashboard/node.py, non modifié) --> state.dashboard

Utilisée par src/graph.py (nœud LangGraph) et src/orchestrator.py (commande `dashboard`).
"""
from __future__ import annotations

import contextlib
import io
import json
import zipfile
from datetime import datetime
from pathlib import Path

from src.agents.dashboard.node import dashboard_node
from src.agents.dashboard.prep import DashboardInputError, build_input, published_kpi_values

ROOT = Path(__file__).resolve().parents[3]
POWERBI_HELPERS = ("relier_power_bi.py", "LIRE_MOI_POWER_BI.md")  # ajoutés au zip seulement : lien avec Power BI Desktop
OK_STATUSES = {"OK", "PARTIAL_SUCCESS"}  # PARTIAL_SUCCESS : tout est généré, Power BI Desktop n'a pas été lancé


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def run_dir_of(state: dict) -> Path:
    """Dossier du run : celui de l'orchestrateur, sinon le dossier de travail des agents (graphe lancé par main.py)."""
    base = (state.get("run") or {}).get("run_dir") or (state.get("input") or {}).get("work_dir")
    return Path(base) if base else ROOT / "outputs" / "run"


def run_dashboard_step(state: dict) -> dict:
    run_dir = run_dir_of(state)
    try:
        prep = build_input(state, run_dir / "dashboard_input")
        node_state = {"dashboard_prep": {"input_dir": prep["input_dir"], "config": prep["config"]}, "run": {**(state.get("run") or {}), "run_dir": str(run_dir)}}
        # le générateur imprime son rapport complet sur la sortie standard : la ligne JSON de
        # l'orchestrateur doit rester la dernière, et le détail est dans les fichiers produits
        with published_kpi_values(prep["kpis"], prep["results"]), contextlib.redirect_stdout(io.StringIO()):
            result = dashboard_node(node_state)["dashboard"]
    except DashboardInputError as exc:
        return _error(str(exc))
    except Exception as exc:  # noqa: BLE001 - l'erreur est remontée dans le state
        return _error(f"{type(exc).__name__}: {exc}")

    out_dir = run_dir / "dashboard"
    ok = result.get("status") in OK_STATUSES
    if ok and out_dir.is_dir():  # projet Power BI prêt à télécharger (hors du dossier zippé)
        result.setdefault("artifacts", {})["project_zip"] = str(_zip_project(out_dir, run_dir / "dashboard_project.zip"))
    section = {**result, "input_dir": prep["input_dir"], "input_mode": prep["input_mode"], "output_dir": str(out_dir),
               "pages": _pages(result),
               "prep_warnings": prep["warnings"],
               "kpis": [{"id": k["id"], "name": k["name"], "transmitted": k["supported"]} for k in prep["kpis"]],
               "kpis_skipped": prep["kpis_skipped"]}
    sent = sum(1 for k in prep["kpis"] if k["supported"])
    return {"dashboard": section, "timeline": [{
        "at": _now(), "agent": "dashboard", "event": "done" if ok else "error",
        "summary": (f"Tableau de bord généré (mode {prep['input_mode']}, {sent}/{len(prep['kpis'])} KPI transmis) : projet Power BI + "
                    f"zip dans {run_dir / 'dashboard_project.zip'}.") if ok else
                   f"Échec de la génération ({result.get('status')}) : {'; '.join(result.get('errors') or []) or 'raison inconnue'}."}],
        **({} if ok else {"errors": [{"agent": "dashboard", "node": "dashboard", "table": None,
                                       "error": "; ".join(result.get("errors") or []) or str(result.get("status"))}]})}


def _pages(result: dict) -> list[dict]:
    """Pages du rapport Power BI et leurs visuels (titre, type), lues dans dashboard_plan.json : affichées par l'appli."""
    try:
        plan = json.loads(Path((result.get("artifacts") or {}).get("plan_json") or "").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [{"name": p.get("name"), "title": p.get("display_name"), "description": p.get("description", ""),
             "visuals": [{"title": v.get("title"), "type": v.get("visual_type"), "kpi": v.get("kpi_reference")} for v in p.get("visuals", [])]}
            for p in plan.get("pages", [])]


def _zip_project(out_dir: Path, zip_path: Path) -> Path:
    """Zip du projet Power BI + aides (relier_power_bi.py, LIRE_MOI).

    Les aides ne sont écrites QUE dans le zip : un fichier .py déposé dans outputs/ faisait redémarrer
    `uvicorn --reload` (WatchFiles), ce qui tuait le pipeline en cours et inondait les journaux.
    """
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(out_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(out_dir).as_posix())
        for name in POWERBI_HELPERS:
            zf.write(Path(__file__).with_name(name), name)
    return zip_path


def _error(message: str) -> dict:
    return {"dashboard": {"status": "ERROR", "errors": [message], "warnings": [], "artifacts": {}},
            "timeline": [{"at": _now(), "agent": "dashboard", "event": "error", "summary": message}],
            "errors": [{"agent": "dashboard", "node": "dashboard", "table": None, "error": message}]}


def dashboard_graph_node(state: dict) -> dict:
    """Nœud LangGraph : même étape, sans état intermédiaire."""
    return run_dashboard_step(state)
