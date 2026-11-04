from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


SUPPORTED_VISUAL_TYPES = {"card", "lineChart", "clusteredBarChart", "tableEx", "slicer"}
MAX_PAGES = 6
MAX_VISUALS_PER_PAGE = 6


class VisualRecommendation(BaseModel):
    visual_id: str
    visual_type: str
    title: str
    target_page: str
    kpi_reference: str | None = None
    insight_reference: str | None = None
    roles: dict[str, list[dict[str, str]]] = Field(default_factory=dict)
    selection_reason: str
    priority: str = "medium"  # "high", "medium", "low"
    supported_fields: list[str] = Field(default_factory=list)


class VisualizationAgent:
    """Intelligent visual selection and semantic constraint validator."""

    def __init__(self, supported_types: set[str] | None = None) -> None:
        self.supported_types = supported_types or SUPPORTED_VISUAL_TYPES

    def recommend_for_kpi(
        self,
        kpi_id: str,
        kpi_name: str,
        definition: str,
        available_dimensions: list[str],
        page_name: str = "ExecutiveOverview",
    ) -> VisualRecommendation:
        """Recommend a visual for a specific KPI based on its semantics."""
        return VisualRecommendation(
            visual_id=f"{kpi_id}_card",
            visual_type="card",
            title=kpi_name,
            target_page=page_name,
            kpi_reference=kpi_id,
            roles={"Values": [{"kind": "Measure", "table": "FactTrade", "name": kpi_name}]},
            selection_reason=f"High-priority KPI '{kpi_name}' best presented as an executive summary card.",
            priority="high",
            supported_fields=["FactTrade." + kpi_name],
        )

    def recommend_trend(
        self,
        kpi_names: list[str],
        date_dimension: str = "Date",
        page_name: str = "TradePerformance",
    ) -> VisualRecommendation:
        """Recommend a line chart for tracking trade metrics across time."""
        projections = [{"kind": "Measure", "table": "FactTrade", "name": name} for name in kpi_names]
        return VisualRecommendation(
            visual_id="trade_trend",
            visual_type="lineChart",
            title="Import and Export Trends",
            target_page=page_name,
            roles={
                "Category": [{"kind": "Column", "table": "DimDate", "name": date_dimension}],
                "Y": projections,
            },
            selection_reason="Time series metrics should be visualized as line charts with the date dimension on the Category axis.",
            priority="high",
            supported_fields=["DimDate." + date_dimension] + [f"FactTrade.{n}" for n in kpi_names],
        )

    def recommend_breakdown(
        self,
        dimension_table: str,
        dimension_column: str,
        value_metric: str = "Value",
        series_column: str | None = "Direction",
        title: str = "Trade Breakdown",
        page_name: str = "CountryAnalysis",
    ) -> VisualRecommendation:
        """Recommend a clustered bar chart for comparing metrics across categories."""
        roles: dict[str, list[dict[str, str]]] = {
            "Category": [{"kind": "Column", "table": dimension_table, "name": dimension_column}],
            "Y": [{"kind": "Sum", "table": "FactTrade", "name": value_metric}],
        }
        supported = [f"{dimension_table}.{dimension_column}", f"FactTrade.{value_metric}"]
        if series_column:
            roles["Series"] = [{"kind": "Column", "table": "FactTrade", "name": series_column}]
            supported.append(f"FactTrade.{series_column}")

        return VisualRecommendation(
            visual_id=f"{dimension_column.lower()}_breakdown",
            visual_type="clusteredBarChart",
            title=title,
            target_page=page_name,
            roles=roles,
            selection_reason=f"Categorical comparison of trade values across {dimension_column} breakdown.",
            priority="medium",
            supported_fields=supported,
        )

    def validate_visual_intent(
        self,
        visual_type: str,
        roles: dict[str, list[dict[str, str]]],
        available_tables: dict[str, list[str]],
        available_measures: list[str],
    ) -> tuple[bool, list[str]]:
        """Verify that a proposed visual uses valid types and fields that exist in the semantic model."""
        issues: list[str] = []
        if visual_type not in self.supported_types:
            issues.append(f"Unsupported visual type: '{visual_type}'. Must be one of {sorted(self.supported_types)}")

        for role_name, specs in roles.items():
            for spec in specs:
                kind = spec.get("kind")
                table = spec.get("table")
                name = spec.get("name")
                if not table or not name:
                    issues.append(f"Invalid field spec in role '{role_name}': {spec}")
                    continue
                if kind == "Measure":
                    if name not in available_measures:
                        issues.append(f"Referenced measure '{name}' not found in available measures: {available_measures}")
                elif kind in ("Column", "Sum"):
                    columns = available_tables.get(table, [])
                    if name not in columns:
                        issues.append(f"Referenced column '{table}.{name}' not found in model table '{table}' columns: {columns}")
                else:
                    issues.append(f"Unknown field projection kind '{kind}' for {table}.{name}")

        return len(issues) == 0, issues

