from __future__ import annotations

from pathlib import Path
import pytest

from src.agents.dashboard.config import GeneratorConfig, OperatingMode
from src.agents.dashboard.dashboard_critic import DashboardCritic
from src.agents.dashboard.dashboard_planner import (
    DashboardPlan,
    DashboardPlanner,
    MockLLMProvider,
    PagePlan,
    VisualPlan,
    create_deterministic_plan,
)
from src.agents.dashboard.knowledge_agent import KnowledgeAgent
from tests.conftest import bundle_for, frame


def test_create_deterministic_plan(sample_bundle):
    plan = create_deterministic_plan(sample_bundle)
    assert plan.planning_mode == "deterministic"
    assert len(plan.pages) == 6
    assert plan.pages[0].name == "ExecutiveOverview"
    assert plan.pages[1].name == "TradePerformance"
    assert plan.pages[2].name == "CountryAnalysis"
    assert plan.pages[3].name == "ProductAnalysis"
    assert plan.pages[4].name == "BusinessInsights"
    assert plan.pages[5].name == "DataQualityTraceability"
    assert len(plan.pages[0].visuals) == 5  # 4 KPI cards + 1 trend chart


def test_ai_assisted_mock_planner(sample_bundle, mock_ai_config):
    planner = DashboardPlanner(mock_ai_config)
    plan = planner.plan(sample_bundle)
    assert plan.planning_mode == "ai_assisted"
    assert 4 <= len(plan.pages) <= 6
    critic = DashboardCritic()
    report = critic.evaluate(plan, sample_bundle)
    assert report.passed_deterministic is True
    assert report.status in ("ACCEPTED", "ACCEPTED_WITH_WARNINGS")


def test_critic_rejects_unknown_table(sample_bundle):
    plan = create_deterministic_plan(sample_bundle)
    # Inject unknown table in a visual projection
    plan.pages[0].visuals[0].roles["Values"] = [{"kind": "Column", "table": "NonExistentTable", "name": "Value"}]
    critic = DashboardCritic()
    report = critic.evaluate(plan, sample_bundle)
    assert report.passed_deterministic is False
    assert report.status == "REJECTED"
    assert any("non-existent column 'NonExistentTable.Value'" in err for err in report.errors)


def test_critic_rejects_unknown_column(sample_bundle):
    plan = create_deterministic_plan(sample_bundle)
    plan.pages[0].visuals[0].roles["Values"] = [{"kind": "Column", "table": "FactTrade", "name": "NonExistentColumn"}]
    critic = DashboardCritic()
    report = critic.evaluate(plan, sample_bundle)
    assert report.passed_deterministic is False
    assert any("non-existent column 'FactTrade.NonExistentColumn'" in err for err in report.errors)


def test_critic_rejects_unknown_measure(sample_bundle):
    plan = create_deterministic_plan(sample_bundle)
    plan.pages[0].visuals[0].roles["Values"] = [{"kind": "Measure", "table": "FactTrade", "name": "NonExistentMeasure"}]
    critic = DashboardCritic()
    report = critic.evaluate(plan, sample_bundle)
    assert report.passed_deterministic is False
    assert any("unknown Measure 'NonExistentMeasure'" in err for err in report.errors)


def test_critic_rejects_unknown_kpi(sample_bundle):
    plan = create_deterministic_plan(sample_bundle)
    plan.pages[0].visuals[0].kpi_reference = "unknown_kpi_id"
    critic = DashboardCritic()
    report = critic.evaluate(plan, sample_bundle)
    assert report.passed_deterministic is False
    assert any("references unknown KPI ID 'unknown_kpi_id'" in err for err in report.errors)


def test_critic_rejects_duplicate_visual_id(sample_bundle):
    plan = create_deterministic_plan(sample_bundle)
    # Duplicate ID across two visuals
    plan.pages[1].visuals[0].id = plan.pages[0].visuals[0].id
    critic = DashboardCritic()
    report = critic.evaluate(plan, sample_bundle)
    assert report.passed_deterministic is False
    assert any("Duplicate visual identifier" in err for err in report.errors)


def test_planner_fallback_on_invalid_json(sample_bundle):
    cfg = GeneratorConfig(mode=OperatingMode.AI_ASSISTED)
    bad_provider = MockLLMProvider(response_override="INVALID JSON {not valid}")
    planner = DashboardPlanner(cfg, provider=bad_provider)
    plan = planner.plan(sample_bundle)
    assert plan.planning_mode == "deterministic_fallback"
    assert any("AI planning failed" in w for w in plan.warnings)


def test_planner_fallback_on_network_exception(sample_bundle):
    cfg = GeneratorConfig(mode=OperatingMode.AI_ASSISTED)
    err_provider = MockLLMProvider(raise_error=ConnectionError("Host unreachable"))
    planner = DashboardPlanner(cfg, provider=err_provider)
    plan = planner.plan(sample_bundle)
    assert plan.planning_mode == "deterministic_fallback"


def test_knowledge_agent_retrieval():
    knowledge_dir = Path(__file__).resolve().parent.parent / "resources" / "knowledge"
    assert knowledge_dir.exists()
    agent = KnowledgeAgent(knowledge_dir)
    assert len(agent.chunks) > 0
    results = agent.retrieve("trade balance", top_k=2)
    assert len(results) > 0
    assert results[0].score > 0
    assert any("glossary" in r.source or "rules" in r.source or "guidelines" in r.source for r in results)

