
from dataclasses import dataclass, field
from typing import Dict


@dataclass(frozen=True)
class RelationshipEvidence:
    """Structural evidence and analytical scoring features."""

    # Analytical scoring features
    semantic_role: float = 0.0
    grain_interaction: float = 0.0
    hypergraph_relevance: float = 0.0
    cardinality_relevance: float = 0.0

    # Structural relationship evidence
    fk_exists: float = 0.0
    cardinality: float = 0.0
    fk_coverage: float = 0.0
    target_unique: float = 0.0
    referential_integrity: float = 0.0

    def to_dict(self) -> Dict[str, float]:
        return {
            "semantic_role": self.semantic_role,
            "grain_interaction": self.grain_interaction,
            "hypergraph_relevance": self.hypergraph_relevance,
            "cardinality_relevance": self.cardinality_relevance,
            "fk_exists": self.fk_exists,
            "cardinality": self.cardinality,
            "fk_coverage": self.fk_coverage,
            "target_unique": self.target_unique,
            "referential_integrity": self.referential_integrity,
        }


@dataclass(frozen=True)
class RelationshipAnalysis:
    source_table: str
    target_table: str
    score: float

    evidence: RelationshipEvidence = field(
        default_factory=RelationshipEvidence
    )

    def evidence_dict(self) -> Dict[str, float]:
        return self.evidence.to_dict()
