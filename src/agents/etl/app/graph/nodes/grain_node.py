from typing import Dict, List, Optional

import pandas as pd

from app.models.grain_models import (
    GrainAnalysis,
    GrainEvidence,
)
from app.models.semantic_models import (
    TableSemanticProfile,
)


class GrainAnalyzer:
    """
    Grain Analyzer.

    Determines what one row represents and evaluates how suitable
    that grain is for fact-table discovery.

    Formula:

        Grain Score =
            0.35 * Key Clarity
          + 0.25 * Atomicity
          + 0.25 * Event Level
          + 0.15 * Fact-Oriented Grain
    """

    WEIGHTS = {
        "key_clarity": 0.35,
        "atomicity": 0.25,
        "event_level": 0.25,
        "fact_oriented_grain": 0.15,
    }

    ATOMICITY_SCORES = {
        "event_detail": 1.00,
        "event": 0.80,
        "entity": 0.40,
        "classification": 0.20,
    }

    EVENT_LEVEL_SCORES = {
        "event_detail": 1.00,
        "event": 0.90,
        "entity": 0.20,
        "classification": 0.10,
    }

    FACT_ORIENTED_GRAIN_SCORES = {
        "event_detail": 1.00,
        "event": 0.90,
        "entity": 0.20,
        "classification": 0.10,
    }

    GRAIN_DESCRIPTIONS = {
        "event_detail": "one event detail / line",
        "event": "one business event / transaction",
        "entity": "one entity",
        "classification": "one classification",
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
    ) -> List[GrainAnalysis]:
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
    ) -> GrainAnalysis:

        semantic_type = self._get_semantic_type(
            table_name
        )

        key_clarity = self._key_clarity_score(
            table_name=table_name,
            df=df,
        )

        atomicity = self._atomicity_score(
            semantic_type
        )

        event_level = self._event_level_score(
            semantic_type
        )

        fact_oriented_grain = self._fact_oriented_grain_score(
            semantic_type
        )

        evidence = GrainEvidence(
            key_clarity=key_clarity,
            atomicity=atomicity,
            event_level=event_level,
            fact_oriented_grain=fact_oriented_grain,
        )

        score = self._weighted_score(evidence)

        grain_description = self._infer_grain_description(
            table_name=table_name,
            semantic_type=semantic_type,
        )

        return GrainAnalysis(
            table_name=table_name,
            grain_description=grain_description,
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

    def _key_clarity_score(
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

        # PK columns must actually exist.
        missing_columns = [
            column
            for column in primary_key
            if column not in df.columns
        ]

        if missing_columns:
            return 0.0

        if len(df) == 0:
            return 0.0

        # A primary key should not contain null values.
        if df[primary_key].isna().any().any():
            return 0.0

        # The complete PK combination should be unique.
        duplicate_count = df.duplicated(
            subset=primary_key,
            keep=False,
        ).sum()

        if duplicate_count > 0:
            return 0.50

        return 1.00

    def _atomicity_score(
        self,
        semantic_type: str,
    ) -> float:

        return self.ATOMICITY_SCORES.get(
            semantic_type,
            0.10,
        )

    def _event_level_score(
        self,
        semantic_type: str,
    ) -> float:

        return self.EVENT_LEVEL_SCORES.get(
            semantic_type,
            0.0,
        )

    def _fact_oriented_grain_score(
        self,
        semantic_type: str,
    ) -> float:

        return self.FACT_ORIENTED_GRAIN_SCORES.get(
            semantic_type,
            0.0,
        )

    def _infer_grain_description(
        self,
        table_name: str,
        semantic_type: str,
    ) -> str:

        # Special case for the current order-line pattern.
        if semantic_type == "event_detail":
            return "one event detail / line"

        if semantic_type == "event":
            return "one business event / transaction"

        if semantic_type == "entity":
            return "one entity"

        if semantic_type == "classification":
            return "one classification"

        return f"one row of {table_name}"

    def _weighted_score(
        self,
        evidence: GrainEvidence,
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


def grain_analysis_node(state: dict) -> dict:
    """Run the existing grain analyzer without changing its scoring logic."""
    analyzer = GrainAnalyzer(
        semantic_profiles=state.get("semantic_profiles", {}),
        primary_keys=state.get("primary_keys", {}),
    )
    return {"grain_results": analyzer.analyze_tables(state["tables_data"])}
