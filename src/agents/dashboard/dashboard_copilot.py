from __future__ import annotations

import json
import logging
import re
from typing import Any
from pydantic import BaseModel, Field

from src.agents.dashboard.dashboard_planner import LLMProvider, MockLLMProvider

logger = logging.getLogger("bi_flow.dashboard_copilot")


class DashboardSpecification(BaseModel):
    """Structured business and visualization specification extracted from user requests."""
    business_objective: str = Field(description="Core business purpose and objective of the dashboard")
    target_audience: str = Field(default="Executive Leadership", description="Target user persona")
    business_questions: list[str] = Field(default_factory=list, description="Specific business questions the dashboard must answer")
    required_kpi_ids: list[str] = Field(default_factory=list, description="KPI IDs from the upstream catalog required in the plan")
    required_insight_ids: list[str] = Field(default_factory=list, description="Upstream finding/insight IDs required in the plan")
    preferred_pages: list[str] = Field(default_factory=list, description="Requested page titles or themes")
    preferred_visual_types: list[str] = Field(default_factory=list, description="Requested chart/visual types")
    constraints: list[str] = Field(default_factory=list, description="Contractual or visual design constraints")
    assumptions: list[str] = Field(default_factory=list, description="Inferred assumptions where the user was underspecified")
    unresolved_questions: list[str] = Field(default_factory=list, description="Clarifications or unsupported items identified")


class DashboardCopilot:
    """Conversational Copilot that converts natural language requests into validated specifications."""

    _SPEC_ONLY_KEYS = ("business_questions", "required_kpi_ids", "preferred_pages", "preferred_visual_types")

    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider

    def interpret(self, user_request: str | dict[str, Any] | None, bundle: Any) -> DashboardSpecification:
        """Parse natural language or structured input, resolving references strictly against upstream bundle."""
        known_kpi_ids = {kpi.id: kpi for kpi in bundle.kpis if getattr(kpi, "supported", True)}
        known_insight_ids = set()
        for f in bundle.findings.get("findings", []):
            if isinstance(f, dict) and "id" in f:
                known_insight_ids.add(f["id"])

        if isinstance(user_request, dict):
            return self._validate_and_sanitize(user_request, known_kpi_ids, known_insight_ids)

        text = (user_request or "").strip()

        # Try LLM extraction if provider is available
        if self.provider is not None:
            prompt_text = text if text else "Create an executive dashboard covering headline trade indicators, partner distributions, product analysis, and analyst findings."
            spec_dict = self._llm_extract(
                prompt_text,
                known_kpi_ids,
                known_insight_ids,
                bundle,
            )
            # A response without any specification field (e.g. the offline mock, which returns a
            # full dashboard plan) carries nothing about the request; keep the user's intent instead.
            if not any(key in spec_dict for key in self._SPEC_ONLY_KEYS):
                if not text:
                    return self._build_default_specification(known_kpi_ids, known_insight_ids)
                return self._deterministic_extract(text, known_kpi_ids, known_insight_ids, bundle)
            return self._validate_and_sanitize(spec_dict, known_kpi_ids, known_insight_ids)

        if not text:
            return self._build_default_specification(known_kpi_ids, known_insight_ids)

        # Deterministic extraction via keyword and semantic rule matching
        return self._deterministic_extract(text, known_kpi_ids, known_insight_ids, bundle)

    def _llm_extract(
        self,
        text: str,
        known_kpi_ids: dict[str, Any],
        known_insight_ids: set[str],
        bundle: Any,
    ) -> dict[str, Any]:
        system_prompt = (
            "You are an expert BI Analyst Copilot.\n"
            "Analyze the user's dashboard request and produce a strict JSON matching DashboardSpecification.\n"
            "Rules:\n"
            "1. ONLY use KPI IDs from available_kpis. Do NOT invent new KPI IDs.\n"
            "2. If the user asks for a KPI not in available_kpis, list it under unresolved_questions.\n"
            "3. Enforce valid visual types: card, lineChart, clusteredBarChart, tableEx, slicer.\n"
            "4. Return pure JSON without markdown fences.\n"
            "5. The JSON object MUST use exactly these keys (no others): business_objective (string), "
            "target_audience (string), business_questions (list of strings), required_kpi_ids (list of KPI ids "
            "from available_kpis), required_insight_ids (list of ids from available_insight_ids), "
            "preferred_pages (list of strings), preferred_visual_types (list chosen from the approved visual types), "
            "constraints (list of strings), assumptions (list of strings), unresolved_questions (list of strings).\n"
            "6. Do NOT describe pages, visuals or layouts: only the specification."
        )
        prompt_payload = {
            "user_request": text,
            "available_kpis": [{"id": k.id, "name": k.name, "definition": k.definition} for k in known_kpi_ids.values()],
            "available_insight_ids": list(known_insight_ids),
            "available_dimensions": ["Date", "Country", "Product", "Direction"],
        }
        raw_res = self.provider.generate(
            f"Extract dashboard specification for:\n{json.dumps(prompt_payload, indent=2)}",
            system_prompt=system_prompt,
        )
        clean_json = re.sub(r"^```(?:json)?|```$", "", raw_res.strip()).strip()
        return json.loads(clean_json)

    def _deterministic_extract(
        self,
        text: str,
        known_kpi_ids: dict[str, Any],
        known_insight_ids: set[str],
        bundle: Any,
    ) -> DashboardSpecification:
        lowered = text.lower()

        # 1. Target audience
        audience = "Executive Leadership"
        if "export manager" in lowered or "export director" in lowered:
            audience = "Export Managers and International Trade Directors"
        elif "policy" in lowered or "regulator" in lowered:
            audience = "Trade Policy Regulators and Analysts"
        elif "operations" in lowered or "logistics" in lowered:
            audience = "Trade Operations & Logistics Managers"

        # 2. Objective
        objective = "Monitor headline trade indicators, partner distributions, and data quality."
        if "export" in lowered and "import" not in lowered:
            objective = "Analyze outbound export performance, partner country dynamics, and commodity concentrations."
        elif "bilateral" in lowered or "partner" in lowered:
            objective = "Evaluate bilateral trade partner balances, product flows, and operational audit evidence."

        # 3. Questions
        questions = []
        if "kpi" in lowered or "overview" in lowered or "headline" in lowered or "important" in lowered:
            questions.append("What are our current headline trade volumes and net balance?")
        if "country" in lowered or "countries" in lowered or "partner" in lowered or "partners" in lowered:
            questions.append("Which partner countries represent our largest trade exposures and surpluses?")
        if "product" in lowered or "products" in lowered or "commodity" in lowered or "commodities" in lowered:
            questions.append("Which product categories drive the highest trade volumes and require investigation?")
        if "finding" in lowered or "findings" in lowered or "recommendation" in lowered or "recommendations" in lowered or "insight" in lowered or "insights" in lowered:
            questions.append("What specific findings and strategic recommendations were identified by analysts?")
        if not questions:
            questions = [
                "What is the net trade balance across reporting periods?",
                "How are trade flows distributed across partner countries and product categories?",
            ]

        # 4. Map requested KPIs strictly to known catalog
        required_kpis = []
        unresolved = []
        if ("export" in lowered or "exports" in lowered) and "total_exports" in known_kpi_ids:
            required_kpis.append("total_exports")
        if ("import" in lowered or "imports" in lowered) and "total_imports" in known_kpi_ids:
            required_kpis.append("total_imports")
        if "balance" in lowered and "trade_balance" in known_kpi_ids:
            required_kpis.append("trade_balance")
        if "count" in lowered or "volume" in lowered or "transaction" in lowered or "transactions" in lowered:
            if "transaction_count" in known_kpi_ids:
                required_kpis.append("transaction_count")

        # If general request without specific mentions, include all known catalog KPIs
        if not required_kpis:
            required_kpis = list(known_kpi_ids.keys())

        # Check for unavailable KPI concepts mentioned by user
        unsupported_terms = {
            "profit": "profit_margin",
            "revenue": "gross_revenue",
            "margin": "net_margin",
            "cagr": "growth_rate_cagr",
            "forecast": "future_export_forecast",
            "churn": "customer_churn",
        }
        for term, kpi_name in unsupported_terms.items():
            # "profit margin" is one metric, not a profit metric plus a separate net margin.
            if term == "margin" and "profit margin" in lowered:
                continue
            if term in lowered:
                unresolved.append(f"Requested metric '{kpi_name}' is not defined in the upstream KPI catalog.")

        # 5. Map requested insights
        required_insights = list(known_insight_ids)

        # 6. Preferred visual types & pages
        pref_visuals = []
        if "card" in lowered or "cards" in lowered or "kpi" in lowered or "kpis" in lowered:
            pref_visuals.append("card")
        if "trend" in lowered or "trends" in lowered or "time" in lowered or "line" in lowered:
            pref_visuals.append("lineChart")
        if "bar" in lowered or "bars" in lowered or "compare" in lowered or "rank" in lowered or "country" in lowered or "countries" in lowered:
            pref_visuals.append("clusteredBarChart")
        if "table" in lowered or "tables" in lowered or "detail" in lowered or "finding" in lowered or "findings" in lowered or "explanation" in lowered or "explanations" in lowered:
            pref_visuals.append("tableEx")
        if "filter" in lowered or "filters" in lowered or "slicer" in lowered or "slicers" in lowered:
            pref_visuals.append("slicer")

        pref_pages = []
        if "executive" in lowered or "overview" in lowered:
            pref_pages.append("Executive Overview")
        if "country" in lowered or "countries" in lowered or "partner" in lowered or "partners" in lowered:
            pref_pages.append("Country Analysis")
        if "product" in lowered or "products" in lowered:
            pref_pages.append("Product Analysis")
        if "finding" in lowered or "findings" in lowered or "insight" in lowered or "insights" in lowered or "explanation" in lowered or "explanations" in lowered:
            pref_pages.append("Business Insights")

        assumptions = [
            "Assumes currency conversion policy is not needed (single currency grain).",
            "Assumes standard star-schema relationships between FactTrade and DimDate/DimCountry/DimProduct.",
        ]

        constraints = [
            "Maximum 6 dashboard pages.",
            "Maximum 6 visual containers per page.",
            "Only approved visual types: card, lineChart, clusteredBarChart, tableEx, slicer.",
        ]

        return DashboardSpecification(
            business_objective=objective,
            target_audience=audience,
            business_questions=questions,
            required_kpi_ids=required_kpis,
            required_insight_ids=required_insights,
            preferred_pages=pref_pages,
            preferred_visual_types=pref_visuals or ["card", "lineChart", "clusteredBarChart", "tableEx"],
            constraints=constraints,
            assumptions=assumptions,
            unresolved_questions=unresolved,
        )

    def _validate_and_sanitize(
        self,
        raw_dict: dict[str, Any],
        known_kpi_ids: dict[str, Any],
        known_insight_ids: set[str],
    ) -> DashboardSpecification:
        # Validate that all required_kpi_ids actually exist
        raw_kpis = raw_dict.get("required_kpi_ids", [])
        valid_kpis = []
        unresolved = list(raw_dict.get("unresolved_questions", []))

        for k in raw_kpis:
            if k in known_kpi_ids:
                valid_kpis.append(k)
            else:
                unresolved.append(f"Requested KPI '{k}' is unavailable in the data catalog.")

        # Ensure at least headline KPIs are included if none matched
        if not valid_kpis:
            valid_kpis = list(known_kpi_ids.keys())

        raw_insights = raw_dict.get("required_insight_ids", [])
        valid_insights = [i for i in raw_insights if i in known_insight_ids]

        return DashboardSpecification(
            business_objective=raw_dict.get("business_objective", "Standard trade performance intelligence."),
            target_audience=raw_dict.get("target_audience", "Executive Leadership"),
            business_questions=raw_dict.get("business_questions", ["What is the trade performance?"]),
            required_kpi_ids=valid_kpis,
            required_insight_ids=valid_insights or list(known_insight_ids),
            preferred_pages=raw_dict.get("preferred_pages", []),
            preferred_visual_types=raw_dict.get("preferred_visual_types", ["card", "lineChart", "clusteredBarChart", "tableEx"]),
            constraints=raw_dict.get("constraints", ["Max 6 pages", "Max 6 visuals per page"]),
            assumptions=raw_dict.get("assumptions", ["Single source currency"]),
            unresolved_questions=unresolved,
        )

    def _build_default_specification(
        self,
        known_kpi_ids: dict[str, Any],
        known_insight_ids: set[str],
    ) -> DashboardSpecification:
        return DashboardSpecification(
            business_objective="Monitor trade performance, bilateral country balances, and data governance.",
            target_audience="Executive Leadership & Trade Analysts",
            business_questions=[
                "What are the headline trade KPIs across reporting periods?",
                "How are trade flows distributed across partner countries and product categories?",
                "What validated findings and data quality checks require attention?",
            ],
            required_kpi_ids=list(known_kpi_ids.keys()),
            required_insight_ids=list(known_insight_ids),
            preferred_pages=["Executive Overview", "Trade Performance", "Country Analysis", "Product Analysis", "Business Insights", "Data Quality & Traceability"],
            preferred_visual_types=["card", "lineChart", "clusteredBarChart", "tableEx", "slicer"],
            constraints=["Maximum 6 pages", "Maximum 6 visuals per page"],
            assumptions=["Single currency assumption", "Standard star-schema dimensions"],
            unresolved_questions=[],
        )

