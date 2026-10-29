from dataclasses import dataclass, field
from typing import Dict


@dataclass(frozen=True)
class GrainEvidence:
    key_clarity: float
    atomicity: float
    event_level: float
    fact_oriented_grain: float


@dataclass(frozen=True)
class GrainAnalysis:
    table_name: str
    grain_description: str
    score: float
    evidence: GrainEvidence = field(
        default_factory=lambda: GrainEvidence(
            key_clarity=0.0,
            atomicity=0.0,
            event_level=0.0,
            fact_oriented_grain=0.0,
        )
    )

    def evidence_dict(self) -> Dict[str, float]:
        return {
            "key_clarity": self.evidence.key_clarity,
            "atomicity": self.evidence.atomicity,
            "event_level": self.evidence.event_level,
            "fact_oriented_grain": self.evidence.fact_oriented_grain,
        }