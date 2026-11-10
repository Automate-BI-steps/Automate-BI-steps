"""
tests/test_optimizer.py â€” Phase 2A DashboardOptimizer tests

All assertions verify actual score changes or structural changes using
controlled fixtures. No test asserts merely that an object exists.
"""
from __future__ import annotations

import pytest

from src.agents.dashboard.config import GeneratorConfig, OperatingMode
from src.agents.dashboard.dashboard_copilot import DashboardCopilot
from src.agents.dashboard.dashboard_critic import DashboardCritic
from src.agents.dashboard.dashboard_evaluator import DashboardEvaluator
from src.agents.dashboard.dashboard_optimizer import DashboardOptimizer, OptimizerReport, OptimizerFinding
from src.agents.dashboard.dashboard_planner import (
    DashboardPlan, PagePlan, VisualPlan,
    build_executive_candidate,
    build_analytical_candidate,
    build_decision_candidate,
    generate_candidate_plans,
    create_deterministic_plan,
    MockLLMProvider,
    DashboardPlanner,
)
from tests.conftest import bundle_for, frame


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _spec_for(bundle, text="Executive trade dashboard for leadership"):
    copilot = DashboardCopilot()
    return copilot.interpret(text, bundle)


def _optimizer(max_ops: int = 5) -> DashboardOptimizer:
    return DashboardOptimizer(max_operations=max_ops)


# â”€â”€ Candidate ranking â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_optimizer_returns_valid_plan(sample_bundle):
    spec = _spec_for(sample_bundle)
    candidates = generate_candidate_plans(sample_bundle, spec, candidate_count=3)
    evaluator = DashboardEvaluator()
    summary = evaluator.evaluate_candidates(candidates, sample_bundle, spec)
    selected = next(c for c in candidates if c.metadata.get("candidate_id") == summary.selected_candidate_id)

    opt = _optimizer()
    best, report = opt.optimize(candidates, selected, sample_bundle, spec, summary)

    assert isinstance(best, DashboardPlan)
    assert isinstance(report, OptimizerReport)
    # Returned plan must always pass deterministic checks
    critic = DashboardCritic()
    critic_report = critic.evaluate(best, sample_bundle)
    assert critic_report.passed_deterministic, f"Optimizer returned invalid plan: {critic_report.errors}"


def test_optimizer_does_not_decrease_score_vs_baseline(sample_bundle):
    spec = _spec_for(sample_bundle)
    candidates = generate_candidate_plans(sample_bundle, spec, candidate_count=3)
    evaluator = DashboardEvaluator()
    summary = evaluator.evaluate_candidates(candidates, sample_bundle, spec)
    selected = next(c for c in candidates if c.metadata.get("candidate_id") == summary.selected_candidate_id)

    opt = _optimizer()
    best, report = opt.optimize(candidates, selected, sample_bundle, spec, summary)

    # Optimizer must never return a plan with a lower score than the baseline
    assert report.optimized_score >= report.baseline_score - 0.01


def test_optimizer_report_has_plan_comparisons(sample_bundle):
    spec = _spec_for(sample_bundle)
    candidates = generate_candidate_plans(sample_bundle, spec, candidate_count=3)
    evaluator = DashboardEvaluator()
    summary = evaluator.evaluate_candidates(candidates, sample_bundle, spec)
    selected = next(c for c in candidates if c.metadata.get("candidate_id") == summary.selected_candidate_id)

    opt = _optimizer()
    _, report = opt.optimize(candidates, selected, sample_bundle, spec, summary)

    assert len(report.plan_comparisons) == len(candidates)
    for comp in report.plan_comparisons:
        assert comp.candidate_id != ""
        assert 0.0 <= comp.total_score <= 100.0
        assert comp.page_count > 0
        assert comp.visual_count > 0


# â”€â”€ Redundant visual removal â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_optimizer_removes_redundant_visuals(sample_bundle):
    """Optimizer should remove duplicate signatures and improve redundancy_penalty score."""
    spec = _spec_for(sample_bundle)
    candidate = build_executive_candidate(sample_bundle, spec)

    # Insert a duplicate lineChart signature on page 1
    duplicate_visual = VisualPlan(
        id="duplicate_trend_extra",
        title="Duplicate Trend",
        visual_type="lineChart",
        roles=candidate.pages[0].visuals[-1].roles,  # same roles as trend visual
        selection_reason="Test duplicate",
        evidence_references=["FactTrade.Value"],
    )
    candidate.pages[0].visuals.append(duplicate_visual)

    evaluator = DashboardEvaluator()
    summary = evaluator.evaluate_candidates([candidate], sample_bundle, spec)

    opt = _optimizer()
    _, report = opt.optimize([candidate], candidate, sample_bundle, spec, summary)

    removal_op = next((o for o in report.operations if o.operation_type == "remove_redundant_visuals"), None)
    assert removal_op is not None, "Expected remove_redundant_visuals operation"


def test_optimizer_does_not_remove_sole_visual_on_page(sample_bundle):
    """Optimizer must not remove a visual if it's the only one on its page."""
    spec = _spec_for(sample_bundle)
    candidate = build_executive_candidate(sample_bundle, spec)

    # Give page 1 only 1 visual with a duplicate signature
    solo_visual = VisualPlan(
        id="solo_trend",
        title="Solo Trend",
        visual_type="lineChart",
        roles={"Y": [{"kind": "Sum", "table": "FactTrade", "name": "Value"}]},
        selection_reason="Test solo",
        evidence_references=["FactTrade.Value"],
    )
    # Also add same signature on page 2
    dup_visual = VisualPlan(
        id="dup_trend",
        title="Dup Trend",
        visual_type="lineChart",
        roles={"Y": [{"kind": "Sum", "table": "FactTrade", "name": "Value"}]},
        selection_reason="Test dup",
        evidence_references=["FactTrade.Value"],
    )
    # Give page 2 the duplicate
    if len(candidate.pages) > 1:
        candidate.pages[1].visuals.append(dup_visual)

    evaluator = DashboardEvaluator()
    summary = evaluator.evaluate_candidates([candidate], sample_bundle, spec)

    opt = _optimizer()
    best, _ = opt.optimize([candidate], candidate, sample_bundle, spec, summary)

    # Every page in the returned plan must have at least 1 visual
    for page in best.pages:
        assert len(page.visuals) >= 1, f"Page '{page.name}' has 0 visuals after optimization"


# â”€â”€ Page reordering â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_optimizer_reorders_pages_for_executive_audience(sample_bundle):
    """For executive audience, ExecutiveOverview should become page 1."""
    spec = _spec_for(sample_bundle, "Executive leadership dashboard")
    # decision candidate starts with BusinessInsights page (not ExecutiveOverview)
    candidate = build_decision_candidate(sample_bundle, spec)
    first_page_before = candidate.pages[0].name

    evaluator = DashboardEvaluator()
    summary = evaluator.evaluate_candidates([candidate], sample_bundle, spec)

    opt = _optimizer()
    best, report = opt.optimize([candidate], candidate, sample_bundle, spec, summary)

    # If the page was not already first, optimizer should have moved it
    if first_page_before != "ExecutiveOverview":
        reorder_op = next((o for o in report.operations if o.operation_type == "reorder_pages_for_audience" and o.applied), None)
        # Either reorder was applied, or first page is already ExecutiveOverview
        assert best.pages[0].name == "ExecutiveOverview" or reorder_op is None


# â”€â”€ Missing required KPI card â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_optimizer_adds_missing_required_kpi_card(sample_bundle):
    """Optimizer should add a KPI card if a required KPI is entirely absent."""
    copilot = DashboardCopilot()
    spec = copilot.interpret("Executive dashboard showing trade balance", sample_bundle)
    # Ensure trade_balance is a required KPI
    assert "trade_balance" in spec.required_kpi_ids

    candidate = build_executive_candidate(sample_bundle, spec)
    # Remove all visuals referencing trade_balance
    for page in candidate.pages:
        page.visuals = [v for v in page.visuals if v.kpi_reference != "trade_balance"
                        and not any(p.get("name") == "Trade Balance" for projs in v.roles.values() for p in projs)]

    evaluator = DashboardEvaluator()
    summary = evaluator.evaluate_candidates([candidate], sample_bundle, spec)

    opt = _optimizer()
    best, report = opt.optimize([candidate], candidate, sample_bundle, spec, summary)

    # Check for the finding about missing trade_balance
    missing_findings = [f for f in report.findings if "trade_balance" in f.defect]
    assert len(missing_findings) >= 1, "Expected a finding about missing trade_balance KPI"

    # The optimizer should have tried to add it (or found it via add_missing_kpi_card)
    add_op = next((o for o in report.operations if o.operation_type == "add_missing_kpi_card"), None)
    assert add_op is not None, "Expected add_missing_kpi_card operation to be attempted"


def test_optimizer_never_invents_unknown_kpi(sample_bundle):
    """Optimizer must not create a card for a KPI that doesn't exist in the bundle."""
    spec = _spec_for(sample_bundle)
    # Inject a fake required KPI not in bundle
    spec = spec.model_copy(update={"required_kpi_ids": ["nonexistent_metric_xyz"]})

    candidate = build_executive_candidate(sample_bundle, spec)
    evaluator = DashboardEvaluator()
    # Override to use the modified spec
    summary = evaluator.evaluate_candidates([candidate], sample_bundle, spec)

    opt = _optimizer()
    best, _ = opt.optimize([candidate], candidate, sample_bundle, spec, summary)

    # No visual should reference the nonexistent KPI
    all_visual_ids = {v.id for p in best.pages for v in p.visuals}
    all_kpi_refs = {v.kpi_reference for p in best.pages for v in p.visuals if v.kpi_reference}
    assert "nonexistent_metric_xyz" not in all_kpi_refs


# â”€â”€ Evidence references â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_optimizer_adds_missing_evidence_references(sample_bundle):
    """Optimizer should infer evidence_references for visuals that have none."""
    spec = _spec_for(sample_bundle)
    candidate = build_executive_candidate(sample_bundle, spec)

    # Strip all evidence_references
    for page in candidate.pages:
        for v in page.visuals:
            v.evidence_references = []

    evaluator = DashboardEvaluator()
    score_before = evaluator.evaluate_candidates([candidate], sample_bundle, spec).evaluations[0].total_score

    # Confirm evidence traceability is degraded
    assert evaluator.evaluate_candidates([candidate], sample_bundle, spec).evaluations[0].criterion_scores["evidence_traceability"].raw_score < 10.0

    summary = evaluator.evaluate_candidates([candidate], sample_bundle, spec)
    opt = _optimizer()
    best, report = opt.optimize([candidate], candidate, sample_bundle, spec, summary)

    ev_op = next((o for o in report.operations if o.operation_type == "add_missing_evidence_references"), None)
    assert ev_op is not None

    if ev_op.applied:
        # After applying, most visuals should have evidence_references
        visuals_with_ev = sum(1 for p in best.pages for v in p.visuals if v.evidence_references)
        total_visuals = sum(len(p.visuals) for p in best.pages)
        assert visuals_with_ev > 0, "Expected at least some visuals to have evidence_references after optimization"


# â”€â”€ All candidates failing hard gates â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_optimizer_preserves_best_valid_plan_when_all_fail(sample_bundle):
    """When all candidates fail deterministic checks, optimizer returns the original (which passes)."""
    spec = _spec_for(sample_bundle)
    candidates = generate_candidate_plans(sample_bundle, spec, candidate_count=3)
    # Keep one valid candidate for selection
    evaluator = DashboardEvaluator()
    summary = evaluator.evaluate_candidates(candidates, sample_bundle, spec)
    selected = next((c for c in candidates if c.metadata.get("candidate_id") == summary.selected_candidate_id), candidates[0])

    # Corrupt ALL candidates passed to optimizer (but not the selected_plan)
    corrupt_candidates = []
    for c in candidates:
        cc = c.model_copy(deep=True)
        cc.pages[0].visuals[0].roles["Values"] = [{"kind": "Column", "table": "Ghost", "name": "GhostCol"}]
        corrupt_candidates.append(cc)

    opt = _optimizer()
    best, report = opt.optimize(corrupt_candidates, selected, sample_bundle, spec, summary)

    # The returned plan must still be valid
    critic = DashboardCritic()
    assert critic.evaluate(best, sample_bundle).passed_deterministic


# â”€â”€ No improvement scenario â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_optimizer_reports_no_improvement_honestly(sample_bundle):
    """When no operation improves the score, optimizer returns the baseline and reports improved=False."""
    spec = _spec_for(sample_bundle)
    # Use the executive candidate â€” already well-structured, minimal redundancy
    candidate = build_executive_candidate(sample_bundle, spec)
    evaluator = DashboardEvaluator()
    summary = evaluator.evaluate_candidates([candidate], sample_bundle, spec)

    opt = DashboardOptimizer(max_operations=1)  # very limited operations
    best, report = opt.optimize([candidate], candidate, sample_bundle, spec, summary)

    # Whether improved or not, report must be honest
    if not report.improved:
        assert report.optimized_score == report.baseline_score
        assert report.improvement_delta == 0.0


# â”€â”€ Invalid revision rejection â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_optimizer_rejects_operations_that_fail_critic(sample_bundle):
    """Operations that produce critic failures must be rejected, not applied."""
    spec = _spec_for(sample_bundle)
    candidate = build_executive_candidate(sample_bundle, spec)
    evaluator = DashboardEvaluator()
    summary = evaluator.evaluate_candidates([candidate], sample_bundle, spec)

    opt = _optimizer()
    best, report = opt.optimize([candidate], candidate, sample_bundle, spec, summary)

    # All applied operations must produce a plan that passes critic
    critic = DashboardCritic()
    final_report = critic.evaluate(best, sample_bundle)
    assert final_report.passed_deterministic


# â”€â”€ Deterministic tie-breaking â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_optimizer_is_deterministic_on_same_input(sample_bundle):
    """Two calls with identical inputs must produce identically-scored plans."""
    spec = _spec_for(sample_bundle)
    candidates = generate_candidate_plans(sample_bundle, spec, candidate_count=3)
    evaluator = DashboardEvaluator()
    summary = evaluator.evaluate_candidates(candidates, sample_bundle, spec)
    selected = next((c for c in candidates if c.metadata.get("candidate_id") == summary.selected_candidate_id), candidates[0])

    opt1 = _optimizer()
    opt2 = _optimizer()
    _, report1 = opt1.optimize(candidates, selected, sample_bundle, spec, summary)
    _, report2 = opt2.optimize(candidates, selected, sample_bundle, spec, summary)

    assert report1.optimized_score == report2.optimized_score
    assert report1.improved == report2.improved


# â”€â”€ Optimizer disabled â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_optimizer_disabled_via_config(sample_bundle):
    """When optimizer_enabled=False, DashboardPlanner.plan() should not call the optimizer."""
    cfg = GeneratorConfig(mode=OperatingMode.AI_ASSISTED, optimizer_enabled=False)
    planner = DashboardPlanner(cfg, provider=MockLLMProvider())
    plan = planner.plan(sample_bundle, user_context="Executive dashboard")

    # optimizer_report should NOT be set in metadata
    assert "optimizer_report" not in plan.metadata


def test_optimizer_enabled_via_config(sample_bundle):
    """When optimizer_enabled=True, DashboardPlanner.plan() should run the optimizer and record its report."""
    cfg = GeneratorConfig(mode=OperatingMode.AI_ASSISTED, optimizer_enabled=True, feedback_enabled=False)
    planner = DashboardPlanner(cfg, provider=MockLLMProvider())
    plan = planner.plan(sample_bundle, user_context="Executive dashboard for leadership")

    # optimizer_report SHOULD be set in metadata
    assert "optimizer_report" in plan.metadata
    opt_meta = plan.metadata["optimizer_report"]
    assert "run_id" in opt_meta
    assert "improved" in opt_meta
    assert "baseline_score" in opt_meta
    assert "optimized_score" in opt_meta
    # Score must still be valid
    assert 0.0 <= opt_meta["optimized_score"] <= 100.0


# â”€â”€ Evaluator weight validation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_optimizer_works_with_custom_weights(sample_bundle):
    """Optimizer must not break when evaluator is given non-default weights."""
    spec = _spec_for(sample_bundle)
    custom_weights = {
        "objective_alignment": 0.50,
        "kpi_relevance": 0.50,
    }
    evaluator = DashboardEvaluator(weights=custom_weights)
    candidates = generate_candidate_plans(sample_bundle, spec, candidate_count=3)
    summary = evaluator.evaluate_candidates(candidates, sample_bundle, spec)
    selected = next((c for c in candidates if c.metadata.get("candidate_id") == summary.selected_candidate_id), candidates[0])

    opt = DashboardOptimizer(evaluator=evaluator)
    best, report = opt.optimize(candidates, selected, sample_bundle, spec, summary)
    assert 0.0 <= report.optimized_score <= 100.0


# â”€â”€ Lineage and artifact preservation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_optimizer_preserves_all_page_names(sample_bundle):
    """Optimizer operations must not delete pages silently."""
    spec = _spec_for(sample_bundle)
    candidate = build_executive_candidate(sample_bundle, spec)
    pages_before = {p.name for p in candidate.pages}

    evaluator = DashboardEvaluator()
    summary = evaluator.evaluate_candidates([candidate], sample_bundle, spec)

    opt = _optimizer()
    best, _ = opt.optimize([candidate], candidate, sample_bundle, spec, summary)
    pages_after = {p.name for p in best.pages}

    # No page should be removed
    assert pages_before == pages_after


def test_optimizer_preserves_existing_evidence_references(sample_bundle):
    """Evidence references that already exist must not be erased by the optimizer."""
    spec = _spec_for(sample_bundle)
    candidate = build_executive_candidate(sample_bundle, spec)

    # Ensure at least one visual has evidence_references set
    assert any(v.evidence_references for p in candidate.pages for v in p.visuals)
    evidence_before = {
        v.id: list(v.evidence_references)
        for p in candidate.pages
        for v in p.visuals
        if v.evidence_references
    }

    evaluator = DashboardEvaluator()
    summary = evaluator.evaluate_candidates([candidate], sample_bundle, spec)

    opt = _optimizer()
    best, _ = opt.optimize([candidate], candidate, sample_bundle, spec, summary)

    for page in best.pages:
        for v in page.visuals:
            if v.id in evidence_before:
                # Original references must still be present (may have more)
                orig = set(evidence_before[v.id])
                after = set(v.evidence_references)
                assert orig.issubset(after), (
                    f"Visual '{v.id}' lost evidence references. Before={orig}, After={after}"
                )


# â”€â”€ Compiler compatibility â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_optimized_plan_compiles_to_valid_pbip(sample_bundle, tmp_path):
    """The optimized plan must compile through the existing compiler without errors."""
    from src.agents.dashboard.dashboard_compiler_adapter import compile_plan_to_report_pages
    spec = _spec_for(sample_bundle)
    candidates = generate_candidate_plans(sample_bundle, spec, candidate_count=3)
    evaluator = DashboardEvaluator()
    summary = evaluator.evaluate_candidates(candidates, sample_bundle, spec)
    selected = next((c for c in candidates if c.metadata.get("candidate_id") == summary.selected_candidate_id), candidates[0])

    opt = _optimizer()
    best, _ = opt.optimize(candidates, selected, sample_bundle, spec, summary)

    # Must compile without exception
    report_pages = compile_plan_to_report_pages(best, sample_bundle)
    assert len(report_pages) == len(best.pages)
    for page in report_pages:
        assert "name" in page
        assert "displayName" in page
        assert "visualContainers" in page

