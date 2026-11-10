from __future__ import annotations

import json
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field, ValidationError

from src.agents.dashboard.config import GeneratorConfig, OperatingMode
from src.agents.dashboard.knowledge_agent import RetrievedChunk

logger = logging.getLogger("bi_flow.dashboard_planner")


class EvidenceCategory(str, Enum):
    UPSTREAM_FACT = "upstream_fact"
    DOMAIN_GUIDANCE = "domain_guidance"
    AI_INTERPRETATION = "ai_interpretation"
    RECOMMENDATION = "recommendation"
    UNKNOWN = "unknown"


class VisualEvidence(BaseModel):
    business_question: str = Field(default="", description="Business question this visual directly answers")
    kpi_or_insight: str = Field(default="", description="KPI or analyst insight bound to this visual")
    visual_suitability_reason: str = Field(default="", description="Why this chart type is appropriate")
    upstream_source: str = Field(default="", description="Underlying upstream table or document")
    domain_rule: str = Field(default="", description="Relevant domain guideline or interpretation rule")
    evidence_category: EvidenceCategory = Field(default=EvidenceCategory.UPSTREAM_FACT, description="Fact vs interpretation category")
    uncertainty_or_limitation: str = Field(default="", description="Known limitations or caveats")


class VisualPlan(BaseModel):
    id: str = Field(description="Unique identifier for the visual container")
    title: str = Field(description="Human-readable title displayed on the visual container")
    visual_type: str = Field(description="Supported visual type: 'card', 'lineChart', 'clusteredBarChart', 'tableEx', 'slicer'")
    kpi_reference: str | None = Field(default=None, description="Referenced KPI identifier from kpi_catalog")
    insight_reference: str | None = Field(default=None, description="Referenced insight identifier from findings")
    roles: dict[str, list[dict[str, str]]] = Field(
        default_factory=dict,
        description="PBIR projection role mapping (e.g. Values, Category, Y, Series)"
    )
    selection_reason: str = Field(default="", description="Business justification for visual choice")
    priority: str = Field(default="medium", description="Visual priority: 'high', 'medium', 'low'")
    evidence_references: list[str] = Field(default_factory=list, description="IDs of supporting evidence/documents")
    evidence: VisualEvidence | None = Field(default=None, description="Structured evidence grounding and domain explanation")
    # Optionnels (rapport générique) : tri par défaut et réglages d'affichage traduits en PBIR par visual().
    sort: list[dict[str, str]] | None = Field(default=None, description="Default sort: [{kind, table, name, direction}]")
    display: dict[str, Any] = Field(default_factory=dict, description="Display options: data_labels, slicer_mode, show_title")


class PagePlan(BaseModel):
    name: str = Field(description="Alphanumeric identifier for the page folder")
    display_name: str = Field(description="User-facing title for the page tab")
    description: str = Field(default="", description="Business purpose of this page")
    visuals: list[VisualPlan] = Field(default_factory=list, description="Visuals placed on this page")


class DashboardPlan(BaseModel):
    dashboard_title: str = Field(default="Trade Performance Intelligence Dashboard")
    business_objective: str = Field(default="Monitor headline trade performance, bilateral balances, and data quality.")
    target_audience: str = Field(default="Executive Leadership and Trade Analysts")
    pages: list[PagePlan] = Field(default_factory=list)
    kpi_references: list[str] = Field(default_factory=list)
    insight_references: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    planning_mode: str = Field(default="deterministic")
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMProvider(ABC):
    """Abstract interface for LLM calls with timeout and error handling."""

    @abstractmethod
    def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        """Execute completion and return raw string response."""
        pass


class MockLLMProvider(LLMProvider):
    """Deterministic mock provider for offline tests and predictable execution."""

    def __init__(self, response_override: str | None = None, raise_error: Exception | None = None) -> None:
        self.response_override = response_override
        self.raise_error = raise_error
        self.call_count = 0

    def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        self.call_count += 1
        if self.raise_error:
            raise self.raise_error
        if self.response_override is not None:
            return self.response_override

        # Return a rich, valid JSON plan matching standard inputs
        plan = {
            "dashboard_title": "Executive Trade Intelligence Overview",
            "business_objective": "Track international trade dynamics, balance of trade, and partner distributions with validated quality assurance.",
            "target_audience": "Senior Trade Executives & Policy Directors",
            "pages": [
                {
                    "name": "ExecutiveOverview",
                    "display_name": "Executive Overview",
                    "description": "Headline trade indicators and overarching trend.",
                    "visuals": [
                        {
                            "id": "total_imports_card",
                            "title": "Total Imports",
                            "visual_type": "card",
                            "kpi_reference": "total_imports",
                            "roles": {"Values": [{"kind": "Measure", "table": "FactTrade", "name": "Total Imports"}]},
                            "selection_reason": "High-priority KPI for immediate executive visibility.",
                            "priority": "high",
                            "evidence_references": ["kpi:total_imports"],
                        },
                        {
                            "id": "total_exports_card",
                            "title": "Total Exports",
                            "visual_type": "card",
                            "kpi_reference": "total_exports",
                            "roles": {"Values": [{"kind": "Measure", "table": "FactTrade", "name": "Total Exports"}]},
                            "selection_reason": "Core revenue flow indicator for outward international trade.",
                            "priority": "high",
                            "evidence_references": ["kpi:total_exports"],
                        },
                        {
                            "id": "trade_balance_card",
                            "title": "Trade Balance",
                            "visual_type": "card",
                            "kpi_reference": "trade_balance",
                            "roles": {"Values": [{"kind": "Measure", "table": "FactTrade", "name": "Trade Balance"}]},
                            "selection_reason": "Critical macroeconomic metric showing trade surplus or deficit.",
                            "priority": "high",
                            "evidence_references": ["kpi:trade_balance"],
                        },
                        {
                            "id": "transaction_count_card",
                            "title": "Transaction Count",
                            "visual_type": "card",
                            "kpi_reference": "transaction_count",
                            "roles": {"Values": [{"kind": "Measure", "table": "FactTrade", "name": "Transaction Count"}]},
                            "selection_reason": "Operational volume indicator.",
                            "priority": "medium",
                            "evidence_references": ["kpi:transaction_count"],
                        },
                        {
                            "id": "trend",
                            "title": "Import and Export Trends",
                            "visual_type": "lineChart",
                            "kpi_reference": "total_imports",
                            "roles": {
                                "Category": [{"kind": "Column", "table": "DimDate", "name": "Date"}],
                                "Y": [
                                    {"kind": "Measure", "table": "FactTrade", "name": "Total Imports"},
                                    {"kind": "Measure", "table": "FactTrade", "name": "Total Exports"},
                                ],
                            },
                            "selection_reason": "Time-series comparison of import vs export trajectories.",
                            "priority": "high",
                            "evidence_references": ["kpi:total_imports", "kpi:total_exports"],
                        },
                    ],
                },
                {
                    "name": "TradePerformance",
                    "display_name": "Trade Performance",
                    "description": "Temporal dynamics and direction filtering.",
                    "visuals": [
                        {
                            "id": "performance",
                            "title": "Imports versus Exports",
                            "visual_type": "lineChart",
                            "roles": {
                                "Category": [{"kind": "Column", "table": "DimDate", "name": "Date"}],
                                "Series": [{"kind": "Column", "table": "FactTrade", "name": "Direction"}],
                                "Y": [{"kind": "Sum", "table": "FactTrade", "name": "Value"}],
                            },
                            "selection_reason": "Detailed directional evolution over time.",
                            "priority": "medium",
                            "evidence_references": ["FactTrade.Direction"],
                        },
                        {
                            "id": "direction",
                            "title": "Trade Direction",
                            "visual_type": "slicer",
                            "roles": {"Values": [{"kind": "Column", "table": "FactTrade", "name": "Direction"}]},
                            "selection_reason": "Interactive filter for toggling Import and Export flows.",
                            "priority": "medium",
                            "evidence_references": ["FactTrade.Direction"],
                        },
                    ],
                },
                {
                    "name": "CountryAnalysis",
                    "display_name": "Country Analysis",
                    "description": "Geographical trade distribution and bilateral rankings.",
                    "visuals": [
                        {
                            "id": "countries",
                            "title": "Trade by Country",
                            "visual_type": "clusteredBarChart",
                            "roles": {
                                "Category": [{"kind": "Column", "table": "DimCountry", "name": "Country"}],
                                "Series": [{"kind": "Column", "table": "FactTrade", "name": "Direction"}],
                                "Y": [{"kind": "Sum", "table": "FactTrade", "name": "Value"}],
                            },
                            "selection_reason": "Identify major trading partner concentrations.",
                            "priority": "high",
                            "evidence_references": ["DimCountry.Country"],
                        },
                        {
                            "id": "country_detail",
                            "title": "Country Comparison",
                            "visual_type": "tableEx",
                            "roles": {
                                "Values": [
                                    {"kind": "Column", "table": "DimCountry", "name": "Country"},
                                    {"kind": "Column", "table": "FactTrade", "name": "Direction"},
                                    {"kind": "Sum", "table": "FactTrade", "name": "Value"},
                                ]
                            },
                            "selection_reason": "Tabular audit of exact country values by flow direction.",
                            "priority": "low",
                            "evidence_references": ["DimCountry.Country"],
                        },
                    ],
                },
                {
                    "name": "ProductAnalysis",
                    "display_name": "Product Analysis",
                    "description": "Product category contribution and commodity breakdown.",
                    "visuals": [
                        {
                            "id": "products",
                            "title": "Trade by Product",
                            "visual_type": "clusteredBarChart",
                            "roles": {
                                "Category": [{"kind": "Column", "table": "DimProduct", "name": "Product"}],
                                "Series": [{"kind": "Column", "table": "FactTrade", "name": "Direction"}],
                                "Y": [{"kind": "Sum", "table": "FactTrade", "name": "Value"}],
                            },
                            "selection_reason": "Rank product categories by trade volume.",
                            "priority": "high",
                            "evidence_references": ["DimProduct.Product"],
                        },
                        {
                            "id": "product_detail",
                            "title": "Product and Country Detail",
                            "visual_type": "tableEx",
                            "roles": {
                                "Values": [
                                    {"kind": "Column", "table": "DimProduct", "name": "Product"},
                                    {"kind": "Column", "table": "DimCountry", "name": "Country"},
                                    {"kind": "Sum", "table": "FactTrade", "name": "Value"},
                                ]
                            },
                            "selection_reason": "Multi-dimensional exploration of commodity flows.",
                            "priority": "low",
                            "evidence_references": ["DimProduct.Product"],
                        },
                    ],
                },
                {
                    "name": "BusinessInsights",
                    "display_name": "Business Insights",
                    "description": "Validated findings and recommendations from upstream analytics.",
                    "visuals": [
                        {
                            "id": "insights",
                            "title": "Validated Business Insights",
                            "visual_type": "tableEx",
                            "insight_reference": "synthetic-finding-1",
                            "roles": {
                                "Values": [
                                    {"kind": "Column", "table": "BusinessInsights", "name": "Kind"},
                                    {"kind": "Column", "table": "BusinessInsights", "name": "Text"},
                                ]
                            },
                            "selection_reason": "Presents analytical narrative and strategic recommendations.",
                            "priority": "high",
                            "evidence_references": ["insights:synthetic-finding-1"],
                        }
                    ],
                },
                {
                    "name": "DataQualityTraceability",
                    "display_name": "Data Quality & Traceability",
                    "description": "Provenance evidence, audit status, and generator diagnostics.",
                    "visuals": [
                        {
                            "id": "quality",
                            "title": "Data Quality Indicators",
                            "visual_type": "tableEx",
                            "roles": {
                                "Values": [
                                    {"kind": "Column", "table": "DataQuality", "name": "Check"},
                                    {"kind": "Column", "table": "DataQuality", "name": "Result"},
                                ]
                            },
                            "selection_reason": "Critical governance and audit verification page.",
                            "priority": "high",
                            "evidence_references": ["quality_report"],
                        }
                    ],
                },
            ],
            "kpi_references": ["total_imports", "total_exports", "trade_balance", "transaction_count"],
            "insight_references": ["synthetic-finding-1"],
            "warnings": [],
            "planning_mode": "ai_assisted",
            "metadata": {"provider": "mock", "model": "mock-planner-v1"},
        }
        return json.dumps(plan)


class HTTPLLMProvider(LLMProvider):
    """Generic HTTP REST provider for OpenAI-compatible or custom LLM endpoints."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        timeout: float = 15.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        if not self.api_key:
            raise ValueError("API key missing. Set LLM_API_KEY environment variable.")
        import urllib.request
        import urllib.error

        url = f"{self.base_url}/chat/completions"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result["choices"][0]["message"]["content"]
        except urllib.error.URLError as exc:
            raise RuntimeError(f"HTTP LLM Provider request failed: {exc}") from exc


class MetaResponsesProvider(LLMProvider):
    """Meta Responses API provider using its streaming SSE response format."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.meta.ai/v1",
        model: str = "muse-wonder-1.0",
        timeout: float = 15.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        if not self.api_key:
            raise ValueError("Meta API key missing. Set MODEL_API_KEY, META_API_KEY, or LLM_API_KEY.")
        import urllib.error
        import urllib.request

        payload = {
            "model": self.model,
            "input": [{"role": "user", "content": prompt}],
            "stream": True,
            "temperature": 0.1,
            "max_output_tokens": 32000,
            "top_p": 1,
            "reasoning": {"effort": "medium"},
        }
        if system_prompt:
            payload["instructions"] = system_prompt
        request = urllib.request.Request(
            f"{self.base_url}/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            method="POST",
        )
        chunks: list[str] = []
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = event.get("delta")
                    if isinstance(delta, str):
                        chunks.append(delta)
                    output_text = event.get("text")
                    if isinstance(output_text, str) and event.get("type") == "response.output_text.done":
                        chunks.append(output_text)
            result = "".join(chunks).strip()
            if not result:
                raise RuntimeError("Meta Responses API returned no output text.")
            return result
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Meta Responses API request failed with HTTP {exc.code}.") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Meta Responses API request failed: {exc.reason}") from exc


def create_deterministic_plan(bundle: Any, user_context: dict[str, Any] | None = None) -> DashboardPlan:
    """Build the baseline deterministic dashboard plan matching the validated 6-page contract."""
    if not getattr(bundle, "trade_mode", True):
        # Hors contrat import/export, le plan décrit le rapport générique réellement généré
        # (mêmes pages et mêmes champs que le PBIP), et non les 6 pages trade.
        from src.agents.dashboard.generic_report import build_generic_plan
        from src.agents.dashboard.role_report import build_role_plan, can_build

        # Schéma en étoile disponible : tableaux de bord par rôle (commercial, admin, finance, logistique).
        return build_role_plan(bundle) if can_build(bundle) else build_generic_plan(bundle)
    kpi_map = {kpi.id: kpi for kpi in bundle.kpis if getattr(kpi, "supported", True)}
    cards = []
    kpi_refs = []
    for kpi_id, kpi in kpi_map.items():
        kpi_refs.append(kpi_id)
        cards.append(
            VisualPlan(
                id=f"{kpi_id}_card",
                title=kpi.name,
                visual_type="card",
                kpi_reference=kpi_id,
                roles={"Values": [{"kind": "Measure", "table": "FactTrade", "name": kpi.name}]},
                selection_reason=f"Standard KPI card for {kpi.name}.",
                priority="high",
                evidence_references=[f"kpi:{kpi_id}"],
            )
        )

    flow_measures = [
        {"kind": "Measure", "table": "FactTrade", "name": kpi_map[k].name}
        for k in ("total_imports", "total_exports")
        if k in kpi_map
    ]

    exec_visuals = cards + [
        VisualPlan(
            id="trend",
            title="Import and Export Trends",
            visual_type="lineChart",
            roles={
                "Category": [{"kind": "Column", "table": "DimDate", "name": "Date"}],
                "Y": flow_measures or [{"kind": "Sum", "table": "FactTrade", "name": "Value"}],
            },
            selection_reason="Headline import/export time trajectory.",
            priority="high",
            evidence_references=["FactTrade.Value", "DimDate.Date"],
        )
    ]

    finding_refs = []
    for f in bundle.findings.get("findings", []):
        if isinstance(f, dict) and "id" in f:
            finding_refs.append(f["id"])

    pages = [
        PagePlan(name="ExecutiveOverview", display_name="Executive Overview", description="Headline indicators and trend.", visuals=exec_visuals),
        PagePlan(
            name="TradePerformance",
            display_name="Trade Performance",
            description="Temporal patterns and direction filter.",
            visuals=[
                VisualPlan(
                    id="performance",
                    title="Imports versus Exports",
                    visual_type="lineChart",
                    roles={
                        "Category": [{"kind": "Column", "table": "DimDate", "name": "Date"}],
                        "Series": [{"kind": "Column", "table": "FactTrade", "name": "Direction"}],
                        "Y": [{"kind": "Sum", "table": "FactTrade", "name": "Value"}],
                    },
                    selection_reason="Imports vs Exports directional breakdown.",
                    priority="high",
                    evidence_references=["FactTrade.Direction"],
                ),
                VisualPlan(
                    id="direction",
                    title="Trade Direction",
                    visual_type="slicer",
                    roles={"Values": [{"kind": "Column", "table": "FactTrade", "name": "Direction"}]},
                    selection_reason="Interactive slicer for directional flow.",
                    priority="medium",
                    evidence_references=["FactTrade.Direction"],
                ),
            ],
        ),
        PagePlan(
            name="CountryAnalysis",
            display_name="Country Analysis",
            description="Country distribution and detail table.",
            visuals=[
                VisualPlan(
                    id="countries",
                    title="Trade by Country",
                    visual_type="clusteredBarChart",
                    roles={
                        "Category": [{"kind": "Column", "table": "DimCountry", "name": "Country"}],
                        "Series": [{"kind": "Column", "table": "FactTrade", "name": "Direction"}],
                        "Y": [{"kind": "Sum", "table": "FactTrade", "name": "Value"}],
                    },
                    selection_reason="Country level contribution bar chart.",
                    priority="high",
                    evidence_references=["DimCountry.Country"],
                ),
                VisualPlan(
                    id="country_detail",
                    title="Country Comparison",
                    visual_type="tableEx",
                    roles={
                        "Values": [
                            {"kind": "Column", "table": "DimCountry", "name": "Country"},
                            {"kind": "Column", "table": "FactTrade", "name": "Direction"},
                            {"kind": "Sum", "table": "FactTrade", "name": "Value"},
                        ]
                    },
                    selection_reason="Tabular audit of country trade values.",
                    priority="low",
                    evidence_references=["DimCountry.Country"],
                ),
            ],
        ),
        PagePlan(
            name="ProductAnalysis",
            display_name="Product Analysis",
            description="Product distribution and detail table.",
            visuals=[
                VisualPlan(
                    id="products",
                    title="Trade by Product",
                    visual_type="clusteredBarChart",
                    roles={
                        "Category": [{"kind": "Column", "table": "DimProduct", "name": "Product"}],
                        "Series": [{"kind": "Column", "table": "FactTrade", "name": "Direction"}],
                        "Y": [{"kind": "Sum", "table": "FactTrade", "name": "Value"}],
                    },
                    selection_reason="Product level contribution bar chart.",
                    priority="high",
                    evidence_references=["DimProduct.Product"],
                ),
                VisualPlan(
                    id="product_detail",
                    title="Product and Country Detail",
                    visual_type="tableEx",
                    roles={
                        "Values": [
                            {"kind": "Column", "table": "DimProduct", "name": "Product"},
                            {"kind": "Column", "table": "DimCountry", "name": "Country"},
                            {"kind": "Sum", "table": "FactTrade", "name": "Value"},
                        ]
                    },
                    selection_reason="Cross-dimensional commodity breakdown.",
                    priority="low",
                    evidence_references=["DimProduct.Product"],
                ),
            ],
        ),
        PagePlan(
            name="BusinessInsights",
            display_name="Business Insights",
            description="Upstream findings and recommendations.",
            visuals=[
                VisualPlan(
                    id="insights",
                    title="Validated Business Insights",
                    visual_type="tableEx",
                    roles={
                        "Values": [
                            {"kind": "Column", "table": "BusinessInsights", "name": "Kind"},
                            {"kind": "Column", "table": "BusinessInsights", "name": "Text"},
                        ]
                    },
                    selection_reason="Narrative table of findings and recommendations.",
                    priority="high",
                    evidence_references=finding_refs or ["findings"],
                )
            ],
        ),
        PagePlan(
            name="DataQualityTraceability",
            display_name="Data Quality & Traceability",
            description="Data quality indicators and generator diagnostics.",
            visuals=[
                VisualPlan(
                    id="quality",
                    title="Data Quality Indicators",
                    visual_type="tableEx",
                    roles={
                        "Values": [
                            {"kind": "Column", "table": "DataQuality", "name": "Check"},
                            {"kind": "Column", "table": "DataQuality", "name": "Result"},
                        ]
                    },
                    selection_reason="Quality and audit verification table.",
                    priority="high",
                    evidence_references=["quality_report"],
                )
            ],
        ),
    ]

    return DashboardPlan(
        dashboard_title="BI Flow Trade Intelligence Dashboard",
        business_objective="Standard deterministic trade performance and quality overview.",
        target_audience="Executive Leadership & Trade Operations",
        pages=pages,
        kpi_references=kpi_refs,
        insight_references=finding_refs,
        warnings=[],
        planning_mode="deterministic",
        metadata={"compiler": "deterministic_baseline"},
    )


def _extract_domain_guidance(retrieved_knowledge: list[RetrievedChunk] | None, topic: str) -> str:
    """Find the most relevant domain guideline snippet for a visual topic."""
    if not retrieved_knowledge:
        return "Standard Power BI visual guidelines apply."
    for rk in retrieved_knowledge:
        if topic.lower() in rk.chunk.title.lower() or topic.lower() in rk.chunk.content.lower():
            lines = [l.strip() for l in rk.chunk.content.splitlines() if l.strip() and not l.strip().startswith("#")]
            return lines[0] if lines else rk.chunk.title
    return retrieved_knowledge[0].chunk.title


def build_executive_candidate(
    bundle: Any,
    spec: Any,
    retrieved_knowledge: list[RetrievedChunk] | None = None,
) -> DashboardPlan:
    """Strategy 1: Executive Summary & Macro Trends."""
    kpi_map = {kpi.id: kpi for kpi in bundle.kpis if getattr(kpi, "supported", True)}
    cards = []
    for kpi_id, kpi in kpi_map.items():
        cards.append(
            VisualPlan(
                id=f"{kpi_id}_card",
                title=kpi.name,
                visual_type="card",
                kpi_reference=kpi_id,
                roles={"Values": [{"kind": "Measure", "table": "FactTrade", "name": kpi.name}]},
                selection_reason=f"High-priority executive KPI card for {kpi.name}.",
                priority="high",
                evidence_references=[f"kpi:{kpi_id}", "cleaned_trade.csv"],
                evidence=VisualEvidence(
                    business_question="What is our headline operational trade volume and balance?",
                    kpi_or_insight=kpi.name,
                    visual_suitability_reason="Single-metric KPI cards give zero-latency visibility for executive leadership.",
                    upstream_source="cleaned_trade.csv",
                    domain_rule=_extract_domain_guidance(retrieved_knowledge, "kpi"),
                    evidence_category=EvidenceCategory.UPSTREAM_FACT,
                ),
            )
        )

    flow_measures = [
        {"kind": "Measure", "table": "FactTrade", "name": kpi_map[k].name}
        for k in ("total_imports", "total_exports")
        if k in kpi_map
    ]

    exec_visuals = cards + [
        VisualPlan(
            id="trend",
            title="Import and Export Trends",
            visual_type="lineChart",
            roles={
                "Category": [{"kind": "Column", "table": "DimDate", "name": "Date"}],
                "Y": flow_measures or [{"kind": "Sum", "table": "FactTrade", "name": "Value"}],
            },
            selection_reason="Temporal trajectory of imports vs exports.",
            priority="high",
            evidence_references=["FactTrade.Value", "DimDate.Date"],
            evidence=VisualEvidence(
                business_question="How are import and export flows trending over reporting periods?",
                kpi_or_insight="Total Imports & Total Exports",
                visual_suitability_reason="Line charts are best suited for continuous chronological trends.",
                upstream_source="FactTrade.Value",
                domain_rule=_extract_domain_guidance(retrieved_knowledge, "trend"),
                evidence_category=EvidenceCategory.UPSTREAM_FACT,
            ),
        )
    ]

    finding_refs = [f.get("id") for f in bundle.findings.get("findings", []) if isinstance(f, dict) and "id" in f]

    pages = [
        PagePlan(name="ExecutiveOverview", display_name="Executive Overview", description="Headline indicators and macro trend.", visuals=exec_visuals),
        PagePlan(
            name="TradePerformance",
            display_name="Trade Performance",
            description="Directional flow breakdown and interactive filter.",
            visuals=[
                VisualPlan(
                    id="performance",
                    title="Imports versus Exports",
                    visual_type="lineChart",
                    roles={
                        "Category": [{"kind": "Column", "table": "DimDate", "name": "Date"}],
                        "Series": [{"kind": "Column", "table": "FactTrade", "name": "Direction"}],
                        "Y": [{"kind": "Sum", "table": "FactTrade", "name": "Value"}],
                    },
                    selection_reason="Imports vs Exports directional breakdown.",
                    priority="high",
                    evidence_references=["FactTrade.Direction"],
                    evidence=VisualEvidence(
                        business_question="What is the directional divergence between trade flows?",
                        kpi_or_insight="FactTrade.Direction",
                        visual_suitability_reason="Multi-series line chart separates import and export volumes on common time axis.",
                        upstream_source="FactTrade",
                        domain_rule=_extract_domain_guidance(retrieved_knowledge, "direction"),
                        evidence_category=EvidenceCategory.UPSTREAM_FACT,
                    ),
                ),
                VisualPlan(
                    id="direction",
                    title="Trade Direction",
                    visual_type="slicer",
                    roles={"Values": [{"kind": "Column", "table": "FactTrade", "name": "Direction"}]},
                    selection_reason="Interactive slicer for directional flow.",
                    priority="medium",
                    evidence_references=["FactTrade.Direction"],
                    evidence=VisualEvidence(
                        business_question="How can users isolate specific import or export transactions?",
                        kpi_or_insight="Direction",
                        visual_suitability_reason="Single-select or multi-select slicer provides intuitive canvas filtering.",
                        upstream_source="FactTrade.Direction",
                        domain_rule=_extract_domain_guidance(retrieved_knowledge, "slicer"),
                        evidence_category=EvidenceCategory.UPSTREAM_FACT,
                    ),
                ),
            ],
        ),
        PagePlan(
            name="BusinessInsights",
            display_name="Business Insights",
            description="Validated analyst findings and strategic recommendations.",
            visuals=[
                VisualPlan(
                    id="insights",
                    title="Validated Business Insights",
                    visual_type="tableEx",
                    roles={
                        "Values": [
                            {"kind": "Column", "table": "BusinessInsights", "name": "Kind"},
                            {"kind": "Column", "table": "BusinessInsights", "name": "Text"},
                        ]
                    },
                    selection_reason="Narrative table of findings and recommendations.",
                    priority="high",
                    evidence_references=finding_refs or ["findings"],
                    evidence=VisualEvidence(
                        business_question="What qualitative insights and recommendations did analysts discover?",
                        kpi_or_insight="bi_analyst_findings",
                        visual_suitability_reason="Tabular layout ensures accurate verbatim reporting of analyst observations.",
                        upstream_source="bi_analyst_findings.json",
                        domain_rule=_extract_domain_guidance(retrieved_knowledge, "finding"),
                        evidence_category=EvidenceCategory.RECOMMENDATION,
                    ),
                )
            ],
        ),
        PagePlan(
            name="DataQualityTraceability",
            display_name="Data Quality & Traceability",
            description="Upstream quality checks, row counts, and governance audit.",
            visuals=[
                VisualPlan(
                    id="quality",
                    title="Data Quality Indicators",
                    visual_type="tableEx",
                    roles={
                        "Values": [
                            {"kind": "Column", "table": "DataQuality", "name": "Check"},
                            {"kind": "Column", "table": "DataQuality", "name": "Result"},
                        ]
                    },
                    selection_reason="Quality and audit verification table.",
                    priority="high",
                    evidence_references=["data_quality_report"],
                    evidence=VisualEvidence(
                        business_question="What data governance evidence validates the underlying datasets?",
                        kpi_or_insight="data_quality_report",
                        visual_suitability_reason="Audit table preserves full upstream gate verification.",
                        upstream_source="data_quality_report.json",
                        domain_rule=_extract_domain_guidance(retrieved_knowledge, "quality"),
                        evidence_category=EvidenceCategory.UPSTREAM_FACT,
                    ),
                )
            ],
        ),
    ]

    return DashboardPlan(
        dashboard_title="Executive Trade Intelligence Overview",
        business_objective=getattr(spec, "business_objective", "Executive overview of trade indicators."),
        target_audience=getattr(spec, "target_audience", "Executive Leadership"),
        pages=pages,
        kpi_references=list(kpi_map.keys()),
        insight_references=finding_refs,
        warnings=[],
        planning_mode="ai_assisted",
        metadata={
            "candidate_id": "candidate_executive",
            "strategy_name": "Executive Summary & Macro Trends",
            "focus": "Executive overview, summary KPI cards, temporal macro trends, and governance",
        },
    )


def build_analytical_candidate(
    bundle: Any,
    spec: Any,
    retrieved_knowledge: list[RetrievedChunk] | None = None,
) -> DashboardPlan:
    """Strategy 2: Dimensional Breakdown & Commodity Matrix."""
    kpi_map = {kpi.id: kpi for kpi in bundle.kpis if getattr(kpi, "supported", True)}
    cards = [
        VisualPlan(
            id=f"{kpi_id}_card",
            title=kpi.name,
            visual_type="card",
            kpi_reference=kpi_id,
            roles={"Values": [{"kind": "Measure", "table": "FactTrade", "name": kpi.name}]},
            selection_reason=f"KPI card for {kpi.name}.",
            priority="high",
            evidence_references=[f"kpi:{kpi_id}"],
            evidence=VisualEvidence(
                business_question="What is the baseline volume for this indicator?",
                kpi_or_insight=kpi.name,
                visual_suitability_reason="Instant numeric benchmark.",
                upstream_source="FactTrade",
                domain_rule=_extract_domain_guidance(retrieved_knowledge, "kpi"),
                evidence_category=EvidenceCategory.UPSTREAM_FACT,
            ),
        )
        for kpi_id, kpi in kpi_map.items()
    ]

    flow_measures = [
        {"kind": "Measure", "table": "FactTrade", "name": kpi_map[k].name}
        for k in ("total_imports", "total_exports")
        if k in kpi_map
    ]

    pages = [
        PagePlan(
            name="ExecutiveOverview",
            display_name="Executive Overview",
            description="Headline indicators and monthly trend.",
            visuals=cards + [
                VisualPlan(
                    id="trend",
                    title="Import and Export Trends",
                    visual_type="lineChart",
                    roles={
                        "Category": [{"kind": "Column", "table": "DimDate", "name": "Date"}],
                        "Y": flow_measures or [{"kind": "Sum", "table": "FactTrade", "name": "Value"}],
                    },
                    selection_reason="Trajectory across periods.",
                    priority="high",
                    evidence_references=["FactTrade.Value"],
                )
            ],
        ),
        PagePlan(
            name="CountryAnalysis",
            display_name="Country Analysis",
            description="Geographical trade distribution and bilateral rankings.",
            visuals=[
                VisualPlan(
                    id="countries",
                    title="Trade by Country",
                    visual_type="clusteredBarChart",
                    roles={
                        "Category": [{"kind": "Column", "table": "DimCountry", "name": "Country"}],
                        "Series": [{"kind": "Column", "table": "FactTrade", "name": "Direction"}],
                        "Y": [{"kind": "Sum", "table": "FactTrade", "name": "Value"}],
                    },
                    selection_reason="Identify major trading partner concentrations.",
                    priority="high",
                    evidence_references=["DimCountry.Country"],
                    evidence=VisualEvidence(
                        business_question="Which trading partners account for the largest volumes and directional imbalances?",
                        kpi_or_insight="DimCountry.Country",
                        visual_suitability_reason="Clustered bar charts allow direct comparative ranking across geographic partners.",
                        upstream_source="DimCountry x FactTrade",
                        domain_rule=_extract_domain_guidance(retrieved_knowledge, "country"),
                        evidence_category=EvidenceCategory.UPSTREAM_FACT,
                    ),
                ),
                VisualPlan(
                    id="country_detail",
                    title="Country Comparison",
                    visual_type="tableEx",
                    roles={
                        "Values": [
                            {"kind": "Column", "table": "DimCountry", "name": "Country"},
                            {"kind": "Column", "table": "FactTrade", "name": "Direction"},
                            {"kind": "Sum", "table": "FactTrade", "name": "Value"},
                        ]
                    },
                    selection_reason="Tabular audit of exact country values by flow direction.",
                    priority="low",
                    evidence_references=["DimCountry.Country"],
                    evidence=VisualEvidence(
                        business_question="What are the precise transaction amounts per country?",
                        kpi_or_insight="DimCountry",
                        visual_suitability_reason="Detailed data grid supports audit reconciliation.",
                        upstream_source="FactTrade",
                        domain_rule=_extract_domain_guidance(retrieved_knowledge, "table"),
                        evidence_category=EvidenceCategory.UPSTREAM_FACT,
                    ),
                ),
            ],
        ),
        PagePlan(
            name="ProductAnalysis",
            display_name="Product Analysis",
            description="Commodity contribution and cross-dimensional breakdown.",
            visuals=[
                VisualPlan(
                    id="products",
                    title="Trade by Product",
                    visual_type="clusteredBarChart",
                    roles={
                        "Category": [{"kind": "Column", "table": "DimProduct", "name": "Product"}],
                        "Series": [{"kind": "Column", "table": "FactTrade", "name": "Direction"}],
                        "Y": [{"kind": "Sum", "table": "FactTrade", "name": "Value"}],
                    },
                    selection_reason="Rank product categories by trade volume.",
                    priority="high",
                    evidence_references=["DimProduct.Product"],
                    evidence=VisualEvidence(
                        business_question="Which product categories drive international trade volume?",
                        kpi_or_insight="DimProduct.Product",
                        visual_suitability_reason="Categorical ranking highlights commodity concentration risks.",
                        upstream_source="DimProduct x FactTrade",
                        domain_rule=_extract_domain_guidance(retrieved_knowledge, "product"),
                        evidence_category=EvidenceCategory.UPSTREAM_FACT,
                    ),
                ),
                VisualPlan(
                    id="product_detail",
                    title="Product and Country Detail",
                    visual_type="tableEx",
                    roles={
                        "Values": [
                            {"kind": "Column", "table": "DimProduct", "name": "Product"},
                            {"kind": "Column", "table": "DimCountry", "name": "Country"},
                            {"kind": "Sum", "table": "FactTrade", "name": "Value"},
                        ]
                    },
                    selection_reason="Cross-dimensional commodity breakdown.",
                    priority="low",
                    evidence_references=["DimProduct.Product"],
                ),
            ],
        ),
        PagePlan(
            name="TradePerformance",
            display_name="Trade Performance",
            description="Directional flow breakdown and temporal dynamics.",
            visuals=[
                VisualPlan(
                    id="performance",
                    title="Imports versus Exports",
                    visual_type="lineChart",
                    roles={
                        "Category": [{"kind": "Column", "table": "DimDate", "name": "Date"}],
                        "Series": [{"kind": "Column", "table": "FactTrade", "name": "Direction"}],
                        "Y": [{"kind": "Sum", "table": "FactTrade", "name": "Value"}],
                    },
                    selection_reason="Directional breakdown.",
                    priority="medium",
                    evidence_references=["FactTrade.Direction"],
                ),
                VisualPlan(
                    id="direction",
                    title="Trade Direction",
                    visual_type="slicer",
                    roles={"Values": [{"kind": "Column", "table": "FactTrade", "name": "Direction"}]},
                    selection_reason="Interactive slicer.",
                    priority="medium",
                    evidence_references=["FactTrade.Direction"],
                ),
            ],
        ),
        PagePlan(
            name="DataQualityTraceability",
            display_name="Data Quality & Traceability",
            description="Data quality indicators and generator diagnostics.",
            visuals=[
                VisualPlan(
                    id="quality",
                    title="Data Quality Indicators",
                    visual_type="tableEx",
                    roles={
                        "Values": [
                            {"kind": "Column", "table": "DataQuality", "name": "Check"},
                            {"kind": "Column", "table": "DataQuality", "name": "Result"},
                        ]
                    },
                    selection_reason="Quality and audit verification table.",
                    priority="high",
                    evidence_references=["quality_report"],
                )
            ],
        ),
    ]

    return DashboardPlan(
        dashboard_title="Dimensional Trade Exposure & Commodity Analysis",
        business_objective=getattr(spec, "business_objective", "Granular country and product trade analysis."),
        target_audience="Senior Trade Analysts and International Policy Planners",
        pages=pages,
        kpi_references=list(kpi_map.keys()),
        insight_references=[],
        warnings=[],
        planning_mode="ai_assisted",
        metadata={
            "candidate_id": "candidate_analytical",
            "strategy_name": "Dimensional Breakdown & Commodity Matrix",
            "focus": "Granular country rankings, commodity breakdowns, cross-dimensional tables",
        },
    )


def build_decision_candidate(
    bundle: Any,
    spec: Any,
    retrieved_knowledge: list[RetrievedChunk] | None = None,
) -> DashboardPlan:
    """Strategy 3: Actionable Insights & Risk Governance."""
    kpi_map = {kpi.id: kpi for kpi in bundle.kpis if getattr(kpi, "supported", True)}
    finding_refs = [f.get("id") for f in bundle.findings.get("findings", []) if isinstance(f, dict) and "id" in f]

    cards = [
        VisualPlan(
            id=f"{kpi_id}_card",
            title=kpi.name,
            visual_type="card",
            kpi_reference=kpi_id,
            roles={"Values": [{"kind": "Measure", "table": "FactTrade", "name": kpi.name}]},
            selection_reason=f"Decision indicator for {kpi.name}.",
            priority="high",
            evidence_references=[f"kpi:{kpi_id}"],
        )
        for kpi_id, kpi in kpi_map.items()
        if kpi_id in ("total_exports", "total_imports", "trade_balance", "transaction_count")
    ]

    pages = [
        PagePlan(
            name="BusinessInsights",
            display_name="Business Insights",
            description="Validated findings, anomalies, and strategic recommendations.",
            visuals=[
                VisualPlan(
                    id="insights",
                    title="Validated Business Insights",
                    visual_type="tableEx",
                    roles={
                        "Values": [
                            {"kind": "Column", "table": "BusinessInsights", "name": "Kind"},
                            {"kind": "Column", "table": "BusinessInsights", "name": "Text"},
                        ]
                    },
                    selection_reason="Strategic recommendations table.",
                    priority="high",
                    evidence_references=finding_refs or ["findings"],
                    evidence=VisualEvidence(
                        business_question="What action items and risks did upstream analytics detect?",
                        kpi_or_insight="bi_analyst_findings",
                        visual_suitability_reason="Direct actionable narrative table for strategic decisions.",
                        upstream_source="bi_analyst_findings.json",
                        domain_rule=_extract_domain_guidance(retrieved_knowledge, "finding"),
                        evidence_category=EvidenceCategory.RECOMMENDATION,
                    ),
                )
            ],
        ),
        PagePlan(
            name="ExecutiveOverview",
            display_name="Executive Overview",
            description="KPI balance cards and headline reconciliation.",
            visuals=cards + [
                VisualPlan(
                    id="trend",
                    title="Import and Export Trends",
                    visual_type="lineChart",
                    roles={
                        "Category": [{"kind": "Column", "table": "DimDate", "name": "Date"}],
                        "Y": [
                            {"kind": "Measure", "table": "FactTrade", "name": kpi_map["total_imports"].name},
                            {"kind": "Measure", "table": "FactTrade", "name": kpi_map["total_exports"].name},
                        ] if "total_imports" in kpi_map and "total_exports" in kpi_map else [{"kind": "Sum", "table": "FactTrade", "name": "Value"}],
                    },
                    selection_reason="Macro trajectory.",
                    priority="high",
                    evidence_references=["FactTrade.Value"],
                )
            ],
        ),
        PagePlan(
            name="CountryAnalysis",
            display_name="Country Analysis",
            description="Country concentrations and bilateral exposure.",
            visuals=[
                VisualPlan(
                    id="countries",
                    title="Trade by Country",
                    visual_type="clusteredBarChart",
                    roles={
                        "Category": [{"kind": "Column", "table": "DimCountry", "name": "Country"}],
                        "Series": [{"kind": "Column", "table": "FactTrade", "name": "Direction"}],
                        "Y": [{"kind": "Sum", "table": "FactTrade", "name": "Value"}],
                    },
                    selection_reason="Partner concentration bar chart.",
                    priority="high",
                    evidence_references=["DimCountry.Country"],
                ),
                VisualPlan(
                    id="direction",
                    title="Trade Direction",
                    visual_type="slicer",
                    roles={"Values": [{"kind": "Column", "table": "FactTrade", "name": "Direction"}]},
                    selection_reason="Interactive slicer.",
                    priority="medium",
                    evidence_references=["FactTrade.Direction"],
                ),
            ],
        ),
        PagePlan(
            name="DataQualityTraceability",
            display_name="Data Quality & Traceability",
            description="Upstream quality verification and generator diagnostics.",
            visuals=[
                VisualPlan(
                    id="quality",
                    title="Data Quality Indicators",
                    visual_type="tableEx",
                    roles={
                        "Values": [
                            {"kind": "Column", "table": "DataQuality", "name": "Check"},
                            {"kind": "Column", "table": "DataQuality", "name": "Result"},
                        ]
                    },
                    selection_reason="Quality and audit verification table.",
                    priority="high",
                    evidence_references=["quality_report"],
                )
            ],
        ),
    ]

    return DashboardPlan(
        dashboard_title="Trade Decisions & Strategic Findings Cockpit",
        business_objective=getattr(spec, "business_objective", "Action-oriented trade governance and decision cockpit."),
        target_audience="Trade Operations Taskforce & Compliance Directors",
        pages=pages,
        kpi_references=list(kpi_map.keys()),
        insight_references=finding_refs,
        warnings=[],
        planning_mode="ai_assisted",
        metadata={
            "candidate_id": "candidate_decision",
            "strategy_name": "Actionable Insights & Risk Governance",
            "focus": "Findings-first organization, operational risk indicators, bilateral drills",
        },
    )


def generate_candidate_plans(
    bundle: Any,
    spec: Any,
    retrieved_knowledge: list[RetrievedChunk] | None = None,
    provider: LLMProvider | None = None,
    candidate_count: int = 3,
) -> list[DashboardPlan]:
    """Generate multiple meaningfully different candidate dashboard plans from validated inputs."""
    candidates = [
        build_executive_candidate(bundle, spec, retrieved_knowledge),
        build_analytical_candidate(bundle, spec, retrieved_knowledge),
        build_decision_candidate(bundle, spec, retrieved_knowledge),
    ]
    return candidates[:candidate_count]


def revise_plan(
    candidate: DashboardPlan,
    critic_report: Any,
    bundle: Any,
    spec: Any,
) -> DashboardPlan:
    """Apply bounded deterministic revisions to resolve critic feedback."""
    revised = candidate.model_copy(deep=True)
    existing_pages = {p.name for p in revised.pages}

    # If critic warned that ExecutiveOverview is recommended and missing:
    if "ExecutiveOverview" not in existing_pages and any("ExecutiveOverview" in s for s in critic_report.revision_suggestions):
        kpi_map = {kpi.id: kpi for kpi in bundle.kpis if getattr(kpi, "supported", True)}
        cards = [
            VisualPlan(
                id=f"{kpi_id}_card",
                title=kpi.name,
                visual_type="card",
                kpi_reference=kpi_id,
                roles={"Values": [{"kind": "Measure", "table": "FactTrade", "name": kpi.name}]},
                selection_reason=f"KPI card for {kpi.name}.",
            )
            for kpi_id, kpi in kpi_map.items()
        ]
        revised.pages.insert(0, PagePlan(name="ExecutiveOverview", display_name="Executive Overview", description="Executive headline overview.", visuals=cards))
        revised.warnings.append("Applied revision: inserted ExecutiveOverview page per critic recommendation.")

    # If critic warned that DataQualityTraceability is missing:
    if "DataQualityTraceability" not in existing_pages and any("DataQualityTraceability" in s for s in critic_report.revision_suggestions):
        revised.pages.append(
            PagePlan(
                name="DataQualityTraceability",
                display_name="Data Quality & Traceability",
                description="Quality evidence and provenance.",
                visuals=[
                    VisualPlan(
                        id="quality",
                        title="Data Quality Indicators",
                        visual_type="tableEx",
                        roles={"Values": [{"kind": "Column", "table": "DataQuality", "name": "Check"}, {"kind": "Column", "table": "DataQuality", "name": "Result"}]},
                        selection_reason="Quality audit table.",
                    )
                ],
            )
        )
        revised.warnings.append("Applied revision: appended DataQualityTraceability page per governance policy.")

    revised.metadata["revisions_applied"] = revised.metadata.get("revisions_applied", 0) + 1
    return revised


class GeminiLLMProvider(LLMProvider):
    """Google Gemini via the native generateContent REST API (API key in the X-goog-api-key header)."""

    ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, api_key: str | None = None, model: str = "gemini-2.5-flash", timeout: float = 60.0) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        if not self.api_key:
            raise ValueError("API key missing. Set LLM_API_KEY environment variable.")
        import urllib.request
        import urllib.error

        payload: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096},
        }
        if system_prompt:
            payload["system_instruction"] = {"parts": [{"text": system_prompt}]}
        req = urllib.request.Request(
            self.ENDPOINT.format(model=self.model),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-goog-api-key": self.api_key},
        )
        import time

        result: dict[str, Any] = {}
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
                # 429 and 503 are temporary overloads: retry with a short backoff.
                if exc.code in (429, 503) and attempt < 2:
                    time.sleep(3 * (attempt + 1))
                    continue
                raise RuntimeError(f"Gemini HTTP {exc.code}: {detail}") from exc
            except urllib.error.URLError as exc:
                raise RuntimeError(f"Gemini request failed: {exc}") from exc
        candidates = result.get("candidates") or []
        if not candidates:
            raise RuntimeError(f"Gemini returned no candidates: {str(result)[:200]}")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(part.get("text", "") for part in parts).strip()
        if not text:
            raise RuntimeError("Gemini returned an empty answer.")
        return text


class DashboardPlanner:
    """Orchestrates autonomous AI-assisted multi-candidate planning, quality scoring, and self-criticism."""

    def __init__(
        self,
        config: GeneratorConfig | None = None,
        provider: LLMProvider | None = None,
    ) -> None:
        self.config = config or GeneratorConfig()
        if provider:
            self.provider = provider
        elif self.config.llm_provider == "mock":
            self.provider = MockLLMProvider()
        elif self.config.llm_provider == "gemini":
            self.provider = GeminiLLMProvider(
                api_key=self.config.api_key,
                model=self.config.llm_model,
                timeout=self.config.timeout_seconds,
            )
        elif self.config.llm_provider == "groq":
            api_key = self.config.api_key or os.environ.get("GROQ_API_KEY")
            self.provider = HTTPLLMProvider(
                api_key=api_key,
                base_url=self.config.api_base_url or "https://api.groq.com/openai/v1",
                model=self.config.llm_model if self.config.llm_model != "mock-planner-v1" else "openai/gpt-oss-120b",
                timeout=self.config.timeout_seconds,
            )
        elif self.config.llm_provider == "meta":
            self.provider = MetaResponsesProvider(
                api_key=self.config.api_key,
                base_url=self.config.api_base_url or "https://api.meta.ai/v1",
                model=self.config.llm_model,
                timeout=self.config.timeout_seconds,
            )
        else:
            self.provider = HTTPLLMProvider(
                api_key=self.config.api_key,
                base_url=self.config.api_base_url or "https://api.openai.com/v1",
                model=self.config.llm_model,
                timeout=self.config.timeout_seconds,
            )

    def plan(
        self,
        bundle: Any,
        user_context: dict[str, Any] | str | None = None,
        retrieved_knowledge: list[RetrievedChunk] | None = None,
    ) -> DashboardPlan:
        """Produce a validated DashboardPlan using conversational copilot, multi-candidate scoring, and critic review."""
        if self.config.mode == OperatingMode.DETERMINISTIC:
            return create_deterministic_plan(bundle, user_context if isinstance(user_context, dict) else None)

        from src.agents.dashboard.dashboard_copilot import DashboardCopilot
        from src.agents.dashboard.dashboard_critic import DashboardCritic
        from src.agents.dashboard.dashboard_evaluator import DashboardEvaluator

        try:
            start_time = time.time()

            # 1. Interpret user request with Copilot
            copilot = DashboardCopilot(self.provider)
            spec = copilot.interpret(user_context, bundle)

            # 2. Generate multiple candidate dashboard plans
            candidates = generate_candidate_plans(
                bundle=bundle,
                spec=spec,
                retrieved_knowledge=retrieved_knowledge,
                provider=self.provider,
                candidate_count=self.config.candidate_count,
            )

            # 3. Evaluate candidate plans with DashboardEvaluator
            evaluator = DashboardEvaluator(weights=self.config.evaluator_weights)
            eval_summary = evaluator.evaluate_candidates(candidates, bundle, spec)

            # 4. Identify best candidate
            selected_candidate = next(
                (c for c in candidates if c.metadata.get("candidate_id") == eval_summary.selected_candidate_id),
                candidates[0],
            )

            # 5. Run critic review and bounded revision on selected candidate
            critic = DashboardCritic()
            critic_report = critic.evaluate(selected_candidate, bundle)

            if not critic_report.passed_deterministic or (critic_report.revision_suggestions and self.config.max_revisions > 0):
                revised_candidate = revise_plan(selected_candidate, critic_report, bundle, spec)
                revised_report = critic.evaluate(revised_candidate, bundle)
                if revised_report.passed_deterministic:
                    critic_report = revised_report
                    all_candidates_pool = [revised_candidate] + [c for c in candidates if c.metadata.get("candidate_id") != selected_candidate.metadata.get("candidate_id")]
                    eval_summary = evaluator.evaluate_candidates(
                        all_candidates_pool,
                        bundle,
                        spec,
                    )
                    selected_candidate = next(
                        (c for c in all_candidates_pool if c.metadata.get("candidate_id") == eval_summary.selected_candidate_id),
                        revised_candidate,
                    )
                    critic_report = critic.evaluate(selected_candidate, bundle)

            # 5b. Phase 2A: Adaptive layout optimizer (skipped in deterministic mode)
            optimizer_report = None
            if getattr(self.config, "optimizer_enabled", True):
                try:
                    from src.agents.dashboard.dashboard_optimizer import DashboardOptimizer
                    optimizer = DashboardOptimizer(
                        evaluator=evaluator,
                        critic=critic,
                        max_operations=getattr(self.config, "optimizer_max_operations", 5),
                    )
                    optimized_candidate, optimizer_report = optimizer.optimize(
                        candidates=candidates,
                        selected_plan=selected_candidate,
                        bundle=bundle,
                        spec=spec,
                        eval_summary=eval_summary,
                        user_request=str(user_context or ""),
                    )
                    if optimizer_report.improved:
                        selected_candidate = optimized_candidate
                        critic_report = critic.evaluate(selected_candidate, bundle)
                        logger.info(
                            "Optimizer improved score %.2f â†’ %.2f (%s)",
                            optimizer_report.baseline_score,
                            optimizer_report.optimized_score,
                            optimizer_report.run_id,
                        )
                except Exception as opt_exc:
                    logger.warning("Optimizer failed (non-fatal, retaining pre-optimizer selection): %s", opt_exc)

            # 5c. Phase 2A: Feedback logging
            if getattr(self.config, "feedback_enabled", False):
                try:
                    from src.agents.dashboard.dashboard_feedback import FeedbackLogger
                    fb = FeedbackLogger(
                        path=getattr(self.config, "feedback_path", None) or (
                            __import__("pathlib").Path("generated") / "optimization_feedback.jsonl"
                        ),
                        enabled=True,
                    )
                    fb.log_candidate_selected(
                        run_id=selected_candidate.metadata.get("optimizer_run_id", ""),
                        candidate_id=selected_candidate.metadata.get("candidate_id", ""),
                        strategy_name=selected_candidate.metadata.get("strategy_name", ""),
                        score=selected_candidate.metadata.get("optimizer_score", 0.0),
                        reason=eval_summary.selection_rationale,
                    )
                    if optimizer_report is not None:
                        fb.log_optimization_result(
                            run_id=optimizer_report.run_id,
                            baseline_candidate_id=optimizer_report.baseline_candidate_id,
                            baseline_score=optimizer_report.baseline_score,
                            optimized_candidate_id=optimizer_report.optimized_candidate_id,
                            optimized_score=optimizer_report.optimized_score,
                            improved=optimizer_report.improved,
                            operations_applied=[o.operation_type for o in optimizer_report.operations if o.applied],
                            operations_rejected=optimizer_report.rejected_operations,
                            validation_outcome=optimizer_report.final_critic_status,
                        )
                except Exception as fb_exc:
                    logger.warning("Feedback logger failed (non-fatal): %s", fb_exc)

            # 6. Finalize metadata
            selected_candidate.planning_mode = self.config.mode.value
            selected_candidate.metadata["llm_provider"] = self.config.llm_provider
            selected_candidate.metadata["copilot_spec"] = spec.model_dump()
            selected_candidate.metadata["evaluation_summary"] = eval_summary.model_dump()
            selected_candidate.metadata["all_candidates"] = [
                {
                    "candidate_id": c.metadata.get("candidate_id"),
                    "strategy_name": c.metadata.get("strategy_name"),
                    "dashboard_title": c.dashboard_title,
                    "page_count": len(c.pages),
                    "visual_count": sum(len(p.visuals) for p in c.pages),
                    "total_score": next((e.total_score for e in eval_summary.evaluations if e.candidate_id == c.metadata.get("candidate_id")), 0.0),
                }
                for c in candidates
            ]
            selected_candidate.metadata["critic_status"] = critic_report.status
            if optimizer_report is not None:
                selected_candidate.metadata["optimizer_report"] = {
                    "run_id": optimizer_report.run_id,
                    "improved": optimizer_report.improved,
                    "baseline_score": optimizer_report.baseline_score,
                    "optimized_score": optimizer_report.optimized_score,
                    "operations_count": len(optimizer_report.operations),
                    "findings_count": len(optimizer_report.findings),
                }
            selected_candidate.metadata["planning_latency_seconds"] = round(time.time() - start_time, 3)

            return selected_candidate

        except Exception as exc:
            logger.warning("AI planning failed; executing controlled deterministic fallback: %s", exc)
            fallback = create_deterministic_plan(bundle, user_context if isinstance(user_context, dict) else None)
            fallback.planning_mode = "deterministic_fallback"
            fallback.warnings.append(f"AI planning failed ({exc}); controlled fallback to deterministic plan.")
            fallback.metadata["fallback_reason"] = str(exc)
            return fallback


    def plan_candidates(
        self,
        bundle: Any,
        user_context: dict[str, Any] | str | None = None,
        retrieved_knowledge: list[RetrievedChunk] | None = None,
    ) -> list[DashboardPlan]:
        """Direct access to candidate plan generation for testing and auditing."""
        from src.agents.dashboard.dashboard_copilot import DashboardCopilot
        copilot = DashboardCopilot(self.provider)
        spec = copilot.interpret(user_context, bundle)
        return generate_candidate_plans(bundle, spec, retrieved_knowledge, self.provider, self.config.candidate_count)

