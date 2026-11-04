"""Tests for generic (non-trade) dataset support and programmatic entry points."""
from __future__ import annotations

import json
from pathlib import Path
import pandas as pd
import pytest

from src.agents.dashboard.dashboard_generator import (
    load_bundle,
    run_dashboard,
    classify_data_model,
    is_trade_preset,
    validate_frame,
)
from src.agents.dashboard.config import GeneratorConfig, OperatingMode
from src.agents.dashboard.node import dashboard_node


# â”€â”€â”€ Fixtures â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.fixture
def sales_input_dir(tmp_path: Path) -> Path:
    """Create a minimal sales dataset input directory without optional files."""
    input_dir = tmp_path / "sales_input"
    input_dir.mkdir(parents=True, exist_ok=True)

    df = pd.DataFrame([
        {"date": "2024-01-01", "store": "StoreA", "category": "Electronics", "revenue": 5000.0, "quantity": 10},
        {"date": "2024-01-02", "store": "StoreB", "category": "Clothing", "revenue": 3000.0, "quantity": 20},
        {"date": "2024-01-03", "store": "StoreA", "category": "Home", "revenue": 1500.0, "quantity": 5},
    ])
    df.to_csv(input_dir / "sales_data.csv", index=False)

    data_model = {
        "fact_table": "FactSales",
        "grain": "One row per sale",
        "columns": {
            "date": "Sale date",
            "store": "Store identifier",
            "category": "Product category",
            "revenue": "Revenue in USD",
            "quantity": "Units sold",
        },
        "validated": True,
    }
    (input_dir / "data_model.json").write_text(json.dumps(data_model), encoding="utf-8")

    kpi_catalog = {
        "kpis": [
            {
                "id": "total_revenue",
                "name": "Total Revenue",
                "definition": "Sum of sales revenue",
                "expression": "SUM(revenue)",
                "source_columns": ["revenue"],
                "supported": True,
            },
            {
                "id": "avg_quantity",
                "name": "Average Quantity",
                "definition": "Average units sold per transaction",
                "expression": "AVG(quantity)",
                "source_columns": ["quantity"],
                "supported": True,
            },
            {
                "id": "unsupported_metric",
                "name": "Unsupported Metric",
                "definition": "Arbitrary non-translatable formula",
                "expression": "COMPLEX_NON_DAX_FUNCTION(revenue, quantity)",
                "source_columns": ["revenue", "quantity"],
                "supported": False,
            },
        ]
    }
    (input_dir / "kpi_catalog.json").write_text(json.dumps(kpi_catalog), encoding="utf-8")

    # bi_analyst_findings.json, data_quality_report.json, and xai_lineage.json are intentionally omitted
    return input_dir


# â”€â”€â”€ Tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_classify_data_model():
    data_model = {
        "columns": {
            "order_date": "Date of the order",
            "store_id": "Identifier for store",
            "product_category": "Category",
            "revenue": "Revenue amount in USD",
            "quantity": "Items count",
        }
    }
    classified = classify_data_model(data_model)
    assert classified["order_date"] == "date"
    assert classified["revenue"] == "measure"
    assert classified["quantity"] == "measure"
    assert classified["store_id"] == "dimension"
    assert classified["product_category"] == "dimension"


def test_is_trade_preset():
    trade_model = {
        "columns": {
            "date": "Date",
            "country": "Country",
            "product": "Product",
            "direction": "Direction",
            "value": "Value",
        }
    }
    assert is_trade_preset(trade_model, ["date", "country", "product", "direction", "value"]) is True

    sales_model = {
        "columns": {
            "date": "Sale date",
            "store": "Store",
            "category": "Category",
            "revenue": "Revenue",
            "quantity": "Quantity",
        }
    }
    assert is_trade_preset(sales_model, ["date", "store", "category", "revenue", "quantity"]) is False


def test_validate_frame_non_trade_mode():
    df = pd.DataFrame([
        {"date": "2024-01-01", "store": "StoreA", "revenue": 500.0, "quantity": 10},
        {"date": "2024-01-02", "store": "StoreB", "revenue": 800.0, "quantity": 15},
    ])
    result, mapping, issues = validate_frame(df, trade_mode=False)
    assert not result.empty
    assert "store" in result.columns
    assert "revenue" in result.columns
    assert not any(i.severity == "error" for i in issues)


def test_load_bundle_generic_sales_dataset(sales_input_dir: Path):
    bundle, issues = load_bundle(sales_input_dir, demo=False)
    assert bundle is not None
    assert bundle.trade_mode is False
    assert len(bundle.data) == 3
    assert len(bundle.kpis) == 3

    # Verify fallbacks for optional files
    assert bundle.findings.get("reporting_period") == "unavailable"
    assert bundle.findings.get("findings") == []
    assert bundle.quality.get("status") == "computed_fallback"
    assert bundle.quality.get("row_count") == 3
    assert bundle.lineage is None

    # Verify warnings recorded for missing files
    warning_codes = [i.code for i in issues if i.severity == "warning"]
    assert "MISSING_FINDINGS" in warning_codes
    assert "MISSING_QUALITY_REPORT" in warning_codes
    assert "MISSING_LINEAGE" in warning_codes


def test_run_dashboard_sales_dataset(sales_input_dir: Path, tmp_path: Path):
    output_dir = tmp_path / "sales_generated"
    result = run_dashboard(sales_input_dir, output_dir)

    assert result["status"] in ("PARTIAL_SUCCESS", "OK")
    assert result["mode"] == "deterministic"
    assert result["selected_plan"] == "deterministic"
    assert result["errors"] == []

    # Artifacts exist and are paths as strings
    artifacts = result["artifacts"]
    assert isinstance(artifacts["pbip_dir"], str)
    assert Path(artifacts["pbip_dir"]).exists()
    assert isinstance(artifacts["html_path"], str)
    assert Path(artifacts["html_path"]).exists()
    assert isinstance(artifacts["plan_json"], str)
    assert Path(artifacts["plan_json"]).exists()
    assert isinstance(artifacts["critic_report"], str)
    assert Path(artifacts["critic_report"]).exists()

    # Unsupported KPI skipped with warning
    warnings = result["warnings"]
    assert any("unsupported_metric" in w for w in warnings)
    assert any("MISSING_FINDINGS" in w for w in warnings)

    # Result is completely JSON-serializable
    dumped = json.dumps(result)
    loaded = json.loads(dumped)
    assert loaded["status"] == result["status"]
    assert loaded["artifacts"]["pbip_dir"] == artifacts["pbip_dir"]


def test_dashboard_node_langgraph_integration(sales_input_dir: Path, tmp_path: Path):
    run_dir = tmp_path / "run_out"
    state = {
        "dashboard_prep": {
            "input_dir": str(sales_input_dir),
            "config": {"mode": "deterministic"},
        },
        "run": {
            "run_dir": str(run_dir),
        },
    }

    result_state = dashboard_node(state)
    assert "dashboard" in result_state
    dashboard_res = result_state["dashboard"]
    assert dashboard_res["status"] in ("PARTIAL_SUCCESS", "OK")
    assert dashboard_res["errors"] == []
    assert json.dumps(dashboard_res) is not None


def test_dashboard_node_catches_exceptions():
    state = {
        "dashboard_prep": {
            "input_dir": "/nonexistent/invalid/dir",
        },
        "run": {
            "run_dir": "any_dir",
        },
    }
    result_state = dashboard_node(state)
    assert "dashboard" in result_state
    dashboard_res = result_state["dashboard"]
    assert dashboard_res["status"] == "ERROR"
    assert len(dashboard_res["errors"]) > 0


