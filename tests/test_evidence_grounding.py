from __future__ import annotations

from pathlib import Path
import pytest
from src.agents.dashboard.dashboard_copilot import DashboardCopilot
from src.agents.dashboard.dashboard_planner import (
    EvidenceCategory,
    VisualEvidence,
    generate_candidate_plans,
)
from src.agents.dashboard.knowledge_agent import KnowledgeAgent


def test_visual_evidence_structure(sample_bundle):
    copilot = DashboardCopilot()
    spec = copilot.interpret("Executive trade dashboard", sample_bundle)

    knowledge_dir = Path(__file__).resolve().parent.parent / "resources" / "knowledge"
    agent = KnowledgeAgent(knowledge_dir)
    chunks = agent.retrieve("trade balance and export trends", top_k=3)

    candidates = generate_candidate_plans(sample_bundle, spec, retrieved_knowledge=chunks)
    assert len(candidates) >= 1

    for candidate in candidates:
        for page in candidate.pages:
            for visual in page.visuals:
                if visual.evidence is not None:
                    ev = visual.evidence
                    assert isinstance(ev, VisualEvidence)
                    assert ev.business_question != ""
                    assert ev.kpi_or_insight != ""
                    assert ev.visual_suitability_reason != ""
                    assert ev.upstream_source != ""
                    assert isinstance(ev.evidence_category, EvidenceCategory)
                    # Evidence category must be one of the approved taxonomy
                    assert ev.evidence_category in (
                        EvidenceCategory.UPSTREAM_FACT,
                        EvidenceCategory.DOMAIN_GUIDANCE,
                        EvidenceCategory.AI_INTERPRETATION,
                        EvidenceCategory.RECOMMENDATION,
                        EvidenceCategory.UNKNOWN,
                    )


def test_evidence_domain_rule_grounding_from_knowledge(sample_bundle):
    copilot = DashboardCopilot()
    spec = copilot.interpret("Export trade performance", sample_bundle)

    knowledge_dir = Path(__file__).resolve().parent.parent / "resources" / "knowledge"
    agent = KnowledgeAgent(knowledge_dir)
    chunks = agent.retrieve("trade balance", top_k=2)

    candidates = generate_candidate_plans(sample_bundle, spec, retrieved_knowledge=chunks)
    exec_c = next(c for c in candidates if c.metadata.get("candidate_id") == "candidate_executive")

    # Find visual with domain rule
    rules_found = [
        v.evidence.domain_rule
        for p in exec_c.pages
        for v in p.visuals
        if v.evidence and v.evidence.domain_rule
    ]
    assert len(rules_found) > 0

