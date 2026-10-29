from dataclasses import dataclass, field
from typing import Dict


@dataclass(frozen=True)
class EventEvidence:
    semantic_event: float
    event_identity: float
    temporal_evidence: float
    occurrence_evidence: float


@dataclass(frozen=True)
class EventAnalysis:
    table_name: str
    event_description: str
    score: float
    evidence: EventEvidence = field(
        default_factory=lambda: EventEvidence(
            semantic_event=0.0,
            event_identity=0.0,
            temporal_evidence=0.0,
            occurrence_evidence=0.0,
        )
    )

    def evidence_dict(self) -> Dict[str, float]:
        return {
            "semantic_event": self.evidence.semantic_event,
            "event_identity": self.evidence.event_identity,
            "temporal_evidence": self.evidence.temporal_evidence,
            "occurrence_evidence": self.evidence.occurrence_evidence,
        }