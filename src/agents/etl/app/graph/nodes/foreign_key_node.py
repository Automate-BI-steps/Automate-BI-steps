from typing import Dict, List

import pandas as pd


class ForeignKeyAnalyzer:

    MIN_COVERAGE = 0.95

    @staticmethod
    def _normalize_column_name(name: str) -> str:
        return (
            name
            .lower()
            .replace("_", "")
            .replace("-", "")
            .replace(" ", "")
        )

    @classmethod
    def _column_name_similarity(
        cls,
        source_column: str,
        target_column: str,
    ) -> float:

        source = cls._normalize_column_name(
            source_column
        )

        target = cls._normalize_column_name(
            target_column
        )

        # Strongest signal:
        # customerID -> customerID
        if source == target:
            return 1.0

        # source: customer_id
        # target: customerID
        if (
            source.endswith(target)
            or target.endswith(source)
        ):
            return 0.85

        return 0.0

    @staticmethod
    def _dtype_compatible(
        source_series: pd.Series,
        target_series: pd.Series,
    ) -> bool:

        source_kind = source_series.dtype.kind
        target_kind = target_series.dtype.kind

        # Numeric ↔ numeric
        numeric = {"i", "u", "f"}

        if (
            source_kind in numeric
            and target_kind in numeric
        ):
            return True

        # String ↔ string
        if (
            source_kind == "O"
            and target_kind == "O"
        ):
            return True

        return source_kind == target_kind

    @staticmethod
    def _coverage(
        source_series: pd.Series,
        target_series: pd.Series,
    ) -> float:

        source_values = source_series.dropna()
        target_values = set(
            target_series.dropna()
        )

        if len(source_values) == 0:
            return 0.0

        valid = source_values.isin(
            target_values
        ).sum()

        return float(
            valid / len(source_values)
        )

    @staticmethod
    def _target_unique(
        target_series: pd.Series,
    ) -> bool:

        values = target_series.dropna()

        if len(values) == 0:
            return False

        return values.nunique() == len(values)

    def infer_relationship(
        self,
        source_table: str,
        source_column: str,
        source_df: pd.DataFrame,
        target_table: str,
        target_column: str,
        target_df: pd.DataFrame,
    ) -> dict:

        # ------------------------------------------
        # 1. Column semantic compatibility
        # ------------------------------------------

        name_similarity = (
            self._column_name_similarity(
                source_column,
                target_column,
            )
        )

        if name_similarity == 0.0:
            return {
                "is_relationship": False
            }

        # ------------------------------------------
        # 2. Data type compatibility
        # ------------------------------------------

        if not self._dtype_compatible(
            source_df[source_column],
            target_df[target_column],
        ):
            return {
                "is_relationship": False
            }

        # ------------------------------------------
        # 3. Referential coverage
        # ------------------------------------------

        coverage = self._coverage(
            source_df[source_column],
            target_df[target_column],
        )

        if coverage < self.MIN_COVERAGE:
            return {
                "is_relationship": False
            }

        # ------------------------------------------
        # 4. Target uniqueness
        # ------------------------------------------

        target_unique = self._target_unique(
            target_df[target_column]
        )

        if not target_unique:
            return {
                "is_relationship": False
            }

        # ------------------------------------------
        # 5. Relationship accepted
        # ------------------------------------------

        return {
            "is_relationship": True,
            "coverage": coverage,
            "target_unique": True,
            "referential_integrity": coverage,
            "name_similarity": name_similarity,
        }

    def infer_all(
        self,
        tables: Dict[str, pd.DataFrame],
    ) -> List[dict]:

        relationships = []

        for source_name, source_df in tables.items():

            for source_column in source_df.columns:

                for target_name, target_df in tables.items():

                    for target_column in target_df.columns:

                        # Avoid comparing a column with itself
                        if (
                            source_name == target_name
                            and source_column == target_column
                        ):
                            continue

                        result = self.infer_relationship(
                            source_name,
                            source_column,
                            source_df,
                            target_name,
                            target_column,
                            target_df,
                        )

                        if result["is_relationship"]:

                            relationships.append({
                                "source_table": source_name,
                                "target_table": target_name,
                                "source_columns": [
                                    source_column
                                ],
                                "target_columns": [
                                    target_column
                                ],
                                "cardinality": "N:1",
                                "fk_coverage": result[
                                    "coverage"
                                ],
                                "target_unique": 1.0,
                                "referential_integrity": result[
                                    "referential_integrity"
                                ],
                                "name_similarity": result[
                                    "name_similarity"
                                ],
                            })

        return relationships


def foreign_key_inference_node(state: dict) -> dict:
    """LangGraph node: infer relationships using the existing FK logic unchanged."""
    tables_data = state.get("tables_data", {})
    analyzer = ForeignKeyAnalyzer()
    relationships = analyzer.infer_all(tables_data)
    return {"relationships": relationships}
