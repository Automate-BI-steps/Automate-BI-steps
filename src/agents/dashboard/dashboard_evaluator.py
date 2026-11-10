from __future__ import annotations

import logging
from typing import Any
from pydantic import BaseModel, Field

from src.agents.dashboard.dashboard_critic import CriticReport, DashboardCritic
from src.agents.dashboard.dashboard_planner import DashboardPlan

logger = logging.getLogger("bi_flow.dashboard_evaluator")

DEFAULT_WEIGHTS = {
    "objective_alignment": 0.15,
    "kpi_relevance": 0.15,
    "question_coverage": 0.15,
    "evidence_traceability": 0.15,
    "visualization_suitability": 0.15,
    "redundancy_penalty": 0.05,
    "page_organization": 0.10,
    "compiler_compliance": 0.10,
    # Phase 2A: new criteria
    "cross_page_consistency": 0.05,
    "actionability": 0.05,
}


class CriterionScore(BaseModel):
    criterion: str
    raw_score: float = Field(description="Score from 0.0 to 100.0")
    weight: float = Field(description="Normalized criterion weight")
    weighted_score: float = Field(description="raw_score * weight")
    explanation: str = Field(description="Detailed justification for this criterion score")


class CandidateEvaluation(BaseModel):
    candidate_id: str
    strategy_name: str
    dashboard_title: str
    passed_deterministic: bool
    deterministic_errors: list[str] = Field(default_factory=list)
    deterministic_warnings: list[str] = Field(default_factory=list)
    total_score: float = Field(description="Overall weighted quality score (0.0 - 100.0)")
    criterion_scores: dict[str, CriterionScore]
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    selection_verdict: str = Field(description="Eligible, Selected, or Disqualified")

    @property
    def validation_failures(self) -> list[str]:
        return self.deterministic_errors

    @property
    def criteria_scores(self) -> dict[str, CriterionScore]:
        return self.criterion_scores


class EvaluationSummary(BaseModel):
    selected_candidate_id: str | None = None
    selected_strategy: str | None = None
    selection_rationale: str
    evaluations: list[CandidateEvaluation]
    comparison_matrix: dict[str, dict[str, float]]
    unresolved_limitations: list[str] = Field(default_factory=list)


class DashboardEvaluator:
    """Evaluates candidate dashboard plans against weighted quality rubrics and deterministic compiler gates."""

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        critic: DashboardCritic | None = None,
    ) -> None:
        merged = dict(DEFAULT_WEIGHTS)
        if weights:
            merged.update(weights)
        total_w = sum(merged.values()) or 1.0
        self.weights = {k: v / total_w for k, v in merged.items()}
        self.critic = critic or DashboardCritic()

    def evaluate_candidates(
        self,
        candidates: list[DashboardPlan],
        bundle: Any,
        spec: Any,  # DashboardSpecification
    ) -> EvaluationSummary:
        """Evaluate and rank candidate plans, selecting the best valid candidate according to policy."""
        evaluations: list[CandidateEvaluation] = []

        for candidate in candidates:
            # 1. Deterministic gating via DashboardCritic
            critic_report = self.critic.evaluate(candidate, bundle)

            # 2. Evaluate qualitative and quantitative criteria
            scores, strengths, weaknesses = self._score_criteria(candidate, bundle, spec, critic_report)

            # Hard failure gate: if deterministic checks fail, candidate is disqualified
            passed = critic_report.passed_deterministic
            if not passed:
                total_score = 0.0
                verdict = "DISQUALIFIED: Deterministic validation errors"
            else:
                raw_total = sum(cs.weighted_score for cs in scores.values())
                total_score = round(min(100.0, max(0.0, raw_total)), 2)
                verdict = "ELIGIBLE"

            strategy = candidate.metadata.get("strategy_name", candidate.planning_mode)

            evaluations.append(
                CandidateEvaluation(
                    candidate_id=candidate.metadata.get("candidate_id", candidate.dashboard_title),
                    strategy_name=strategy,
                    dashboard_title=candidate.dashboard_title,
                    passed_deterministic=passed,
                    deterministic_errors=critic_report.errors,
                    deterministic_warnings=critic_report.warnings,
                    total_score=total_score,
                    criterion_scores=scores,
                    strengths=strengths,
                    weaknesses=weaknesses,
                    selection_verdict=verdict,
                )
            )

        # 3. Selection policy: highest scoring candidate that passed deterministic checks
        valid_evals = [e for e in evaluations if e.passed_deterministic]
        if not valid_evals:
            logger.warning("No candidate passed deterministic checks; fallback will be triggered.")
            selected_id = None
            selected_strat = None
            rationale = "No candidate passed deterministic critic gating; controlled fallback required."
            warnings = []
        else:
            valid_evals.sort(key=lambda e: e.total_score, reverse=True)
            selected = valid_evals[0]
            selected.selection_verdict = "SELECTED"
            selected_id = selected.candidate_id
            selected_strat = selected.strategy_name
            rationale = (
                f"Candidate '{selected.candidate_id}' ({selected.strategy_name}) achieved the highest validated quality score "
                f"({selected.total_score}/100.0). Key advantages: {'; '.join(selected.strengths[:2])}."
            )
            warnings = selected.deterministic_warnings

        comparison_matrix = {
            e.candidate_id: {k: cs.raw_score for k, cs in e.criterion_scores.items()}
            for e in evaluations
        }

        return EvaluationSummary(
            selected_candidate_id=selected_id,
            selected_strategy=selected_strat,
            selection_rationale=rationale,
            evaluations=evaluations,
            comparison_matrix=comparison_matrix,
            unresolved_limitations=warnings,
        )

    def _score_criteria(
        self,
        candidate: DashboardPlan,
        bundle: Any,
        spec: Any,
        critic_report: CriticReport,
    ) -> tuple[dict[str, CriterionScore], list[str], list[str]]:
        scores: dict[str, CriterionScore] = {}
        strengths: list[str] = []
        weaknesses: list[str] = []

        # 1. Objective Alignment (20%)
        # Does the plan address the user's objective and audience?
        obj_score = 80.0
        obj_lower = spec.business_objective.lower()
        title_lower = candidate.dashboard_title.lower()
        if any(term in title_lower for term in ("executive", "export", "trade", "overview", "analytics")):
            obj_score += 15.0
        if candidate.target_audience == spec.target_audience:
            obj_score += 5.0
        obj_score = min(100.0, max(0.0, obj_score))
        w = self.weights.get("objective_alignment", 0.20)
        scores["objective_alignment"] = CriterionScore(
            criterion="objective_alignment",
            raw_score=obj_score,
            weight=w,
            weighted_score=round(obj_score * w, 2),
            explanation=f"Matches target audience '{candidate.target_audience}' with appropriate scope.",
        )
        if obj_score >= 90:
            strengths.append(f"Strong alignment with target audience '{candidate.target_audience}'")

        # 2. KPI Relevance (15%)
        # Are required KPIs present in visual projections?
        plan_kpis = set()
        for p in candidate.pages:
            for v in p.visuals:
                if v.kpi_reference:
                    plan_kpis.add(v.kpi_reference)
                for projections in v.roles.values():
                    for proj in projections:
                        if proj.get("kind") == "Measure":
                            # map measure name back to kpi id if possible
                            m_name = proj.get("name")
                            for k in bundle.kpis:
                                if k.name == m_name:
                                    plan_kpis.add(k.id)

        required_kpis = set(getattr(spec, "required_kpi_ids", []))
        if required_kpis:
            kpi_overlap = len(plan_kpis.intersection(required_kpis)) / len(required_kpis)
            kpi_score = round(kpi_overlap * 100.0, 1)
        else:
            kpi_score = 90.0
        w = self.weights.get("kpi_relevance", 0.15)
        scores["kpi_relevance"] = CriterionScore(
            criterion="kpi_relevance",
            raw_score=kpi_score,
            weight=w,
            weighted_score=round(kpi_score * w, 2),
            explanation=f"Includes {len(plan_kpis.intersection(required_kpis))} of {len(required_kpis)} required KPI metrics.",
        )
        if kpi_score >= 95:
            strengths.append("Comprehensive coverage of all mandatory KPI metrics")
        elif kpi_score < 75:
            weaknesses.append("Missing one or more requested KPI measures")

        # 3. Question Coverage (15%)
        # Does the plan address business questions (geography, product, trend, findings)?
        questions = getattr(spec, "business_questions", [])
        page_names = {p.name for p in candidate.pages}
        q_hits = 0
        total_q = max(1, len(questions))
        question_pages = (
            (("country", "partner", "exposure"), "CountryAnalysis"),
            (("product", "commodity"), "ProductAnalysis"),
            (("headline", "balance", "volume"), "ExecutiveOverview"),
            (("finding", "recommendation", "insight"), "BusinessInsights"),
        )
        for q in questions:
            q_low = q.lower()
            # The first matching dimension is the question's subject ("product ... volumes" is about products).
            target = next((page for terms, page in question_pages if any(t in q_low for t in terms)), None)
            if target is None:
                q_hits += 0.5  # Partial credit for generic questions no dedicated page answers
            elif target in page_names:
                q_hits += 1
            # A question aimed at a page the plan lacks earns nothing.
        q_score = min(100.0, round((q_hits / total_q) * 100.0, 1))
        w = self.weights.get("question_coverage", 0.15)
        scores["question_coverage"] = CriterionScore(
            criterion="question_coverage",
            raw_score=q_score,
            weight=w,
            weighted_score=round(q_score * w, 2),
            explanation=f"Addresses analytical questions across {len(page_names)} dedicated dashboard pages.",
        )

        # 4. Evidence Traceability (15%)
        # Are visuals backed by evidence references (findings, quality reports, KPI lineage)?
        total_visuals = sum(len(p.visuals) for p in candidate.pages)
        grounded_visuals = sum(1 for p in candidate.pages for v in p.visuals if v.evidence_references)
        trace_ratio = (grounded_visuals / total_visuals) if total_visuals > 0 else 1.0
        trace_score = round(trace_ratio * 100.0, 1)
        w = self.weights.get("evidence_traceability", 0.15)
        scores["evidence_traceability"] = CriterionScore(
            criterion="evidence_traceability",
            raw_score=trace_score,
            weight=w,
            weighted_score=round(trace_score * w, 2),
            explanation=f"{grounded_visuals}/{total_visuals} visuals carry explicit upstream evidence references.",
        )
        if trace_score >= 90:
            strengths.append("High evidence traceability across visual elements")

        # 5. Visualization Suitability (15%)
        # Appropriate visual types (cards for KPIs, line charts for trends, bar charts for comparisons)
        suitability_points = 0
        for p in candidate.pages:
            for v in p.visuals:
                if v.visual_type == "card" and v.kpi_reference:
                    suitability_points += 1
                elif v.visual_type == "lineChart" and ("Date" in str(v.roles) or "trend" in v.id):
                    suitability_points += 1
                elif v.visual_type == "clusteredBarChart" and ("Country" in str(v.roles) or "Product" in str(v.roles)):
                    suitability_points += 1
                elif v.visual_type == "tableEx":
                    suitability_points += 1
                elif v.visual_type == "slicer" and "Direction" in str(v.roles):
                    suitability_points += 1
                else:
                    suitability_points += 0.5
        suit_score = min(100.0, round((suitability_points / max(1, total_visuals)) * 100.0, 1))
        w = self.weights.get("visualization_suitability", 0.15)
        scores["visualization_suitability"] = CriterionScore(
            criterion="visualization_suitability",
            raw_score=suit_score,
            weight=w,
            weighted_score=round(suit_score * w, 2),
            explanation="Visual types follow best practices (cards for KPIs, line charts for trends, bar charts for distributions).",
        )

        # 6. Redundancy Penalty (10%)
        # Penalize duplicate container signatures or visual overload
        red_score = 100.0
        if critic_report.redundant_visuals:
            red_score -= len(critic_report.redundant_visuals) * 15.0
        red_score = max(40.0, red_score)
        w = self.weights.get("redundancy_penalty", 0.10)
        scores["redundancy_penalty"] = CriterionScore(
            criterion="redundancy_penalty",
            raw_score=red_score,
            weight=w,
            weighted_score=round(red_score * w, 2),
            explanation=f"Redundancy assessment: {len(critic_report.redundant_visuals)} overlapping visual signatures detected.",
        )
        if critic_report.redundant_visuals:
            weaknesses.append(f"Contains {len(critic_report.redundant_visuals)} visually repetitive elements")

        # 7. Page Organization and Readability (10%)
        # Density check: visuals per page between 2 and 5 is ideal
        org_points = 0
        for p in candidate.pages:
            v_count = len(p.visuals)
            if 1 <= v_count <= 5:
                org_points += 1
            else:
                org_points += 0.5
        org_score = min(100.0, round((org_points / max(1, len(candidate.pages))) * 100.0, 1))
        w = self.weights.get("page_organization", 0.10)
        scores["page_organization"] = CriterionScore(
            criterion="page_organization",
            raw_score=org_score,
            weight=w,
            weighted_score=round(org_score * w, 2),
            explanation=f"Balanced information density across {len(candidate.pages)} pages without visual clutter.",
        )

        # 8. Compiler & Layout Compliance (10%)
        comp_score = 100.0 if critic_report.passed_deterministic else 0.0
        if critic_report.warnings:
            comp_score = max(50.0, comp_score - len(critic_report.warnings) * 5.0)
        w = self.weights.get("compiler_compliance", 0.10)
        scores["compiler_compliance"] = CriterionScore(
            criterion="compiler_compliance",
            raw_score=comp_score,
            weight=w,
            weighted_score=round(comp_score * w, 2),
            explanation=f"Compiler constraint validation: {len(critic_report.errors)} errors, {len(critic_report.warnings)} warnings.",
        )
        if critic_report.passed_deterministic and not critic_report.warnings:
            strengths.append("Full zero-warning compliance with compiler constraints")
        elif not critic_report.passed_deterministic:
            weaknesses.append("Disqualified by hard compiler or semantic validation errors")

        # 9. Cross-Page Consistency (Phase 2A)
        # Distinct from kpi_relevance: measures whether EACH required KPI appears on AT LEAST ONE page
        # kpi_relevance counts visual projections; this measures page-level KPI distribution breadth
        required_kpis = list(getattr(spec, "required_kpi_ids", []))
        if required_kpis:
            kpi_map_by_id = {k.id: k for k in bundle.kpis}
            kpis_per_page: dict[str, set[str]] = {}
            for p in candidate.pages:
                page_kpis: set[str] = set()
                for v in p.visuals:
                    if v.kpi_reference:
                        page_kpis.add(v.kpi_reference)
                    for projections in v.roles.values():
                        for proj in projections:
                            if proj.get("kind") == "Measure":
                                for k in bundle.kpis:
                                    if k.name == proj.get("name"):
                                        page_kpis.add(k.id)
                kpis_per_page[p.name] = page_kpis
            # Count how many required KPIs appear on exactly one page vs spread across multiple
            kpi_page_counts = {kpi_id: sum(1 for pg_kpis in kpis_per_page.values() if kpi_id in pg_kpis) for kpi_id in required_kpis}
            # Score: each KPI present on >= 1 page gives full credit; missing = 0
            covered = sum(1 for c in kpi_page_counts.values() if c >= 1)
            cpc_score = round((covered / len(required_kpis)) * 100.0, 1)
            cpc_explanation = (
                f"{covered}/{len(required_kpis)} required KPIs appear on at least one dashboard page. "
                f"KPI page distribution: {kpi_page_counts}"
            )
        else:
            cpc_score = 50.0  # Explicitly unknown â€” no required KPIs specified
            cpc_explanation = "No required KPIs specified in specification; cross-page consistency cannot be measured."
        w = self.weights.get("cross_page_consistency", 0.05)
        scores["cross_page_consistency"] = CriterionScore(
            criterion="cross_page_consistency",
            raw_score=cpc_score,
            weight=w,
            weighted_score=round(cpc_score * w, 2),
            explanation=cpc_explanation,
        )
        if cpc_score >= 100.0:
            strengths.append("All required KPIs are distributed across dedicated dashboard pages")
        elif cpc_score < 50.0:
            weaknesses.append("One or more required KPIs are absent from all pages in the plan")

        # 10. Actionability (Phase 2A)
        # Audience-sensitive: measures layout relevance to declared target audience
        # Distinguished from objective_alignment which uses title/audience text matching;
        # this inspects the actual page ORDER and visual PRIORITY to assess UX appropriateness.
        audience_lower = getattr(spec, "target_audience", candidate.target_audience).lower()
        page_names_ordered = [p.name for p in candidate.pages]
        act_score = 50.0  # Neutral baseline when audience is unmeasured
        act_notes = []
        if "executive" in audience_lower or "director" in audience_lower or "leadership" in audience_lower:
            # Executive audience: ExecutiveOverview should be page 1; should have high-priority KPI cards
            if page_names_ordered and page_names_ordered[0] == "ExecutiveOverview":
                act_score = 80.0
                act_notes.append("ExecutiveOverview is the landing page (correct for executive audience)")
                # Bonus: KPI cards with high priority on page 1
                exec_page = candidate.pages[0]
                high_cards = sum(1 for v in exec_page.visuals if v.visual_type == "card" and v.priority == "high")
                if high_cards >= 2:
                    act_score = min(100.0, act_score + 20.0)
                    act_notes.append(f"{high_cards} high-priority KPI cards on landing page")
            else:
                act_score = 40.0
                act_notes.append("ExecutiveOverview is not the landing page; executive UX priority is low")
                weaknesses.append("ExecutiveOverview is not page 1; suboptimal for executive audience")
        elif "analyst" in audience_lower or "policy" in audience_lower or "planner" in audience_lower:
            # Analyst audience: dimensional pages (Country, Product) should exist and be prominent
            dimensional_pages = {"CountryAnalysis", "ProductAnalysis"}
            present_dims = dimensional_pages.intersection(set(page_names_ordered))
            act_score = round(min(100.0, 50.0 + (len(present_dims) / len(dimensional_pages)) * 50.0), 1)
            act_notes.append(f"Analytical pages present: {sorted(present_dims)} ({len(present_dims)}/{len(dimensional_pages)})")
        elif "operations" in audience_lower or "compliance" in audience_lower or "audit" in audience_lower:
            # Operations audience: governance page should exist and be accessible early
            gov_page_idx = next((i for i, p in enumerate(candidate.pages) if p.name == "DataQualityTraceability"), None)
            if gov_page_idx is not None:
                prominence = max(0, 1 - (gov_page_idx / max(1, len(candidate.pages))))
                act_score = round(50.0 + prominence * 50.0, 1)
                act_notes.append(f"DataQualityTraceability found at page index {gov_page_idx} (prominence={prominence:.2f})")
            else:
                act_score = 20.0
                act_notes.append("DataQualityTraceability page absent; critical for operations/compliance audience")
                weaknesses.append("Missing DataQualityTraceability page for operations audience")
        else:
            act_score = 50.0
            act_notes.append("Audience type not recognized for actionability heuristic; defaulting to neutral 50")
        act_score = min(100.0, max(0.0, act_score))
        w = self.weights.get("actionability", 0.05)
        scores["actionability"] = CriterionScore(
            criterion="actionability",
            raw_score=act_score,
            weight=w,
            weighted_score=round(act_score * w, 2),
            explanation="Audience-sensitive layout actionability. " + " | ".join(act_notes),
        )
        if act_score >= 85:
            strengths.append("Dashboard layout optimised for declared target audience")

        return scores, strengths, weaknesses

