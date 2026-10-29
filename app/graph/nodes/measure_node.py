from typing import Dict, List, Optional

import pandas as pd

from app.models.measure_models import (
    MeasureAnalysis,
    MeasureEvidence,
)
from app.models.semantic_models import (
    TableSemanticProfile,
)


class MeasureAnalyzer:
    """
    Measure Analyzer.

    Determines whether a table contains meaningful quantitative
    attributes suitable for measuring a business occurrence.

    Formula:

        Measure Score =
            0.30 * Quantitative Evidence
          + 0.25 * Grain Alignment
          + 0.25 * Semantic Amount Evidence
          + 0.20 * Measure Density
    """

    WEIGHTS = {
        "quantitative_evidence": 0.30,
        "grain_alignment": 0.25,
        "semantic_amount_evidence": 0.25,
        "measure_density": 0.20,
    }

    NUMERIC_DTYPES = (
        "int",
        "float",
        "decimal",
    )

    MEASURE_KEYWORDS = (
        "quantity",
        "qty",
        "amount",
        "price",
        "cost",
        "revenue",
        "sales",
        "total",
        "discount",
        "tax",
        "freight",
        "duration",
        "value",
        "weight",
        "rate",
        "hours",
        "volume",
    )

    ID_KEYWORDS = (
        "id",
        "key",
        "code",
    )

    EVENT_TYPES = {
        "event_detail",
        "event",
    }

    def __init__(
        self,
        semantic_profiles: Optional[
            Dict[str, TableSemanticProfile]
        ] = None,
    ):
        self.semantic_profiles = semantic_profiles or {}

    def analyze_tables(
        self,
        tables_data: Dict[str, pd.DataFrame],
    ) -> List[MeasureAnalysis]:

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
    ) -> MeasureAnalysis:

        semantic_type = self._get_semantic_type(
            table_name
        )

        measure_columns = self._detect_measure_columns(
            df
        )

        quantitative_evidence = (
            self._quantitative_evidence(
                df=df,
                measure_columns=measure_columns,
            )
        )

        grain_alignment = (
            self._grain_alignment(
                semantic_type=semantic_type,
                measure_columns=measure_columns,
            )
        )

        semantic_amount_evidence = (
            self._semantic_amount_evidence(
                measure_columns
            )
        )

        measure_density = (
            self._measure_density(
                df=df,
                measure_columns=measure_columns,
            )
        )

        evidence = MeasureEvidence(
            quantitative_evidence=quantitative_evidence,
            grain_alignment=grain_alignment,
            semantic_amount_evidence=semantic_amount_evidence,
            measure_density=measure_density,
        )

        score = self._weighted_score(evidence)

        return MeasureAnalysis(
            table_name=table_name,
            measure_columns=measure_columns,
            score=score,
            evidence=evidence,
        )

    # ------------------------------------------------------------------
    # Semantic type
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Measure column detection
    # ------------------------------------------------------------------

    def _detect_measure_columns(
        self,
        df: pd.DataFrame,
    ) -> List[str]:

        measures = []

        for column in df.columns:

            series = df[column]

            dtype = str(
                series.dtype
            ).lower()

            # Only numeric columns can be measures.
            if not any(
                numeric_type in dtype
                for numeric_type in self.NUMERIC_DTYPES
            ):
                continue

            normalized = self._normalize_column_name(
                column
            )

            # IDs, keys and codes are not measures.
            if any(
                keyword in normalized
                for keyword in self.ID_KEYWORDS
            ):
                continue

            # Numeric columns with clear measure semantics.
            if any(
                keyword in normalized
                for keyword in self.MEASURE_KEYWORDS
            ):
                measures.append(column)

        return measures

    # ------------------------------------------------------------------
    # Quantitative Evidence
    # ------------------------------------------------------------------

    def _quantitative_evidence(
        self,
        df: pd.DataFrame,
        measure_columns: List[str],
    ) -> float:

        if df.empty:
            return 0.0

        numeric_non_identifier_columns = []

        for column in df.columns:

            dtype = str(
                df[column].dtype
            ).lower()

            # Only numeric columns participate in
            # quantitative evidence.
            if not any(
                numeric_type in dtype
                for numeric_type in self.NUMERIC_DTYPES
            ):
                continue

            normalized = self._normalize_column_name(
                column
            )

            # Numeric identifiers must NOT dilute
            # quantitative evidence.
            if any(
                keyword in normalized
                for keyword in self.ID_KEYWORDS
            ):
                continue

            numeric_non_identifier_columns.append(
                column
            )

        # No valid numeric attributes.
        if not numeric_non_identifier_columns:
            return 0.0

        ratio = (
            len(measure_columns)
            / len(numeric_non_identifier_columns)
        )

        return round(
            min(1.0, ratio),
            4,
        )

    # ------------------------------------------------------------------
    # Grain Alignment
    # ------------------------------------------------------------------

    def _grain_alignment(
        self,
        semantic_type: str,
        measure_columns: List[str],
    ) -> float:

        if not measure_columns:
            return 0.0

        if semantic_type == "event_detail":
            return 1.0

        if semantic_type == "event":
            return 0.9

        if semantic_type == "entity":
            return 0.35

        if semantic_type == "classification":
            return 0.20

        return 0.0

    # ------------------------------------------------------------------
    # Semantic Amount Evidence
    # ------------------------------------------------------------------

    def _semantic_amount_evidence(
        self,
        measure_columns: List[str],
    ) -> float:

        if not measure_columns:
            return 0.0

        matched = 0

        for column in measure_columns:

            normalized = self._normalize_column_name(
                column
            )

            if any(
                keyword in normalized
                for keyword in self.MEASURE_KEYWORDS
            ):
                matched += 1

        return round(
            matched / len(measure_columns),
            4,
        )

    # ------------------------------------------------------------------
    # Measure Density
    # ------------------------------------------------------------------

    def _measure_density(
        self,
        df: pd.DataFrame,
        measure_columns: List[str],
    ) -> float:

        if (
            df.empty
            or len(df.columns) == 0
        ):
            return 0.0

        density = (
            len(measure_columns)
            / len(df.columns)
        )

        return round(
            min(1.0, density * 2.0),
            4,
        )

    # ------------------------------------------------------------------
    # Weighted Measure Score
    # ------------------------------------------------------------------

    def _weighted_score(
        self,
        evidence: MeasureEvidence,
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

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_column_name(
        column: str,
    ) -> str:

        return (
            str(column)
            .strip()
            .lower()
            .replace("_", "")
            .replace("-", "")
            .replace(" ", "")
        )


def measure_analysis_node(state: dict) -> dict:
    """Run the existing measure analyzer without changing its scoring logic."""
    analyzer = MeasureAnalyzer(
        semantic_profiles=state.get("semantic_profiles", {}),
    )
    return {"measure_results": analyzer.analyze_tables(state["tables_data"])}
