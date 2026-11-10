from __future__ import annotations

import json
from pathlib import Path
import pytest

from src.agents.dashboard.dashboard_compiler_adapter import compile_plan_to_report_pages
from src.agents.dashboard.dashboard_generator import generate, test_generation as run_generation_checks
from src.agents.dashboard.dashboard_planner import create_deterministic_plan
from tests.conftest import bundle_for, frame


def test_generate_in_tmp_path(sample_bundle, tmp_path: Path):
    result = generate(sample_bundle, tmp_path, demo=False)
    assert result["status"] == "PARTIAL_SUCCESS"
    assert result["project_format"] == "PBIP"
    assert len(result["report_pages"]) == 6

    # Verify PBIP entry points
    pbip_file = tmp_path / "dashboard_generator.pbip"
    assert pbip_file.exists()
    pbip_data = json.loads(pbip_file.read_text(encoding="utf-8"))
    assert pbip_data["artifacts"][0]["report"]["path"] == "dashboard_generator.Report"

    # Verify Report definition
    report_dir = tmp_path / "dashboard_generator.Report"
    pbir_file = report_dir / "definition.pbir"
    assert pbir_file.exists()
    pbir_data = json.loads(pbir_file.read_text(encoding="utf-8"))
    assert pbir_data["datasetReference"]["byPath"]["path"] == "../dashboard_generator.SemanticModel"

    # Verify Theme reference and presence
    report_json = json.loads((report_dir / "definition" / "report.json").read_text(encoding="utf-8"))
    base_theme = report_json["themeCollection"]["baseTheme"]["name"]
    theme_file = report_dir / "StaticResources" / "SharedResources" / "BaseThemes" / f"{base_theme}.json"
    assert theme_file.exists()

    # Verify Semantic Model
    model_dir = tmp_path / "dashboard_generator.SemanticModel"
    bim_file = model_dir / "model.bim"
    assert bim_file.exists()
    bim_data = json.loads(bim_file.read_text(encoding="utf-8"))
    tables = {t["name"]: t for t in bim_data["model"]["tables"]}
    assert {"FactTrade", "DimDate", "DimCountry", "DimProduct", "BusinessInsights", "DataQuality"}.issubset(tables)

    # Verify Measures
    measures = {m["name"] for m in tables["FactTrade"].get("measures", [])}
    assert {"Total Imports", "Total Exports", "Trade Balance", "Transaction Count"}.issubset(measures)

    # Verify Relationships
    rel_names = {r["name"] for r in bim_data["model"].get("relationships", [])}
    assert {"FactTradeDate", "FactTradeCountry", "FactTradeProduct"}.issubset(rel_names)

    # Verify SourceFolder parameter in partitions
    for t_name, table in tables.items():
        for partition in table.get("partitions", []):
            expr = "".join(partition["source"]["expression"])
            assert "File.Contents(\"" not in expr or "SourceFolder" in expr

    # Verify PBIR Visuals
    pages_dir = report_dir / "definition" / "pages"
    pages_meta = json.loads((pages_dir / "pages.json").read_text(encoding="utf-8"))
    assert len(pages_meta["pageOrder"]) == 6

    visual_files = list(pages_dir.glob("*/visuals/*/visual.json"))
    assert len(visual_files) > 0
    for vf in visual_files:
        v_data = json.loads(vf.read_text(encoding="utf-8"))
        assert "visual" in v_data
        assert "position" in v_data
        assert "visualType" in v_data["visual"]
        # Ensure coordinates are within canvas bounds (1280x720)
        pos = v_data["position"]
        assert pos["x"] + pos["width"] <= 1280
        assert pos["y"] + pos["height"] <= 720

    # Verify Automated Tests Check
    report = run_generation_checks(sample_bundle, result, tmp_path)
    assert report["passed"] is True
    assert report["checks"]["input_schema_valid"] is True
    assert report["checks"]["kpi_values_match_loaded_source_csv"] is True
    assert report["checks"]["required_pbip_artifacts"] is True
    assert report["checks"]["semantic_model_consistent"] is True
    assert report["checks"]["visual_bindings_present"] is True


def test_compiler_adapter_structure(sample_bundle):
    plan = create_deterministic_plan(sample_bundle)
    report_pages = compile_plan_to_report_pages(plan, sample_bundle)
    assert len(report_pages) == 6
    for page in report_pages:
        assert "name" in page
        assert "displayName" in page
        assert "visualContainers" in page
        for vc in page["visualContainers"]:
            assert "id" in vc
            assert "config" in vc
            assert "visualType" in vc["config"]

