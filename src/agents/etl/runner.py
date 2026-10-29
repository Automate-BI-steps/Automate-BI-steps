"""Lance le code de l'Agent 3 (ETL) tel quel, dans un processus isolé.

Le code de l'agent (app/) utilise des imports absolus `from app...` : on ajoute donc
ce dossier à sys.path pour que `app` se résolve vers notre copie, sans toucher à son
code. L'isolation en sous-processus (comme src/orchestrator.py pour les autres agents)
évite toute interférence avec le reste du projet.

Usage :
    python -m src.agents.etl.runner --input-dir <dossier CSV> --output-dir <dossier de sortie>
        [--primary-keys-json '{"orders": ["orderID"], ...}']
        [--reasoning-model gemma4:31b-cloud] [--ollama-base-url https://ollama.com/v1]
        [--reasoning-timeout 120]

Écrit une ligne JSON sur stdout : {"ok": bool, "plan": ..., "validation": ...,
"tables": {nom: {"path", "rows", "columns"}}, "fact_reasoning_error": ..., "errors": [...]}
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def invoke_with_retry(invoke, initial_state: dict, max_attempts: int = 3) -> tuple[dict, list[str]]:
    """Relance le workflow quand le raisonnement LLM de la table de faits échoue.

    Le code de l'agent valide strictement la réponse du LLM (JSON valide, score recalculé à
    ±0.001) et ne réessaie jamais : une erreur d'arithmétique du modèle arrête tout le schéma.
    Ce nouvel essai vit ici, dans le wrapper, pour ne pas modifier l'agent. Pas de nouvel essai
    quand la clé est absente : ça ne peut pas changer.
    """
    errors: list[str] = []
    result: dict = {}
    for _ in range(max(1, max_attempts)):
        result = invoke(initial_state)
        error = result.get("fact_reasoning_error")
        if not error:
            break
        errors.append(error)
        if "OLLAMA_API_KEY is not set" in error:
            break
    return result, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--primary-keys-json", default=None)
    parser.add_argument("--reasoning-model", default="gemma4:31b-cloud")
    parser.add_argument("--ollama-base-url", default="https://ollama.com/v1")
    parser.add_argument("--reasoning-timeout", type=int, default=120)
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args()

    try:
        from app.graph.workflow import DEFAULT_PRIMARY_KEYS, build_workflow
        from app.reasoning.star_schema_materializer import StarSchemaMaterializer

        primary_keys = (
            json.loads(args.primary_keys_json) if args.primary_keys_json else DEFAULT_PRIMARY_KEYS
        )
        initial_state = {
            "input_dir": args.input_dir,
            "primary_keys": primary_keys,
            "reasoning_model": args.reasoning_model,
            "ollama_base_url": args.ollama_base_url,
            "reasoning_timeout": args.reasoning_timeout,
        }

        workflow = build_workflow()
        result, attempt_errors = invoke_with_retry(workflow.invoke, initial_state, args.max_attempts)

        plan = result.get("star_schema_plan")
        plan_dict = plan if isinstance(plan, dict) else (plan.to_dict() if hasattr(plan, "to_dict") else None)

        out = {
            "ok": False,
            "confirmed_fact": result.get("confirmed_fact"),
            "selected_dimensions": result.get("selected_dimensions", []),
            "fact_reasoning_error": result.get("fact_reasoning_error"),
            "fact_reasoning_attempts": len(attempt_errors) + (0 if result.get("fact_reasoning_error") else 1),
            "dimension_selection_error": result.get("dimension_selection_error"),
            "dimension_selection_fallback_used": result.get("dimension_selection_fallback_used"),
            "star_schema_build_error": result.get("star_schema_build_error"),
            "plan": plan_dict,
            "tables": {},
            "validation": None,
            "errors": [],
        }

        tables_data = result.get("tables_data")
        if not isinstance(tables_data, dict) or not plan_dict:
            out["errors"].append(
                "Pas de tables_data ou de plan de schéma en étoile à matérialiser "
                f"(build error: {result.get('star_schema_build_error')})."
            )
            print(json.dumps(out, ensure_ascii=False, default=str))
            return 0

        materialized = StarSchemaMaterializer().materialize(tables_data=tables_data, plan=plan_dict)
        validation = materialized.get("validation", {})
        out["validation"] = validation

        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, df in materialized.get("tables", {}).items():
            path = out_dir / f"{name}.csv"
            df.to_csv(path, index=False, encoding="utf-8")
            out["tables"][name] = {"path": str(path), "rows": len(df), "columns": list(df.columns)}

        out["ok"] = bool(validation.get("valid")) and bool(out["tables"])
        print(json.dumps(out, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:  # noqa: BLE001 - l'erreur est remontée à l'appelant via stdout
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}))
        return 0


if __name__ == "__main__":
    sys.exit(main())
