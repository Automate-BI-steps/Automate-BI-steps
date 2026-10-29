from typing import Dict, List, Optional

import pandas as pd

from app.models.event_models import (
    EventAnalysis,
    EventEvidence,
)
from app.models.semantic_models import (
    TableSemanticProfile,
)


class EventAnalyzer:
    """
    Business Event Analyzer.

    Determines whether a table represents a business occurrence,
    transaction, event, or event detail.

    Formula:

        Business Event Score =
            0.45 * Semantic Event
          + 0.20 * Event Identity
          + 0.15 * Temporal Evidence
          + 0.20 * Occurrence Evidence
    """

    WEIGHTS = {
        "semantic_event": 0.45,
        "event_identity": 0.20,
        "temporal_evidence": 0.15,
        "occurrence_evidence": 0.20,
    }

    SEMANTIC_EVENT_SCORES = {
        "event_detail": 1.00,
        "event": 0.90,
        "entity": 0.20,
        "classification": 0.10,
    }

    OCCURRENCE_SCORES = {
        "event_detail": 1.00,
        "event": 0.90,
        "entity": 0.20,
        "classification": 0.10,
    }

    TEMPORAL_KEYWORDS = (
        "date",
        "time",
        "datetime",
        "timestamp",
        "created",
        "updated",
        "occurred",
        "ordered",
        "shipped",
        "delivered",
        "completed",
    )

    EVENT_DESCRIPTIONS = {
        "event_detail": "one business event detail / occurrence line",
        "event": "one business event / transaction",
        "entity": "not a business event; represents an entity",
        "classification": "not a business event; represents a classification",
    }

    def __init__(
        self,
        semantic_profiles: Optional[
            Dict[str, TableSemanticProfile]
        ] = None,
        primary_keys: Optional[
            Dict[str, List[str]]
        ] = None,
    ):
        self.semantic_profiles = semantic_profiles or {}
        self.primary_keys = primary_keys or {}

    def analyze_tables(
        self,
        tables_data: Dict[str, pd.DataFrame],
    ) -> List[EventAnalysis]:

        return [
            self.analyze_table(
                table_name=table_name,
                df=df,
            )
            for table_name, df in tables_data.items()
        ]

    def analyze_table(
        self,
        table_name: str,
        df: pd.DataFrame,
    ) -> EventAnalysis:

        semantic_type = self._get_semantic_type(
            table_name
        )

        semantic_event = (
            self._semantic_event_score(
                semantic_type
            )
        )

        event_identity = (
            self._event_identity_score(
                table_name=table_name,
                df=df,
            )
        )

        temporal_evidence = (
            self._temporal_evidence_score(df)
        )

        occurrence_evidence = (
            self._occurrence_evidence_score(
                semantic_type
            )
        )

        evidence = EventEvidence(
            semantic_event=semantic_event,
            event_identity=event_identity,
            temporal_evidence=temporal_evidence,
            occurrence_evidence=occurrence_evidence,
        )

        score = self._weighted_score(evidence)

        event_description = (
            self._event_description(
                semantic_type
            )
        )

        return EventAnalysis(
            table_name=table_name,
            event_description=event_description,
            score=score,
            evidence=evidence,
        )

    def _get_semantic_type(
        self,
        table_name: str,
    ) -> str:

        profile = self.semantic_profiles.get(
            table_name
        )

        if profile is None:
            return "unknown"

        return profile.semantic_type.value

    def _semantic_event_score(
        self,
        semantic_type: str,
    ) -> float:

        return self.SEMANTIC_EVENT_SCORES.get(
            semantic_type,
            0.0,
        )

    def _event_identity_score(
        self,
        table_name: str,
        df: pd.DataFrame,
    ) -> float:

        primary_key = self.primary_keys.get(
            table_name,
            [],
        )

        if not primary_key:
            return 0.0

        if any(
            column not in df.columns
            for column in primary_key
        ):
            return 0.0

        if len(df) == 0:
            return 0.0

        if df[primary_key].isna().any().any():
            return 0.0

        unique = not df.duplicated(
            subset=primary_key
        ).any()

        return 1.0 if unique else 0.5

    def _temporal_evidence_score(
        self,
        df: pd.DataFrame,
    ) -> float:

        if df.empty:
            return 0.0

        temporal_columns = 0

        for column in df.columns:

            normalized = (
                str(column)
                .strip()
                .lower()
                .replace("_", "")
                .replace("-", "")
                .replace(" ", "")
            )

            if any(
                keyword in normalized
                for keyword in self.TEMPORAL_KEYWORDS
            ):
                temporal_columns += 1

        if temporal_columns == 0:
            return 0.0

        if temporal_columns == 1:
            return 0.75

        return 1.0

    def _occurrence_evidence_score(
        self,
        semantic_type: str,
    ) -> float:

        return self.OCCURRENCE_SCORES.get(
            semantic_type,
            0.0,
        )

    def _weighted_score(
        self,
        evidence: EventEvidence,
    ) -> float:

        values = evidence.__dict__

        score = sum(
            values[name] * weight
            for name, weight in self.WEIGHTS.items()
        )

        return round(
            max(0.0, min(1.0, score)),
            4,
        )

    def _event_description(
        self,
        semantic_type: str,
    ) -> str:

        return self.EVENT_DESCRIPTIONS.get(
            semantic_type,
            "unknown business event role",
        )


def business_event_analysis_node(state: dict) -> dict:
    """Run the existing business-event analyzer without changing its scoring logic."""
    analyzer = EventAnalyzer(
        semantic_profiles=state.get("semantic_profiles", {}),
        primary_keys=state.get("primary_keys", {}),
    )
    return {"event_results": analyzer.analyze_tables(state["tables_data"])}
