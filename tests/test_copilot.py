from __future__ import annotations

import pytest
from src.agents.dashboard.dashboard_copilot import DashboardCopilot, DashboardSpecification
from src.agents.dashboard.dashboard_planner import MockLLMProvider


def test_copilot_deterministic_natural_language(sample_bundle):
    copilot = DashboardCopilot()
    prompt = (
        "Create an executive dashboard for export managers. "
        "Highlight important trade KPIs, compare countries, identify products that deserve investigation "
        "based on existing findings, and provide clear explanations."
    )
    spec = copilot.interpret(prompt, sample_bundle)

    assert isinstance(spec, DashboardSpecification)
    assert "Export Managers" in spec.target_audience
    assert len(spec.business_questions) >= 3
    # Check that required KPIs strictly exist in sample_bundle
    known_kpi_ids = {k.id for k in sample_bundle.kpis}
    for kpi_id in spec.required_kpi_ids:
        assert kpi_id in known_kpi_ids
    assert "total_exports" in spec.required_kpi_ids

    # Check preferred pages and visual types
    assert any("Executive" in p for p in spec.preferred_pages)
    assert any("Country" in p for p in spec.preferred_pages)
    assert "clusteredBarChart" in spec.preferred_visual_types
    assert "card" in spec.preferred_visual_types


def test_copilot_identifies_unsupported_kpis(sample_bundle):
    copilot = DashboardCopilot()
    prompt = "I need to see gross revenue, profit margin, future export forecast, and customer churn."
    spec = copilot.interpret(prompt, sample_bundle)

    # Must NOT pretend unsupported KPIs exist
    assert len(spec.unresolved_questions) > 0
    unresolved_text = " ".join(spec.unresolved_questions)
    assert "profit_margin" in unresolved_text
    assert "gross_revenue" in unresolved_text
    assert "future_export_forecast" in unresolved_text
    assert "customer_churn" in unresolved_text

    # Valid required KPIs must only be catalog KPIs
    known_kpi_ids = {k.id for k in sample_bundle.kpis}
    for kpi_id in spec.required_kpi_ids:
        assert kpi_id in known_kpi_ids


def test_copilot_structured_input_mode(sample_bundle):
    copilot = DashboardCopilot()
    structured = {
        "business_objective": "Audit bilateral trade compliance and tariff exposure.",
        "target_audience": "Customs Compliance Directorate",
        "business_questions": ["What is total export volume to non-EU nations?"],
        "required_kpi_ids": ["total_exports", "non_existent_fake_kpi"],
        "required_insight_ids": ["f_001", "non_existent_insight"],
        "preferred_pages": ["Compliance Audit"],
        "preferred_visual_types": ["tableEx", "card"],
        "constraints": ["Strict table pagination"],
    }
    spec = copilot.interpret(structured, sample_bundle)

    assert spec.target_audience == "Customs Compliance Directorate"
    assert "total_exports" in spec.required_kpi_ids
    assert "non_existent_fake_kpi" not in spec.required_kpi_ids
    assert any("non_existent_fake_kpi" in q for q in spec.unresolved_questions)


def test_copilot_default_fallback_on_empty(sample_bundle):
    copilot = DashboardCopilot()
    spec = copilot.interpret(None, sample_bundle)
    assert isinstance(spec, DashboardSpecification)
    assert spec.business_objective != ""
    assert len(spec.required_kpi_ids) > 0
    assert len(spec.preferred_pages) == 6


def test_copilot_llm_provider_mode(sample_bundle):
    provider = MockLLMProvider()
    copilot = DashboardCopilot(provider=provider)
    spec = copilot.interpret("Build an export overview", sample_bundle)
    assert isinstance(spec, DashboardSpecification)
    assert provider.call_count == 1


def test_copilot_mock_provider_keeps_user_intent(sample_bundle):
    # The offline mock returns a dashboard plan, not a specification: the request must still drive the spec.
    copilot = DashboardCopilot(provider=MockLLMProvider())
    spec = copilot.interpret("Dashboard for export managers comparing partner countries", sample_bundle)
    assert "Export Managers" in spec.target_audience
    assert "Country Analysis" in spec.preferred_pages
    assert any("countries" in q for q in spec.business_questions)


@pytest.mark.parametrize(
    ("request_text", "expected_page"),
    [
        ("Executive dashboard comparing partner countries and analyst findings", "CountryAnalysis"),
        ("Which products and commodities drive exports?", "ProductAnalysis"),
    ],
)
def test_ai_mock_mode_plans_requested_page(sample_bundle, tmp_path, request_text, expected_page):
    from src.agents.dashboard.config import GeneratorConfig, OperatingMode
    from src.agents.dashboard.dashboard_planner import DashboardPlanner

    cfg = GeneratorConfig(mode=OperatingMode.AI_ASSISTED, llm_provider="mock", feedback_path=tmp_path / "fb.jsonl")
    plan = DashboardPlanner(cfg).plan(sample_bundle, request_text)
    assert plan.planning_mode == "ai_assisted"
    assert expected_page in {p.name for p in plan.pages}

