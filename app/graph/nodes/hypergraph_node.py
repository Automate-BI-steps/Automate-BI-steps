from typing import Any, Dict, List

from app.models.hypergraph_models import (
    Cardinality,
    Hyperedge,
    Hypergraph,
    RelationshipType,
)


class HypergraphAnalyzer:

    def build(
        self,
        tables: List[str],
        foreign_keys: List[Dict[str, Any]],
    ) -> Hypergraph:

        hypergraph = Hypergraph(
            tables=list(tables)
        )

        for fk in foreign_keys:

            source_table = fk["source_table"]
            target_table = fk["target_table"]

            source_columns = fk.get(
                "source_columns",
                [],
            )

            target_columns = fk.get(
                "target_columns",
                [],
            )

            cardinality = self._get_cardinality(fk)

            # Preserve all useful structural evidence
            # inside the hypergraph edge.
            evidence = {
                "fk_exists": True,
                "source_table": source_table,
                "target_table": target_table,
                "source_columns": source_columns,
                "target_columns": target_columns,
                "fk_coverage": fk.get(
                    "fk_coverage",
                    0.0,
                ),
                "target_unique": fk.get(
                    "target_unique",
                    0.0,
                ),
                "referential_integrity": fk.get(
                    "referential_integrity",
                    0.0,
                ),
                "name_similarity": fk.get(
                    "name_similarity",
                    0.0,
                ),
            }

            edge = Hyperedge(
                source_table=source_table,
                target_table=target_table,
                source_columns=source_columns,
                target_columns=target_columns,
                relationship_type=RelationshipType.FOREIGN_KEY,
                cardinality=cardinality,
                constraint_name=fk.get(
                    "constraint_name"
                ),
                evidence=evidence,
            )

            hypergraph.add_edge(edge)

        return hypergraph

    @staticmethod
    def _get_cardinality(
        fk: Dict[str, Any]
    ) -> Cardinality:

        cardinality = fk.get("cardinality")

        if cardinality is None:
            return Cardinality.UNKNOWN

        try:
            return Cardinality(cardinality)

        except ValueError:
            return Cardinality.UNKNOWN


def hypergraph_build_node(state: dict) -> dict:
    """LangGraph node: build the hypergraph from inferred relationships unchanged."""
    table_names = state.get("table_names")
    if table_names is None:
        table_names = list(state.get("tables_data", {}).keys())
    relationships = state.get("relationships", [])
    analyzer = HypergraphAnalyzer()
    hypergraph = analyzer.build(tables=table_names, foreign_keys=relationships)
    return {"hypergraph": hypergraph}
