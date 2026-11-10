from __future__ import annotations

from pathlib import Path
import pandas as pd
import pytest

from src.agents.dashboard.config import GeneratorConfig, OperatingMode
from src.agents.dashboard.dashboard_generator import Bundle, Kpi, demo_bundle, validate_frame


def frame(rows: list[list[object]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["date", "country", "product", "direction", "value"])


def bundle_for(data: pd.DataFrame) -> Bundle:
    normalized, _, _ = validate_frame(data)
    return Bundle(
        data=normalized,
        data_model={"fact_table": "FactTrade", "grain": "transaction", "validated": True},
        kpis=[
            Kpi(id="total_imports", name="Total Imports", definition="Sum of imports", expression="SUM(value) where direction=Import", source_columns=["value", "direction"]),
            Kpi(id="total_exports", name="Total Exports", definition="Sum of exports", expression="SUM(value) where direction=Export", source_columns=["value", "direction"]),
            Kpi(id="trade_balance", name="Trade Balance", definition="Exports minus imports", expression="[Total Exports] - [Total Imports]", source_columns=["value", "direction"]),
            Kpi(id="transaction_count", name="Transaction Count", definition="Number of transactions", expression="COUNTROWS(FactTrade)"),
        ],
        findings={"findings": [{"id": "finding-1", "text": "Exports exceed imports."}], "recommendations": ["Expand exports."]},
        quality={"status": "passed", "checks": {"row_count": "passed", "column_types": "passed"}},
        lineage={"source_dataset": "test_dataset", "upstream_agent": "mock_upstream"},
    )


@pytest.fixture
def sample_bundle() -> Bundle:
    data = frame([
        ["2024-01-01", "DE", "Machinery", "Import", 1000.0],
        ["2024-01-02", "US", "Chemicals", "Export", 2500.0],
        ["2024-01-03", "FR", "Electronics", "Import", 800.0],
        ["2024-01-04", "JP", "Chemicals", "Export", 1200.0],
    ])
    return bundle_for(data)


@pytest.fixture
def deterministic_config() -> GeneratorConfig:
    return GeneratorConfig(mode=OperatingMode.DETERMINISTIC)


@pytest.fixture
def mock_ai_config() -> GeneratorConfig:
    return GeneratorConfig(mode=OperatingMode.AI_ASSISTED, llm_provider="mock")

