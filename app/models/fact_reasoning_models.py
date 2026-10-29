from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class FactCandidateInput:
    """
    Input provided to the reasoning model for one fact-table candidate.

    The four scores are produced by deterministic analyzers:
        - Grain Score
        - Relationship Score
        - Business Event Score
        - Measure Score

    Phi uses these scores and the associated evidence to calculate
    the final Candidate Score.
    """

    table_name: str

    grain_score: float
    relationship_score: float
    event_score: float
    measure_score: float

    evidence: Dict[str, Any]


@dataclass(frozen=True)
class FactDecision:
    """Final decision produced by the reasoning model."""

    selected_fact: str
    candidate_score: float
    reasoning: List[str]


@dataclass(frozen=True)
class DimensionSelection:
    """One physical dimension selected by Phi."""

    table_name: str
    dimension_type: str
    path: List[str]
    reasoning: str


@dataclass(frozen=True)
class DimensionCandidateDecision:
    """Phi's decision about one dimension candidate."""

    table_name: str
    decision: str  # selected or rejected
    reasoning: str


@dataclass(frozen=True)
class DimensionDecision:
    """Final dimension decisions produced by Phi."""

    selected_dimensions: List[DimensionSelection]
    candidate_decisions: List[DimensionCandidateDecision]
    reasoning: List[str]


