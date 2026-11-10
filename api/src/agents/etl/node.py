"""Nœud LangGraph de l'Agent 3 (ETL) : schéma en étoile à partir des tables nettoyées.

    verification --(state.cleaning.clean_data_dir)--> etl

Le code de l'agent (app/) n'est pas modifié : il est lancé tel quel dans un sous-processus
isolé (voir runner.py), comme les autres étapes du pipeline (src/orchestrator.py). Le LLM de
raisonnement (par défaut Groq, GROQ_API_KEY_AGENT3 ; Ollama Cloud avec ETL_LLM_PROVIDER=ollama) est optionnel : l'agent bascule sur une sélection
de dimensions déterministe quand il est absent, comme documenté dans son propre code
(app/graph/nodes/dimension_reasoning_node.py).

Limite connue : le schéma en étoile matérialisé par cet agent (ex. table de faits nommée
d'après la table source, clés étrangères brutes comme customerID/productID) n'a pas le même
nom ni les mêmes colonnes que la `fact_trade` attendue par l'Agent 4 (Semantic & KPI) —
voir src/agents/semantic_kpi/node.py. Les deux agents sont intégrés chacun dans son dossier
et transmettent leur état dans le state commun ; faire concorder les deux schémas demanderait
de changer le principe de l'un des deux codes, ce qui n'est pas fait ici.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from src.llm_keys import agent_key, llm_allowed, provider_of

ROOT = Path(__file__).resolve().parents[3]
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = "openai/gpt-oss-120b"
RUNNER = [sys.executable, "-m", "src.agents.etl.runner"]
DEFAULT_TIMEOUT = 20 * 60


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _work_dir(state: dict) -> Path:
    return Path(state.get("input", {}).get("work_dir") or ROOT / "outputs" / "etl")


def _input_dir(state: dict) -> str:
    """Tables les plus récentes disponibles : nettoyées (Agent 2) sinon source d'origine."""
    clean_dir = state.get("cleaning", {}).get("clean_data_dir")
    if clean_dir:
        return clean_dir
    return state.get("input", {}).get("source_dir") or str(ROOT / "data")


def run_etl(state: dict, timeout: int = DEFAULT_TIMEOUT) -> dict:
    input_dir = _input_dir(state)
    out_dir = _work_dir(state) / "etl"
    cmd = RUNNER + ["--input-dir", input_dir, "--output-dir", str(out_dir)]

    env = os.environ.copy()
    provider = provider_of(3)
    if provider == "groq":
        # Le code de l'Agent 3 parle à n'importe quel serveur compatible OpenAI (/chat/completions, clé dans OLLAMA_API_KEY) :
        # on le pointe sur Groq avec un modèle gpt-oss et la clé Groq de l'Agent 3. La clé Ollama éventuelle n'est jamais envoyée à Groq.
        cmd += ["--reasoning-model", os.environ.get("ETL_GROQ_MODEL") or os.environ.get("GROQ_MODEL") or GROQ_MODEL,
                "--ollama-base-url", GROQ_BASE_URL]
        env["OLLAMA_API_KEY"] = agent_key(3) if llm_allowed() else ""
    elif not llm_allowed():
        env["OLLAMA_API_KEY"] = ""
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)

    line = (proc.stdout or "").strip().splitlines()
    payload = json.loads(line[-1]) if line else {}

    if not payload:
        raise RuntimeError(f"Le sous-processus ETL n'a rien renvoyé (code {proc.returncode}). "
                           f"stderr : {proc.stderr[-2000:]}")
    if payload.get("error"):
        raise RuntimeError(payload["error"])
    if provider == "groq" and "OLLAMA_API_KEY is not set" in str(payload.get("fact_reasoning_error")):
        payload["fact_reasoning_error"] = ("Clé LLM de l'Agent 3 absente : renseignez GROQ_API_KEY_AGENT3 "
                                           "(ou GROQ_API_KEY) dans .env.")

    tables = payload.get("tables", {})
    validation = payload.get("validation") or {}
    ollama_used = not payload.get("fact_reasoning_error")

    return {
        "etl": {
            "status": "done" if payload.get("ok") else "error",
            "input_dir": input_dir,
            "confirmed_fact": payload.get("confirmed_fact"),
            "selected_dimensions": payload.get("selected_dimensions", []),
            "ollama_reasoning_used": ollama_used,
            "fact_reasoning_error": payload.get("fact_reasoning_error"),
            "fact_reasoning_attempts": payload.get("fact_reasoning_attempts"),
            "dimension_selection_error": payload.get("dimension_selection_error"),
            "plan": payload.get("plan"),
            "validation": validation,
            "tables": tables,
            "output_dir": str(out_dir),
        },
        "timeline": [{
            "at": _now(), "agent": "etl", "event": "done" if payload.get("ok") else "error",
            "summary": (
                f"Schéma en étoile matérialisé : {len(tables)} table(s) "
                f"({', '.join(sorted(tables)) or 'aucune'}) dans {out_dir}."
                + ("" if ollama_used else " Raisonnement LLM indisponible : "
                                           "sélection des dimensions faite par les règles de repli.")
            ) if payload.get("ok") else
                "Aucun schéma produit : "
                + (payload.get("fact_reasoning_error") or str(validation.get("errors") or payload))
                + f" (essais du raisonnement LLM : {payload.get('fact_reasoning_attempts')})",
        }],
    }


def etl_node(state: dict) -> dict:
    try:
        return run_etl(state)
    except Exception as exc:  # noqa: BLE001 - l'erreur est remontée dans le state
        return {"etl": {"status": "error"},
                "errors": [{"agent": "etl", "node": "etl", "table": None, "error": f"{type(exc).__name__}: {exc}"}]}
