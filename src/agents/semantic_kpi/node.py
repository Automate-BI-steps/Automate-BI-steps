"""Nœud LangGraph de l'Agent 4 (Semantic & KPI) : calcule les KPI à partir des tables.

    etl --(state.cleaning.clean_data_dir)--> semantic_kpi

Le code de l'agent (agent.py, data.py, kpis.py, mapping.py, analysis.py) n'est pas modifié :
il est lancé tel quel dans un sous-processus isolé (voir runner.py).

Note d'intégration (écart de contrat entre agent 3 et agent 4) : le code de l'Agent 4 prévoit,
dans son propre `charger_donnees`, de recevoir une `fact_trade` déjà construite par l'agent ETL
(commentaire « déjà fournie par l'agent ETL »). Mais le schéma matérialisé par l'Agent 3
(src/agents/etl/) est un schéma en étoile NORMALISÉ (une table de faits au grain order_details
+ des tables de dimension séparées : dim_product, dim_customer...), alors que l'Agent 4 attend
une table À PLAT avec les colonnes déjà dénormalisées (pays, nom de produit, montant de ligne...)
— exactement ce que fabrique sa propre fonction `load_fact_table` en joignant les 7 CSV bruts.
Faire concorder les deux demanderait d'écrire une jointure fact + dimensions -> table à plat,
ce qui changerait le principe de l'un des deux agents : non fait ici (RULE « sans changement de
principe de code »). En attendant un accord entre les deux agents sur un format commun, ce nœud
utilise donc le chemin « mode autonome » déjà prévu par l'Agent 4 lui-même (`run(data_path)`),
sur les tables les plus récentes du state partagé (nettoyées par l'Agent 2 si disponibles).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RUNNER = [sys.executable, "-m", "src.agents.semantic_kpi.runner"]
DEFAULT_TIMEOUT = 10 * 60


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _work_dir(state: dict) -> Path:
    return Path(state.get("input", {}).get("work_dir") or ROOT / "outputs" / "semantic_kpi")


def _data_path(state: dict) -> str:
    """Tables les plus récentes disponibles : nettoyées (Agent 2) sinon source d'origine."""
    clean_dir = state.get("cleaning", {}).get("clean_data_dir")
    if clean_dir:
        return clean_dir
    return state.get("input", {}).get("source_dir") or str(ROOT / "data")


def run_semantic_kpi(state: dict, timeout: int = DEFAULT_TIMEOUT) -> dict:
    data_path = _data_path(state)
    out_dir = _work_dir(state) / "semantic_kpi"
    cmd = RUNNER + ["--data-path", data_path, "--output-dir", str(out_dir)]

    env = os.environ.copy()
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)

    line = (proc.stdout or "").strip().splitlines()
    payload = json.loads(line[-1]) if line else {}

    if not payload:
        raise RuntimeError(f"Le sous-processus KPI n'a rien renvoyé (code {proc.returncode}). "
                           f"stderr : {proc.stderr[-2000:]}")
    if payload.get("error"):
        raise RuntimeError(payload["error"])

    kpi_results = payload.get("kpi_results") or {}
    errors = payload.get("errors") or []

    return {
        "kpi": {
            "status": "done" if payload.get("ok") else "error",
            "data_path": data_path,
            "semantic_mapping": payload.get("semantic_mapping"),
            "mapping_methode": payload.get("mapping_methode"),
            "kpi_results": kpi_results,
            "kpi_checks": payload.get("kpi_checks"),
            "kpi_analysis": payload.get("kpi_analysis"),
            "kpi_summary": payload.get("kpi_summary"),
            "outputs": payload.get("outputs"),
            "output_dir": str(out_dir),
            "errors": errors,
        },
        "timeline": [{
            "at": _now(), "agent": "semantic_kpi",
            "event": "done" if payload.get("ok") else "error",
            "summary": (
                f"{len(kpi_results)} KPI calculé(s) à partir de {data_path} ; fichiers dans {out_dir}."
            ) if payload.get("ok") else
                f"Aucun KPI calculé ({', '.join(errors) or 'raison inconnue'}).",
        }],
    }


def semantic_kpi_node(state: dict) -> dict:
    try:
        return run_semantic_kpi(state)
    except Exception as exc:  # noqa: BLE001 - l'erreur est remontée dans le state
        return {"kpi": {"status": "error"},
                "errors": [{"agent": "semantic_kpi", "node": "semantic_kpi", "table": None,
                            "error": f"{type(exc).__name__}: {exc}"}]}
