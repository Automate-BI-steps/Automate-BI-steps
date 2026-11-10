"""LangGraph node for Dashboard Generator (Agent 5)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("bi_flow.node")


def dashboard_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph node execution function.

    Reads:
      - state["dashboard_prep"]["input_dir"]
      - state["run"]["run_dir"]
      - state["dashboard_prep"]["config"] (optional)

    Returns:
      {"dashboard": result_dict}
      Catches all exceptions and returns them in result_dict["errors"].
    """
    try:
        try:
            from .dashboard_generator import run_dashboard
            from .config import GeneratorConfig
        except (ImportError, ValueError):
            from src.agents.dashboard.dashboard_generator import run_dashboard
            from src.agents.dashboard.config import GeneratorConfig

        prep = state.get("dashboard_prep") or {}
        run_info = state.get("run") or {}

        input_dir = Path(prep.get("input_dir", "input"))
        run_dir = Path(run_info.get("run_dir", "generated"))
        output_dir = run_dir / "dashboard"

        cfg_dict = prep.get("config") or {}
        if cfg_dict and isinstance(cfg_dict, dict):
            try:
                cfg = GeneratorConfig(**cfg_dict)
            except Exception as e:
                logger.warning("Could not instantiate GeneratorConfig with %s: %s; using default", cfg_dict, e)
                cfg = GeneratorConfig()
        elif isinstance(cfg_dict, GeneratorConfig):
            cfg = cfg_dict
        else:
            cfg = GeneratorConfig.from_env()

        result = run_dashboard(input_dir, output_dir, cfg)
        return {"dashboard": result}

    except Exception as exc:
        logger.exception("dashboard_node failed: %s", exc)
        return {
            "dashboard": {
                "status": "ERROR",
                "mode": "unknown",
                "selected_plan": None,
                "score": None,
                "candidates": [],
                "artifacts": {
                    "pbip_dir": None,
                    "plan_json": None,
                    "critic_report": None,
                },
                "warnings": [],
                "errors": [str(exc)],
            }
        }

