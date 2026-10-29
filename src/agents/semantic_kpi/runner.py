"""Lance le code de l'Agent 4 (Semantic & KPI) tel quel, dans un processus isolé.

Le code de l'agent (agent.py, data.py, kpis.py, mapping.py, analysis.py) utilise des
imports simples (`from data import ...`, etc.), prévus pour être exécutés depuis son
propre dossier (voir son pytest.ini : pythonpath = .). On isole donc son exécution dans
un sous-processus dédié, plutôt que de modifier ses imports ou d'ajouter ce dossier au
sys.path du processus principal : ça évite tout conflit avec les autres agents du projet,
qui ont eux aussi des modules nommés `agent.py` (voir src/agents/cleaning/agent.py).

Usage :
    python -m src.agents.semantic_kpi.runner --data-path <dossier ou fichier CSV> --output-dir <dossier de sortie>
        [--use-llm]

Écrit une ligne JSON sur stdout : {"ok": bool, "semantic_mapping": ..., "kpi_results": ...,
"kpi_checks": ..., "kpi_analysis": ..., "outputs": ..., "errors": [...]}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-path", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--use-llm", action="store_true")
    args = parser.parse_args()

    os.environ["OUTPUT_DIR"] = args.output_dir
    if args.use_llm:
        os.environ["USE_LLM"] = "1"
    else:
        os.environ.pop("USE_LLM", None)

    try:
        from agent import en_json, run

        etat = run(args.data_path)
        out = en_json({
            "ok": not etat.get("errors"),
            "semantic_mapping": etat.get("semantic_mapping"),
            "mapping_methode": etat.get("mapping_methode"),
            "kpi_results": etat.get("kpi_results"),
            "kpi_checks": etat.get("kpi_checks"),
            "kpi_analysis": etat.get("kpi_analysis"),
            "kpi_summary": etat.get("kpi_summary"),
            "audit_log": etat.get("audit_log"),
            "outputs": etat.get("outputs"),
            "errors": etat.get("errors"),
        })
        print(json.dumps(out, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:  # noqa: BLE001 - l'erreur est remontée à l'appelant via stdout
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}))
        return 0


if __name__ == "__main__":
    sys.exit(main())
