from __future__ import annotations

import pytest
from src.agents.dashboard.config import GeneratorConfig, OperatingMode
from src.agents.dashboard.dashboard_critic import DashboardCritic
from src.agents.dashboard.dashboard_planner import (
    DashboardPlanner,
    MockLLMProvider,
    revise_plan,
)


def test_bounded_critic_revision_loop(sample_bundle):
    cfg = GeneratorConfig(mode=OperatingMode.AI_ASSISTED, max_revisions=2)
    planner = DashboardPlanner(cfg, provider=MockLLMProvider())
    plan = planner.plan(sample_bundle, user_context="Create an executive trade dashboard")

    assert plan.planning_mode == "ai_assisted"
    assert "copilot_spec" in plan.metadata
    assert "evaluation_summary" in plan.metadata
    assert "all_candidates" in plan.metadata
    assert plan.metadata.get("critic_status") in ("ACCEPTED", "ACCEPTED_WITH_WARNINGS")

    # Verify that revisions are bounded
    revisions_count = plan.metadata.get("revisions_applied", 0)
    assert revisions_count <= cfg.max_revisions


def test_critic_revision_inserts_governance_page(sample_bundle):
    # Candidate without DataQualityTraceability page
    from src.agents.dashboard.dashboard_planner import build_analytical_candidate
    from src.agents.dashboard.dashboard_copilot import DashboardCopilot

    copilot = DashboardCopilot()
    spec = copilot.interpret("Trade analysis", sample_bundle)
    candidate = build_analytical_candidate(sample_bundle, spec)

    # Remove DataQualityTraceability page
    candidate.pages = [p for p in candidate.pages if p.name != "DataQualityTraceability"]
    assert not any(p.name == "DataQualityTraceability" for p in candidate.pages)

    critic = DashboardCritic()
    report = critic.evaluate(candidate, sample_bundle)
    assert any("DataQualityTraceability" in s for s in report.revision_suggestions)

    # Revise plan
    revised = revise_plan(candidate, report, sample_bundle, spec)
    assert any(p.name == "DataQualityTraceability" for p in revised.pages)
    assert any("DataQualityTraceability" in w for w in revised.warnings)
    assert revised.metadata.get("revisions_applied") == 1


def test_deterministic_mode_bypasses_llm(sample_bundle):
    cfg = GeneratorConfig(mode=OperatingMode.DETERMINISTIC)
    planner = DashboardPlanner(cfg)
    plan = planner.plan(sample_bundle)
    assert plan.planning_mode == "deterministic"
    assert len(plan.pages) == 6
    assert len(plan.warnings) == 0

