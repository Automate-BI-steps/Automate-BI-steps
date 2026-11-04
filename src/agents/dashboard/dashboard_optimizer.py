"""
dashboard_optimizer.py â€” Phase 2A Adaptive Layout Optimizer

Receives validated candidates and applies bounded, evidence-grounded layout
optimisations. Every operation is re-validated by DashboardCritic before
acceptance. If no optimisation improves the baseline score, the baseline is
returned with an explicit explanation.

Constraints (from Phase 2A spec):
- Never invent measures, fields, findings, or data.
- Never exceed MAX_PAGES or MAX_VISUALS_PER_PAGE.
- Never change KPI formulas, aggregation semantics, or units.
- Never remove required content to inflate a score.
- Every modification is validated before acceptance.
- Bounded revision attempts (optimizer_max_operations from config).
- LLM is never used here â€” all operations are deterministic.
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from src.agents.dashboard.dashboard_critic import DashboardCritic, CriticReport, VALID_TABLES
from src.agents.dashboard.dashboard_evaluator import DashboardEvaluator, EvaluationSummary, CandidateEvaluation
from src.agents.dashboard.dashboard_planner import DashboardPlan, PagePlan, VisualPlan
from src.agents.dashboard.visualization_agent import MAX_PAGES, MAX_VISUALS_PER_PAGE

logger = logging.getLogger("bi_flow.dashboard_optimizer")

# â”€â”€ Semantic model schema â€” imported from dashboard_critic (single source of truth) â”€â”€
_VALID_TABLES = VALID_TABLES


# â”€â”€ Structured finding model â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class OptimizerFinding(BaseModel):
    """A structured, actionable finding produced by the optimizer critic."""
    finding_id: str
    severity: str = Field(description="'error', 'warning', or 'info'")
    page_id: str | None = None
    visual_id: str | None = None
    criterion: str = Field(description="Evaluator criterion this finding affects")
    defect: str = Field(description="Concrete description of the problem")
    evidence: str = Field(description="Specific evidence supporting this finding")
    proposed_repair: str = Field(description="Exact repair action")
    is_deterministic: bool = Field(description="True if repair can be applied automatically")
    post_repair_status: str = Field(default="pending", description="'resolved', 'unresolved', or 'pending'")


# â”€â”€ Per-candidate comparison model â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class PlanComparison(BaseModel):
    """Safe comparison of a single candidate plan."""
    candidate_id: str
    strategy_name: str
    eligible: bool
    total_score: float
    criterion_scores: dict[str, float]        # criterion â†’ raw_score
    visual_count: int
    page_count: int
    kpi_coverage: float                        # fraction of required KPIs covered
    question_coverage: float                   # fraction of business questions addressed
    redundancy_findings: list[str]
    evidence_failures: list[str]
    compiler_warnings: list[str]
    compiler_errors: list[str]
    delta_from_baseline: float | None = None   # total_score - baseline_score


# â”€â”€ Optimization operation record â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class OptimizationOperation(BaseModel):
    operation_id: str
    operation_type: str       # e.g. "reorder_pages", "remove_redundant_visual", ...
    description: str
    applied: bool
    score_before: float
    score_after: float | None = None
    rejection_reason: str | None = None


# â”€â”€ Optimizer report â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class OptimizerReport(BaseModel):
    """Complete record of the optimization run."""
    run_id: str
    user_request: str
    baseline_candidate_id: str
    baseline_score: float
    optimized_candidate_id: str
    optimized_score: float
    improved: bool
    improvement_delta: float
    operations: list[OptimizationOperation]
    findings: list[OptimizerFinding]
    plan_comparisons: list[PlanComparison]
    rejected_operations: list[str]
    unresolved_limitations: list[str]
    final_critic_status: str


# â”€â”€ Main optimizer class â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class DashboardOptimizer:
    """
    Adaptive layout optimizer.

    Works with existing candidates and applies deterministic layout operations
    in priority order. Each operation is validated before being kept.
    """

    # Ordered list of operations by priority (applied in this sequence)
    OPERATION_PRIORITY = [
        "remove_redundant_visuals",
        "reorder_pages_for_audience",
        "add_missing_kpi_card",
        "add_missing_evidence_references",
        "move_misplaced_insights_visual",
    ]

    def __init__(
        self,
        evaluator: DashboardEvaluator | None = None,
        critic: DashboardCritic | None = None,
        max_operations: int = 5,
    ) -> None:
        self.evaluator = evaluator or DashboardEvaluator()
        self.critic = critic or DashboardCritic()
        self.max_operations = max_operations

    # â”€â”€ Public entry point â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def optimize(
        self,
        candidates: list[DashboardPlan],
        selected_plan: DashboardPlan,
        bundle: Any,
        spec: Any,
        eval_summary: EvaluationSummary,
        user_request: str = "",
    ) -> tuple[DashboardPlan, OptimizerReport]:
        """
        Optimise the selected plan.

        Returns:
            (best_valid_plan, OptimizerReport)

        If no operation improves the score, returns the original selected_plan.
        Never returns an invalid plan.
        """
        import uuid
        run_id = str(uuid.uuid4())[:8]

        # 1. Compute baseline score
        selected_eval = self._find_eval(eval_summary, selected_plan)
        baseline_score = selected_eval.total_score if selected_eval else 0.0
        baseline_id = selected_plan.metadata.get("candidate_id", "selected")

        # 2. Build plan comparisons for all candidates
        comparisons = [
            self._build_comparison(c, bundle, spec, eval_summary, baseline_score)
            for c in candidates
        ]

        # 3. Collect findings from the selected plan
        findings = self._collect_findings(selected_plan, bundle, spec)

        # 4. Apply operations
        working_plan = selected_plan.model_copy(deep=True)
        operations: list[OptimizationOperation] = []
        rejected_ops: list[str] = []
        op_idx = 0

        for op_name in self.OPERATION_PRIORITY:
            if op_idx >= self.max_operations:
                rejected_ops.append(f"{op_name}: max_operations limit reached")
                break

            op_method = getattr(self, f"_op_{op_name}", None)
            if op_method is None:
                continue

            current_score = self._rescore(working_plan, bundle, spec)
            candidate_plan = op_method(working_plan, bundle, spec)
            if candidate_plan is None:
                logger.debug("Operation %s: no change needed", op_name)
                continue

            # Validate candidate
            critic_report = self.critic.evaluate(candidate_plan, bundle)
            if not critic_report.passed_deterministic:
                op = OptimizationOperation(
                    operation_id=f"{run_id}_{op_idx}",
                    operation_type=op_name,
                    description=f"Attempted {op_name} â€” rejected by critic",
                    applied=False,
                    score_before=current_score,
                    rejection_reason="; ".join(critic_report.errors[:3]),
                )
                operations.append(op)
                rejected_ops.append(f"{op_name}: {op.rejection_reason}")
                op_idx += 1
                continue

            new_score = self._rescore(candidate_plan, bundle, spec)
            op = OptimizationOperation(
                operation_id=f"{run_id}_{op_idx}",
                operation_type=op_name,
                description=f"Applied {op_name}",
                applied=new_score > current_score,
                score_before=round(current_score, 2),
                score_after=round(new_score, 2),
            )
            if new_score > current_score:
                working_plan = candidate_plan
                logger.info("Operation %s improved score %.2f â†’ %.2f", op_name, current_score, new_score)
            else:
                op.applied = False
                op.rejection_reason = f"Score did not improve: {new_score:.2f} <= {current_score:.2f}"
                rejected_ops.append(f"{op_name}: score did not improve ({new_score:.2f} vs {current_score:.2f})")
            operations.append(op)
            op_idx += 1

        # 5. Final validation
        final_critic = self.critic.evaluate(working_plan, bundle)
        final_score = self._rescore(working_plan, bundle, spec)
        final_id = working_plan.metadata.get("candidate_id", "optimized")

        improved = final_score > baseline_score

        # 6. If no improvement, revert to original
        if not improved:
            working_plan = selected_plan
            final_score = baseline_score
            final_id = baseline_id
            logger.info("Optimizer: no improvement (%.2f â‰¤ %.2f); retaining baseline", final_score, baseline_score)

        # Update metadata
        working_plan.metadata["optimizer_run_id"] = run_id
        working_plan.metadata["optimizer_score"] = round(final_score, 2)
        working_plan.metadata["optimizer_baseline_score"] = round(baseline_score, 2)
        working_plan.metadata["optimizer_improved"] = improved

        report = OptimizerReport(
            run_id=run_id,
            user_request=user_request,
            baseline_candidate_id=baseline_id,
            baseline_score=round(baseline_score, 2),
            optimized_candidate_id=final_id,
            optimized_score=round(final_score, 2),
            improved=improved,
            improvement_delta=round(final_score - baseline_score, 2),
            operations=operations,
            findings=findings,
            plan_comparisons=comparisons,
            rejected_operations=rejected_ops,
            unresolved_limitations=[
                "Power BI Desktop rendering not validated headlessly.",
                "DAX engine evaluation not performed.",
            ],
            final_critic_status=final_critic.status,
        )
        return working_plan, report

    # â”€â”€ Scoring helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _rescore(self, plan: DashboardPlan, bundle: Any, spec: Any) -> float:
        """Re-evaluate a single plan; returns 0.0 if it fails deterministic checks."""
        summary = self.evaluator.evaluate_candidates([plan], bundle, spec)
        evals = [e for e in summary.evaluations if e.passed_deterministic]
        return evals[0].total_score if evals else 0.0

    def _find_eval(self, summary: EvaluationSummary, plan: DashboardPlan) -> CandidateEvaluation | None:
        cid = plan.metadata.get("candidate_id")
        return next((e for e in summary.evaluations if e.candidate_id == cid), None)

    # â”€â”€ Finding collection â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _collect_findings(
        self,
        plan: DashboardPlan,
        bundle: Any,
        spec: Any,
    ) -> list[OptimizerFinding]:
        findings: list[OptimizerFinding] = []
        fid = 0

        known_kpi_ids = {k.id for k in bundle.kpis}
        known_kpi_names = {k.name for k in bundle.kpis}
        required_kpi_ids = set(getattr(spec, "required_kpi_ids", []))

        # F1: Missing required KPIs
        plan_kpis: set[str] = set()
        for p in plan.pages:
            for v in p.visuals:
                if v.kpi_reference:
                    plan_kpis.add(v.kpi_reference)
                for projs in v.roles.values():
                    for proj in projs:
                        if proj.get("kind") == "Measure":
                            for k in bundle.kpis:
                                if k.name == proj.get("name"):
                                    plan_kpis.add(k.id)

        for missing_kpi in required_kpi_ids - plan_kpis:
            fid += 1
            findings.append(OptimizerFinding(
                finding_id=f"OPT-{fid:03d}",
                severity="warning",
                criterion="kpi_relevance",
                defect=f"Required KPI '{missing_kpi}' is absent from all pages in the plan.",
                evidence=f"required_kpi_ids={list(required_kpi_ids)}, plan_kpis={list(plan_kpis)}",
                proposed_repair=f"Add a KPI card for '{missing_kpi}' to ExecutiveOverview page.",
                is_deterministic=True,
            ))

        # F2: Visuals missing evidence references
        for p in plan.pages:
            for v in p.visuals:
                if not v.evidence_references:
                    fid += 1
                    findings.append(OptimizerFinding(
                        finding_id=f"OPT-{fid:03d}",
                        severity="info",
                        page_id=p.name,
                        visual_id=v.id,
                        criterion="evidence_traceability",
                        defect=f"Visual '{v.id}' on page '{p.name}' has no evidence_references.",
                        evidence=f"visual.evidence_references=[]",
                        proposed_repair="Infer evidence reference from visual roles and KPI catalog.",
                        is_deterministic=True,
                    ))

        # F3: Duplicate visual signatures (redundancy)
        seen_sigs: set[str] = set()
        for p in plan.pages:
            for v in p.visuals:
                sig = f"{v.visual_type}_{sorted(v.roles.items())}"
                if sig in seen_sigs and v.visual_type != "card":
                    fid += 1
                    findings.append(OptimizerFinding(
                        finding_id=f"OPT-{fid:03d}",
                        severity="warning",
                        page_id=p.name,
                        visual_id=v.id,
                        criterion="redundancy_penalty",
                        defect=f"Visual '{v.id}' has identical type+roles signature to an earlier visual.",
                        evidence=f"signature={sig}",
                        proposed_repair=f"Remove visual '{v.id}' to eliminate redundancy.",
                        is_deterministic=True,
                    ))
                seen_sigs.add(sig)

        # F4: Non-executive audience with ExecutiveOverview not as page 1
        audience = getattr(spec, "target_audience", "").lower()
        page_names = [p.name for p in plan.pages]
        if ("executive" in audience or "leadership" in audience) and page_names and page_names[0] != "ExecutiveOverview":
            fid += 1
            findings.append(OptimizerFinding(
                finding_id=f"OPT-{fid:03d}",
                severity="warning",
                criterion="actionability",
                defect="ExecutiveOverview is not the first page, but target audience is executive/leadership.",
                evidence=f"page_order={page_names}, audience='{audience}'",
                proposed_repair="Reorder pages so ExecutiveOverview is page 1.",
                is_deterministic=True,
            ))

        # F5: BusinessInsights-type visuals placed on non-Insights pages
        for p in plan.pages:
            if p.name == "BusinessInsights":
                continue
            for v in p.visuals:
                if v.insight_reference:
                    fid += 1
                    findings.append(OptimizerFinding(
                        finding_id=f"OPT-{fid:03d}",
                        severity="info",
                        page_id=p.name,
                        visual_id=v.id,
                        criterion="page_organization",
                        defect=f"Visual '{v.id}' references insight '{v.insight_reference}' but is on page '{p.name}' instead of BusinessInsights.",
                        evidence=f"visual.insight_reference={v.insight_reference}",
                        proposed_repair="Move visual to BusinessInsights page.",
                        is_deterministic=True,
                    ))

        return findings

    # â”€â”€ Layout operations â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _op_remove_redundant_visuals(
        self,
        plan: DashboardPlan,
        bundle: Any,
        spec: Any,
    ) -> DashboardPlan | None:
        """
        Remove duplicate visual signatures (same visual_type + roles) except cards.
        Never removes a visual that is the only one on its page.
        Returns modified plan or None if no change was made.
        """
        candidate = plan.model_copy(deep=True)
        seen_sigs: set[str] = set()
        changed = False

        for page in candidate.pages:
            keep: list[VisualPlan] = []
            for v in page.visuals:
                sig = f"{v.visual_type}_{sorted(v.roles.items())}"
                if sig in seen_sigs and v.visual_type != "card":
                    # Only remove if page still has >= 1 visual after removal
                    remaining = [x for x in page.visuals if x.id != v.id]
                    if len(remaining) >= 1:
                        logger.debug("Removing redundant visual '%s' from page '%s'", v.id, page.name)
                        changed = True
                        continue
                seen_sigs.add(sig)
                keep.append(v)
            page.visuals = keep

        return candidate if changed else None

    def _op_reorder_pages_for_audience(
        self,
        plan: DashboardPlan,
        bundle: Any,
        spec: Any,
    ) -> DashboardPlan | None:
        """
        For executive/leadership audience, ensure ExecutiveOverview is page 1.
        For operations/compliance, ensure DataQualityTraceability is page 2 (after exec overview).
        """
        audience = getattr(spec, "target_audience", "").lower()
        page_names = [p.name for p in plan.pages]
        changed = False
        candidate = plan.model_copy(deep=True)

        if ("executive" in audience or "leadership" in audience or "director" in audience):
            if page_names and page_names[0] != "ExecutiveOverview":
                exec_idx = next((i for i, p in enumerate(candidate.pages) if p.name == "ExecutiveOverview"), None)
                if exec_idx is not None and exec_idx > 0:
                    exec_page = candidate.pages.pop(exec_idx)
                    candidate.pages.insert(0, exec_page)
                    changed = True
                    logger.debug("Reordered pages: ExecutiveOverview moved to position 0 for executive audience")

        elif ("operations" in audience or "compliance" in audience or "audit" in audience):
            gov_idx = next((i for i, p in enumerate(candidate.pages) if p.name == "DataQualityTraceability"), None)
            if gov_idx is not None and gov_idx > 1:
                gov_page = candidate.pages.pop(gov_idx)
                candidate.pages.insert(1, gov_page)
                changed = True
                logger.debug("Reordered pages: DataQualityTraceability moved to position 1 for operations audience")

        return candidate if changed else None

    def _op_add_missing_kpi_card(
        self,
        plan: DashboardPlan,
        bundle: Any,
        spec: Any,
    ) -> DashboardPlan | None:
        """
        If a required KPI is absent from all pages, add a card to ExecutiveOverview
        (if present and within MAX_VISUALS_PER_PAGE limit).
        Never invents a KPI â€” only adds cards for KPIs that exist in the bundle.
        """
        required_kpis = set(getattr(spec, "required_kpi_ids", []))
        if not required_kpis:
            return None

        kpi_map = {k.id: k for k in bundle.kpis}
        plan_kpis: set[str] = set()
        for p in plan.pages:
            for v in p.visuals:
                if v.kpi_reference:
                    plan_kpis.add(v.kpi_reference)
                for projs in v.roles.values():
                    for proj in projs:
                        if proj.get("kind") == "Measure":
                            for k in bundle.kpis:
                                if k.name == proj.get("name"):
                                    plan_kpis.add(k.id)

        missing = required_kpis - plan_kpis
        if not missing:
            return None

        # Find ExecutiveOverview page
        candidate = plan.model_copy(deep=True)
        exec_page = next((p for p in candidate.pages if p.name == "ExecutiveOverview"), None)
        if exec_page is None:
            return None  # Cannot safely add without a target page

        added = False
        for kpi_id in sorted(missing):
            kpi = kpi_map.get(kpi_id)
            if kpi is None:
                continue  # KPI doesn't exist in bundle â€” never invent
            if len(exec_page.visuals) >= MAX_VISUALS_PER_PAGE:
                break  # Respect hard limit

            # Infer the measure table from the KPI object when available; fall back to FactTrade.
            kpi_table = getattr(kpi, "table", None) or "FactTrade"
            new_card = VisualPlan(
                id=f"{kpi_id}_card",
                title=kpi.name,
                visual_type="card",
                kpi_reference=kpi_id,
                roles={"Values": [{"kind": "Measure", "table": kpi_table, "name": kpi.name}]},
                selection_reason=f"Added by optimizer: required KPI '{kpi.name}' was absent from plan.",
                priority="high",
                evidence_references=[f"kpi:{kpi_id}"],
            )
            # Avoid duplicate visual IDs
            existing_ids = {v.id for p in candidate.pages for v in p.visuals}
            if new_card.id in existing_ids:
                continue
            exec_page.visuals.append(new_card)
            added = True
            logger.info("Optimizer added missing KPI card '%s' to ExecutiveOverview", kpi_id)

        return candidate if added else None

    def _op_add_missing_evidence_references(
        self,
        plan: DashboardPlan,
        bundle: Any,
        spec: Any,
    ) -> DashboardPlan | None:
        """
        For visuals with no evidence_references, infer them from roles and KPI catalog.
        Only adds references to tables and KPIs that provably exist.
        """
        kpi_name_to_id = {k.name: k.id for k in bundle.kpis}
        candidate = plan.model_copy(deep=True)
        changed = False

        for p in candidate.pages:
            for v in p.visuals:
                if v.evidence_references:
                    continue  # Already grounded
                inferred: list[str] = []
                if v.kpi_reference:
                    inferred.append(f"kpi:{v.kpi_reference}")
                for projs in v.roles.values():
                    for proj in projs:
                        kind = proj.get("kind", "")
                        table = proj.get("table", "")
                        name = proj.get("name", "")
                        if kind == "Measure" and name in kpi_name_to_id:
                            inferred.append(f"kpi:{kpi_name_to_id[name]}")
                        elif kind in ("Column", "Sum") and table in _VALID_TABLES:
                            inferred.append(f"{table}.{name}")
                if inferred:
                    v.evidence_references = list(dict.fromkeys(inferred))  # deduplicate, preserve order
                    changed = True

        return candidate if changed else None

    def _op_move_misplaced_insights_visual(
        self,
        plan: DashboardPlan,
        bundle: Any,
        spec: Any,
    ) -> DashboardPlan | None:
        """
        Move visuals that reference insights but are on non-BusinessInsights pages.
        Only moves if BusinessInsights page exists and has capacity.
        """
        candidate = plan.model_copy(deep=True)
        insights_page = next((p for p in candidate.pages if p.name == "BusinessInsights"), None)
        if insights_page is None:
            return None

        changed = False
        for page in candidate.pages:
            if page.name == "BusinessInsights":
                continue
            to_move = [v for v in page.visuals if v.insight_reference]
            for v in to_move:
                if len(insights_page.visuals) >= MAX_VISUALS_PER_PAGE:
                    break
                # Remove from current page (keep at least 1 visual)
                remaining = [x for x in page.visuals if x.id != v.id]
                if len(remaining) < 1:
                    continue
                page.visuals = remaining
                insights_page.visuals.append(v)
                changed = True
                logger.info("Optimizer moved visual '%s' from '%s' to BusinessInsights", v.id, page.name)

        return candidate if changed else None

    # â”€â”€ Plan comparison builder â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _build_comparison(
        self,
        plan: DashboardPlan,
        bundle: Any,
        spec: Any,
        eval_summary: EvaluationSummary,
        baseline_score: float,
    ) -> PlanComparison:
        cid = plan.metadata.get("candidate_id", plan.dashboard_title)
        strategy = plan.metadata.get("strategy_name", plan.planning_mode)

        # Find existing evaluation for this candidate
        existing_eval = next((e for e in eval_summary.evaluations if e.candidate_id == cid), None)
        if existing_eval:
            eligible = existing_eval.passed_deterministic
            total = existing_eval.total_score
            crit_scores = {k: cs.raw_score for k, cs in existing_eval.criterion_scores.items()}
            compiler_errors = existing_eval.deterministic_errors
            compiler_warnings = existing_eval.deterministic_warnings
            # Collect findings once and partition by criterion to avoid the double-call.
            all_findings = self._collect_findings(plan, bundle, spec)
            redundancy = [
                f.defect for f in all_findings
                if f.criterion == "redundancy_penalty"
            ]
            evidence_failures = [
                f"Visual '{f.visual_id}' on '{f.page_id}' lacks evidence"
                for f in all_findings
                if f.criterion == "evidence_traceability"
            ]
        else:
            # Re-evaluate
            critic_rpt = self.critic.evaluate(plan, bundle)
            eligible = critic_rpt.passed_deterministic
            sub_summary = self.evaluator.evaluate_candidates([plan], bundle, spec)
            sub_eval = sub_summary.evaluations[0] if sub_summary.evaluations else None
            total = sub_eval.total_score if sub_eval else 0.0
            crit_scores = {k: cs.raw_score for k, cs in (sub_eval.criterion_scores if sub_eval else {}).items()}
            compiler_errors = critic_rpt.errors
            compiler_warnings = critic_rpt.warnings
            redundancy = critic_rpt.redundant_visuals
            evidence_failures = []

        # KPI coverage
        required_kpis = list(getattr(spec, "required_kpi_ids", []))
        plan_kpis: set[str] = set()
        for p in plan.pages:
            for v in p.visuals:
                if v.kpi_reference:
                    plan_kpis.add(v.kpi_reference)
        kpi_cov = (len(plan_kpis.intersection(required_kpis)) / len(required_kpis)) if required_kpis else 1.0

        # Question coverage (from evaluator's criterion score if available)
        q_cov = crit_scores.get("question_coverage", 0.0) / 100.0

        return PlanComparison(
            candidate_id=cid,
            strategy_name=strategy,
            eligible=eligible,
            total_score=round(total, 2),
            criterion_scores={k: round(v, 2) for k, v in crit_scores.items()},
            visual_count=sum(len(p.visuals) for p in plan.pages),
            page_count=len(plan.pages),
            kpi_coverage=round(kpi_cov, 3),
            question_coverage=round(q_cov, 3),
            redundancy_findings=redundancy,
            evidence_failures=evidence_failures,
            compiler_warnings=compiler_warnings,
            compiler_errors=compiler_errors,
            delta_from_baseline=round(total - baseline_score, 2),
        )

