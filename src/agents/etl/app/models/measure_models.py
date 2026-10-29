from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class MeasureEvidence:
    quantitative_evidence: float
    grain_alignment: float
    semantic_amount_evidence: float
    measure_density: float


@dataclass(frozen=True)
class MeasureAnalysis:
    table_name: str
    measure_columns: List[str]
    score: float
    evidence: MeasureEvidence = field(
        default_factory=lambda: MeasureEvidence(
            quantitative_evidence=0.0,
            grain_alignment=0.0,
            semantic_amount_evidence=0.0,
            measure_density=0.0,
        )
    )

    def evidence_dict(self) -> Dict[str, float]:
        return {
            "quantitative_evidence":
                self.evidence.quantitative_evidence,
            "grain_alignment":
                self.evidence.grain_alignment,
            "semantic_amount_evidence":
                self.evidence.semantic_amount_evidence,
            "measure_density":
                self.evidence.measure_density,
        }