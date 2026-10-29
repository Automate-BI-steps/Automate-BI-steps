from dataclasses import dataclass, field
from enum import Enum
from typing import List


class SemanticDomain(str, Enum):
    PERSON = "person"
    ORDER = "order"
    PRODUCT = "product"
    UNKNOWN = "unknown"


class SemanticType(str, Enum):
    ENTITY = "entity"
    EVENT = "event"
    EVENT_DETAIL = "event_detail"
    CLASSIFICATION = "classification"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SemanticMatch:
    domain: SemanticDomain
    semantic_type: SemanticType
    concept: str
    score: float
    matched_patterns: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class TableSemanticProfile:
    table_name: str
    domain: SemanticDomain
    semantic_type: SemanticType
    concept: str
    score: float
    matched_patterns: List[str] = field(default_factory=list)
    alternatives: List[SemanticMatch] = field(default_factory=list)

    @property
    def is_confident(self) -> bool:
        return (
            self.domain != SemanticDomain.UNKNOWN
            and self.score >= 0.60
        )