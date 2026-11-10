from __future__ import annotations

import pytest
from src.agents.dashboard.dashboard_copilot import DashboardCopilot
from src.agents.dashboard.dashboard_evaluator import DashboardEvaluator, EvaluationSummary
from src.agents.dashboard.dashboard_planner import generate_candidate_plans


def test_evaluator_scores_all_candidates(sample_bundle):
    copilot = DashboardCopilot()
    spec = copilot.interpret("Executive trade dashboard with country comparisons and recommendations", sample_bundle)
    candidates = generate_candidate_plans(sample_bundle, spec, candidate_count=3)

    evaluator = DashboardEvaluator()
    summary = evaluator.evaluate_candidates(candidates, sample_bundle, spec)

    assert isinstance(summary, EvaluationSummary)
    assert len(summary.evaluations) == 3
    assert summary.selected_candidate_id in ("candidate_executive", "candidate_analytical", "candidate_decision")
    assert summary.selection_rationale != ""

    for score_card in summary.evaluations:
        assert score_card.passed_deterministic is True
        assert 0.0 <= score_card.total_score <= 100.0
        # Phase 1 had 8 criteria; Phase 2A adds 2 more â†’ minimum 8
        assert len(score_card.criteria_scores) >= 8
        # Phase 1 criteria
        assert "objective_alignment" in score_card.criteria_scores
        assert "kpi_relevance" in score_card.criteria_scores
        assert "question_coverage" in score_card.criteria_scores
        assert "evidence_traceability" in score_card.criteria_scores
        assert "visualization_suitability" in score_card.criteria_scores
        assert "compiler_compliance" in score_card.criteria_scores
        # Phase 2A criteria
        assert "cross_page_consistency" in score_card.criteria_scores
        assert "actionability" in score_card.criteria_scores
        # All criterion scores must be bounded
        for cs in score_card.criteria_scores.values():
            assert 0.0 <= cs.raw_score <= 100.0
            assert cs.explanation != ""


def test_evaluator_rejects_candidate_with_hard_failure(sample_bundle):
    copilot = DashboardCopilot()
    spec = copilot.interpret("Executive overview", sample_bundle)
    candidates = generate_candidate_plans(sample_bundle, spec, candidate_count=3)

    # Corrupt candidate 0 with an invalid non-existent table
    bad_candidate = candidates[0].model_copy(deep=True)
    bad_candidate.pages[0].visuals[0].roles["Values"] = [{"kind": "Column", "table": "FakeTable", "name": "FakeCol"}]

    evaluator = DashboardEvaluator()
    corrupted_candidates = [bad_candidate, candidates[1], candidates[2]]
    summary = evaluator.evaluate_candidates(corrupted_candidates, sample_bundle, spec)

    # The corrupted candidate must fail deterministic check and receive 0.0 total score
    bad_score = next(e for e in summary.evaluations if e.candidate_id == bad_candidate.metadata.get("candidate_id"))
    assert bad_score.passed_deterministic is False
    assert bad_score.total_score == 0.0
    assert len(bad_score.validation_failures) > 0
    assert any("FakeTable" in failure for failure in bad_score.validation_failures)

    # Must NOT select the bad candidate even if its qualitative score was previously high!
    assert summary.selected_candidate_id != bad_candidate.metadata.get("candidate_id")
    assert summary.selected_candidate_id in (candidates[1].metadata.get("candidate_id"), candidates[2].metadata.get("candidate_id"))


def test_evaluator_no_candidates_pass_behavior(sample_bundle):
    copilot = DashboardCopilot()
    spec = copilot.interpret("Executive overview", sample_bundle)
    candidates = generate_candidate_plans(sample_bundle, spec, candidate_count=3)

    # Corrupt ALL candidates
    for c in candidates:
        c.pages[0].visuals[0].roles["Values"] = [{"kind": "Column", "table": "MissingTable", "name": "MissingCol"}]

    evaluator = DashboardEvaluator()
    summary = evaluator.evaluate_candidates(candidates, sample_bundle, spec)

    assert summary.selected_candidate_id is None
    assert "No candidate passed deterministic critic gating" in summary.selection_rationale
    for e in summary.evaluations:
        assert e.passed_deterministic is False
        assert e.total_score == 0.0


def test_evaluator_custom_weights(sample_bundle):
    copilot = DashboardCopilot()
    spec = copilot.interpret("Trade report", sample_bundle)
    candidates = generate_candidate_plans(sample_bundle, spec, candidate_count=3)

    # Heavily weight question coverage
    custom_weights = {
        "objective_alignment": 0.05,
        "kpi_relevance": 0.05,
        "question_coverage": 0.60,
        "evidence_traceability": 0.10,
        "visualization_suitability": 0.10,
        "redundancy_penalty": 0.02,
        "page_organization": 0.03,
        "compiler_compliance": 0.05,
    }
    evaluator = DashboardEvaluator(weights=custom_weights)
    summary = evaluator.evaluate_candidates(candidates, sample_bundle, spec)
    assert summary.selected_candidate_id is not None


def test_evaluator_scores_strictly_bounded_at_100(sample_bundle):
    from src.agents.dashboard.config import GeneratorConfig
    cfg = GeneratorConfig()
    copilot = DashboardCopilot()
    spec = copilot.interpret("Executive overview", sample_bundle)
    candidates = generate_candidate_plans(sample_bundle, spec, candidate_count=3)

    evaluator = DashboardEvaluator(weights=cfg.evaluator_weights)
    summary = evaluator.evaluate_candidates(candidates, sample_bundle, spec)

    for ev in summary.evaluations:
        assert 0.0 <= ev.total_score <= 100.0, f"Score {ev.total_score} exceeded 100.0!"


def test_evaluator_penalizes_redundant_visuals(sample_bundle):
    copilot = DashboardCopilot()
    spec = copilot.interpret("Executive overview", sample_bundle)
    candidates = generate_candidate_plans(sample_bundle, spec, candidate_count=3)

    baseline = candidates[0].model_copy(deep=True)
    redundant = candidates[0].model_copy(deep=True)

    # Duplicate visual signature (lineChart trend on another page with a new unique id)
    from src.agents.dashboard.dashboard_planner import VisualPlan
    redundant.pages[1].visuals.append(
        VisualPlan(
            id="duplicate_trend_visual",
            title="Duplicate Import and Export Trends",
            visual_type="lineChart",
            roles=redundant.pages[0].visuals[4].roles,
            selection_reason="Duplicate visual.",
        )
    )

    evaluator = DashboardEvaluator()
    base_eval = evaluator.evaluate_candidates([baseline], sample_bundle, spec).evaluations[0]
    red_eval = evaluator.evaluate_candidates([redundant], sample_bundle, spec).evaluations[0]

    assert base_eval.criterion_scores["redundancy_penalty"].raw_score == 100.0
    assert red_eval.criterion_scores["redundancy_penalty"].raw_score == 85.0
    assert red_eval.total_score < base_eval.total_score


def test_evaluator_penalizes_missing_kpis_and_questions(sample_bundle):
    copilot = DashboardCopilot()
    spec = copilot.interpret("Compare countries and product categories with findings", sample_bundle)
    candidates = generate_candidate_plans(sample_bundle, spec, candidate_count=3)

    complete_candidate = candidates[0].model_copy(deep=True)
    stripped_candidate = candidates[0].model_copy(deep=True)

    # Strip out country and findings pages
    stripped_candidate.pages = [p for p in stripped_candidate.pages if p.name not in ("CountryAnalysis", "BusinessInsights")]

    evaluator = DashboardEvaluator()
    full_eval = evaluator.evaluate_candidates([complete_candidate], sample_bundle, spec).evaluations[0]
    stripped_eval = evaluator.evaluate_candidates([stripped_candidate], sample_bundle, spec).evaluations[0]

    assert stripped_eval.criterion_scores["question_coverage"].raw_score < full_eval.criterion_scores["question_coverage"].raw_score
    assert stripped_eval.total_score < full_eval.total_score

