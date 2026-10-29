"""Point d'entrée : lance le pipeline BI (Agent 1 profilage -> Agent 2 nettoyage -> contrôle).

    python main.py
    python main.py --data "Datasets/Import&Export/data" --context "Ventes import/export"
    python main.py --planner llm --outliers advice

Interface web (visualisation + validation des valeurs extrêmes) : uvicorn api.app:app (voir README.md).
"""

import argparse
import sys

from src.config import DEFAULT_DATA_DIR
from src.graph import build_pipeline


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Pipeline BI — profilage puis nettoyage")
    parser.add_argument("--data", default=str(DEFAULT_DATA_DIR), help="Dossier des CSV")
    parser.add_argument("--context", default=None, help="Contexte métier pour le LLM")
    parser.add_argument("--planner", choices=["rules", "llm"], default="rules",
                        help="Nettoyage : règles seules ou choix des outils par le LLM")
    parser.add_argument("--outliers", choices=["keep", "cap", "empty", "advice"], default="keep",
                        help="Valeurs extrêmes : garder, ramener à la limite, vider ou suivre le conseil")
    args = parser.parse_args()

    state = build_pipeline().invoke(
        {"input": {"source_dir": args.data, "business_context": args.context,
                   "planner": args.planner, "outliers": args.outliers}}
    )

    prof = state["profiling"]
    summary = prof["summary"]
    print(f"\nTables : {summary['tables_count']} | Lignes : {summary['total_rows']} "
          f"| Problèmes : {summary['issues_count']} {summary['issues_by_severity']}")
    print(f"Score global de qualité : {summary['global_quality_score']}/100")
    print(f"LLM : {prof['metadata']['llm']['status']}")
    for err in state.get("errors", []):
        print(f"  ! [{err['node']}] {err['table'] or ''} {err['error']}")
    print(f"\nRapport JSON     : {prof['artifacts']['json_report']}")
    print(f"Rapport Markdown : {prof['artifacts']['markdown_report']}")

    cleaning = state.get("cleaning", {})
    if cleaning.get("status") == "done":
        ver, res = cleaning.get("verification", {}), cleaning.get("issue_resolution", {})
        print(f"\nNettoyage : {cleaning['summary']['corrections']} correction(s), "
              f"{cleaning['summary']['rows_removed']} ligne(s) supprimée(s)")
        print(f"Score qualité : {ver.get('score_before')} -> {ver.get('score_after')} / 100 "
              f"| problèmes réglés : {res.get('handled')}/{res.get('total')}")
        print(f"Tables nettoyées : {cleaning['clean_data_dir']}")
        print(f"Comparaison avant / après : {cleaning['artifacts']['report_html']}")
    print("\nMémoire du run :")
    for step in state.get("timeline", []):
        print(f"  [{step['agent']}] {step['summary']}")


if __name__ == "__main__":
    main()
