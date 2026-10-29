import re
from typing import Dict, Iterable, List

from app.models.semantic_models import (
    SemanticDomain,
    SemanticMatch,
    SemanticType,
    TableSemanticProfile,
)
from app.ontology.semantic_dictionary import get_semantic_dictionary


class SemanticAnalyzer:

    _TYPE_BY_SECTION = {
        "entities": SemanticType.ENTITY,
        "events": SemanticType.EVENT,
        "event_details": SemanticType.EVENT_DETAIL,
        "classifications": SemanticType.CLASSIFICATION,
    }

    def __init__(self, dictionary: Dict | None = None):
        self.dictionary = dictionary or get_semantic_dictionary()

    @staticmethod
    def _normalize(value: str) -> str:
        value = value.lower().strip()

        value = re.sub(
            r"([a-z0-9])([A-Z])",
            r"\1_\2",
            value
        )

        value = re.sub(
            r"[^a-z0-9]+",
            "_",
            value
        )

        return re.sub(
            r"_+",
            "_",
            value
        ).strip("_")

    @staticmethod
    def _singularize(value: str) -> str:

        if value.endswith("ies") and len(value) > 3:
            return value[:-3] + "y"

        if value.endswith("ses") and len(value) > 3:
            return value[:-2]

        if (
            value.endswith("s")
            and not value.endswith("ss")
            and len(value) > 2
        ):
            return value[:-1]

        return value

    def _pattern_score(
        self,
        table_name: str,
        pattern: str
    ) -> float:

        table = self._normalize(table_name)
        pattern = self._normalize(pattern)

        table_singular = self._singularize(table)
        pattern_singular = self._singularize(pattern)

        # Exact match
        if (
            table == pattern
            or table_singular == pattern_singular
        ):
            return 1.0

        # Pattern components are contained in table
        if set(pattern.split("_")) <= set(table.split("_")):
            return 0.85

        # Partial semantic match
        if (
            pattern in table
            or pattern_singular in table_singular
        ):
            return 0.70

        return 0.0

    def _matches(
        self,
        table_name: str
    ) -> List[SemanticMatch]:

        matches = []

        for domain_name, sections in self.dictionary.get(
            "domains",
            {}
        ).items():

            try:
                domain = SemanticDomain(domain_name)
            except ValueError:
                continue

            for section, patterns in sections.items():

                semantic_type = self._TYPE_BY_SECTION.get(
                    section,
                    SemanticType.UNKNOWN
                )

                for pattern in patterns:

                    score = self._pattern_score(
                        table_name,
                        pattern
                    )

                    if score > 0:

                        matches.append(
                            SemanticMatch(
                                domain=domain,
                                semantic_type=semantic_type,
                                concept=pattern,
                                score=score,
                                matched_patterns=[pattern],
                            )
                        )

        return sorted(
            matches,
            key=lambda match: match.score,
            reverse=True
        )

    def analyze_table(
        self,
        table_name: str
    ) -> TableSemanticProfile:

        matches = self._matches(table_name)

        if not matches:

            return TableSemanticProfile(
                table_name=table_name,
                domain=SemanticDomain.UNKNOWN,
                semantic_type=SemanticType.UNKNOWN,
                concept="unknown",
                score=0.0,
            )

        best = matches[0]

        return TableSemanticProfile(
            table_name=table_name,
            domain=best.domain,
            semantic_type=best.semantic_type,
            concept=best.concept,
            score=best.score,
            matched_patterns=best.matched_patterns,
            alternatives=matches[1:],
        )

    def analyze_tables(
        self,
        table_names: Iterable[str]
    ) -> Dict[str, TableSemanticProfile]:

        return {
            name: self.analyze_table(name)
            for name in table_names
        }


def semantic_analysis_node(state: dict) -> dict:
    """LangGraph node that runs the existing semantic analyzer unchanged."""
    table_names = state.get("table_names")
    if table_names is None:
        tables_data = state.get("tables_data", {})
        table_names = list(tables_data.keys())

    analyzer = SemanticAnalyzer()
    semantic_profiles = analyzer.analyze_tables(table_names)
    return {"semantic_profiles": semantic_profiles}
