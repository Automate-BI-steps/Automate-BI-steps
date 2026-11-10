from __future__ import annotations

import pytest
from src.agents.dashboard.dashboard_copilot import DashboardCopilot
from src.agents.dashboard.dashboard_critic import DashboardCritic
from src.agents.dashboard.dashboard_planner import (
    DashboardPlan,
    DashboardPlanner,
    generate_candidate_plans,
)


def test_candidate_generation_diversity(sample_bundle):
    copilot = DashboardCopilot()
    spec = copilot.interpret("Executive trade dashboard with country comparison and analyst findings", sample_bundle)

    candidates = generate_candidate_plans(sample_bundle, spec, candidate_count=3)
    assert len(candidates) == 3

    # Unique candidate IDs
    candidate_ids = [c.metadata.get("candidate_id") for c in candidates]
    assert len(set(candidate_ids)) == 3
    assert "candidate_executive" in candidate_ids
    assert "candidate_analytical" in candidate_ids
    assert "candidate_decision" in candidate_ids

    # Meaningful structural diversity:
    # 1. Executive candidate starts with ExecutiveOverview and contains findings page
    exec_c = next(c for c in candidates if c.metadata.get("candidate_id") == "candidate_executive")
    assert exec_c.pages[0].name == "ExecutiveOverview"
    assert any(p.name == "BusinessInsights" for p in exec_c.pages)

    # 2. Analytical candidate emphasizes CountryAnalysis and ProductAnalysis
    analyt_c = next(c for c in candidates if c.metadata.get("candidate_id") == "candidate_analytical")
    page_names = [p.name for p in analyt_c.pages]
    assert "CountryAnalysis" in page_names
    assert "ProductAnalysis" in page_names
    # Analytical candidate has more visuals for granular breakdown
    analyt_visual_count = sum(len(p.visuals) for p in analyt_c.pages)
    assert analyt_visual_count >= 8

    # 3. Decision candidate prioritizes BusinessInsights as the first page!
    decis_c = next(c for c in candidates if c.metadata.get("candidate_id") == "candidate_decision")
    assert decis_c.pages[0].name == "BusinessInsights"


def test_all_candidates_pass_deterministic_critic(sample_bundle):
    copilot = DashboardCopilot()
    spec = copilot.interpret("Comprehensive dashboard", sample_bundle)
    candidates = generate_candidate_plans(sample_bundle, spec, candidate_count=3)
    critic = DashboardCritic()

    for candidate in candidates:
        assert isinstance(candidate, DashboardPlan)
        # Check compiler limits
        assert len(candidate.pages) <= 6
        for page in candidate.pages:
            assert len(page.visuals) <= 6

        # Check deterministic critic evaluation
        report = critic.evaluate(candidate, sample_bundle)
        assert report.passed_deterministic is True
        assert len(report.errors) == 0


def test_candidates_use_real_upstream_references(sample_bundle):
    copilot = DashboardCopilot()
    spec = copilot.interpret("Trade report", sample_bundle)
    candidates = generate_candidate_plans(sample_bundle, spec, candidate_count=3)

    known_kpis = {k.id for k in sample_bundle.kpis}
    known_tables = {"FactTrade", "DimDate", "DimCountry", "DimProduct", "BusinessInsights", "DataQuality"}

    for candidate in candidates:
        for page in candidate.pages:
            for visual in page.visuals:
                if visual.kpi_reference:
                    assert visual.kpi_reference in known_kpis
                for role, projections in visual.roles.items():
                    for proj in projections:
                        assert proj["table"] in known_tables

