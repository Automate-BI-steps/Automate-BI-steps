#!/usr/bin/env python3
"""
Clean generation build script.
Thin CLI wrapper around run_dashboard().
"""
from __future__ import annotations

import logging
import pathlib
import shutil
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.dashboard.config import GeneratorConfig, OperatingMode
from src.agents.dashboard.dashboard_generator import run_dashboard

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("bi_flow.generate_clean")


def main() -> None:
    input_dir = pathlib.Path("input")
    gen_dir = pathlib.Path("generated")

    if gen_dir.exists():
        shutil.rmtree(gen_dir)
        logger.info("[clean] removed previous generated/ directory")

    cfg = GeneratorConfig.from_env()
    if cfg.llm_provider != "mock" and cfg.mode == OperatingMode.DETERMINISTIC:
        cfg.mode = OperatingMode.AI_ASSISTED
    logger.info("[config] mode=%s, provider=%s, model=%s", cfg.mode.value, cfg.llm_provider, cfg.llm_model)

    result = run_dashboard(input_dir, gen_dir, cfg)

    for warning in result.get("warnings", []):
        logger.warning(warning)

    if result.get("errors"):
        for error in result["errors"]:
            logger.error(error)
        logger.error("BUILD FAILED")
        sys.exit(1)

    logger.info("BUILD OK - status: %s", result.get("status"))


if __name__ == "__main__":
    main()
