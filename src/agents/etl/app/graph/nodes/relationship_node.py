
from typing import Dict, Optional

from app.models.hypergraph_models import (
    Cardinality,
    Hyperedge,
    Hypergraph,
    RelationshipType,
)
from app.models.scoring_models import (
    RelationshipAnalysis,
    RelationshipEvidence,
)
from app.models.semantic_models import TableSemanticProfile


class RelationshipAnalyzer:
    """
    Analytical relationship analyzer.

    Relationship Score =
        0.35 * Semantic Role
      + 0.30 * Grain Interaction
      + 0.20 * Hypergraph Relevance
      + 0.15 * Cardinality Relevance

    Structural evidence is exposed separately from the score.
    """

    WEIGHTS = {
        "semantic_role": 0.35,
        "grain_interaction": 0.30,
        "hypergraph_relevance": 0.20,
        "cardinality_relevance": 0.15,
    }

    SEMANTIC_ROLE_SCORES = {
        ("event_detail", "event"): 1.00,
        ("event_detail", "entity"): 0.90,
        ("event", "entity"): 0.85,
        ("event", "event"): 0.80,
        ("entity", "entity"): 0.60,
        ("entity", "classification"): 0.30,
        ("classification", "entity"): 0.30,
    }

    CARDINALITY_SCORES = {
        Cardinality.MANY_TO_ONE: 1.00,
        Cardinality.ONE_TO_MANY: 0.80,
        Cardinality.ONE_TO_ONE: 0.70,
        Cardinality.MANY_TO_MANY: 0.50,
        Cardinality.UNKNOWN: 0.00,
    }

    def __init__(
        self,
        semantic_profiles: Optional[
            Dict[str, TableSemanticProfile]
        ] = None,
        primary_keys: Optional[Dict[str, list[str]]] = None,
        semantic_priors: Optional[
            Dict[str, Dict[str, float]]
        ] = None,
    ):
        self.semantic_profiles = semantic_profiles or {}
        self.primary_keys = primary_keys or {}
        self.semantic_priors = semantic_priors or {}

    def analyze(
        self,
        hypergraph: Hypergraph,
    ) -> list[RelationshipAnalysis]:
        return [
            self.analyze_edge(edge, hypergraph)
            for edge in hypergraph.edges
        ]

    def analyze_edge(
        self,
        edge: Hyperedge,
        hypergraph: Hypergraph,
    ) -> RelationshipAnalysis:
        semantic_role = self._semantic_role_score(edge)
        grain_interaction = self._grain_interaction_score(edge)
        hypergraph_relevance = self._hypergraph_relevance(
            edge, hypergraph
        )
        cardinality_relevance = self._cardinality_score(
            edge.cardinality
        )

        structural = edge.evidence or {}

        evidence = RelationshipEvidence(
            semantic_role=semantic_role,
            grain_interaction=grain_interaction,
            hypergraph_relevance=hypergraph_relevance,
            cardinality_relevance=cardinality_relevance,
            fk_exists=(
                1.0
                if edge.relationship_type == RelationshipType.FOREIGN_KEY
                else 0.0
            ),
            cardinality=cardinality_relevance,
            fk_coverage=self._evidence_value(
                structural, "fk_coverage"
            ),
            target_unique=self._evidence_value(
                structural, "target_unique"
            ),
            referential_integrity=self._evidence_value(
                structural, "referential_integrity"
            ),
        )

        score = self._weighted_score(evidence)

        return RelationshipAnalysis(
            source_table=edge.source_table,
            target_table=edge.target_table,
            score=score,
            evidence=evidence,
        )

    @staticmethod
    def _evidence_value(
        evidence: dict,
        key: str,
    ) -> float:
        """Read a supplied structural value without inventing evidence."""
        value = evidence.get(key, 0.0)

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"Structural evidence '{key}' must be numeric."
            )

        if not 0.0 <= value <= 1.0:
            raise ValueError(
                f"Structural evidence '{key}' must be between 0 and 1."
            )

        return float(value)

    def _semantic_role_score(self, edge: Hyperedge) -> float:
        source_priors = self.semantic_priors.get(
            edge.source_table, {}
        )
        prior = source_priors.get(edge.target_table)

        if prior is not None:
            if (
                isinstance(prior, bool)
                or not isinstance(prior, (int, float))
                or not 0.0 <= prior <= 1.0
            ):
                raise ValueError(
                    f"Invalid semantic prior for "
                    f"{edge.source_table} -> {edge.target_table}: {prior}"
                )
            return float(prior)

        source = self.semantic_profiles.get(edge.source_table)
        target = self.semantic_profiles.get(edge.target_table)

        if source is None or target is None:
            return 0.20

        key = (
            source.semantic_type.value,
            target.semantic_type.value,
        )
        return self.SEMANTIC_ROLE_SCORES.get(key, 0.20)

    def _grain_interaction_score(self, edge: Hyperedge) -> float:
        primary_key = self.primary_keys.get(edge.source_table, [])

        if not primary_key:
            return 0.30

        source_columns = set(edge.source_columns)
        primary_key_columns = set(primary_key)

        if source_columns.intersection(primary_key_columns):
            return 1.00

        source_profile = self.semantic_profiles.get(edge.source_table)

        if source_profile is not None:
            semantic_type = source_profile.semantic_type.value

            if semantic_type == "event_detail":
                return 0.80

            if semantic_type == "event":
                return 0.30

        return 0.30

    def _hypergraph_relevance(
        self,
        edge: Hyperedge,
        hypergraph: Hypergraph,
    ) -> float:
        if not hypergraph.edges:
            return 0.0

        centralities = {
            self._edge_key(current_edge): self._edge_betweenness(
                current_edge, hypergraph
            )
            for current_edge in hypergraph.edges
        }

        current_value = centralities.get(self._edge_key(edge), 0.0)
        maximum = max(centralities.values(), default=0.0)

        if maximum == 0.0:
            return 0.0

        return round(current_value / maximum, 4)

    @staticmethod
    def _edge_key(edge: Hyperedge) -> tuple:
        return (
            edge.source_table,
            edge.target_table,
            tuple(edge.source_columns),
            tuple(edge.target_columns),
        )

    def _edge_betweenness(
        self,
        edge: Hyperedge,
        hypergraph: Hypergraph,
    ) -> float:
        tables = hypergraph.tables
        adjacency = {table: set() for table in tables}

        for current_edge in hypergraph.edges:
            source = current_edge.source_table
            target = current_edge.target_table
            adjacency[source].add(target)
            adjacency[target].add(source)

        edge_key = frozenset({
            edge.source_table,
            edge.target_table,
        })

        if len(tables) < 2:
            return 0.0

        total = 0.0

        for i, source in enumerate(tables):
            for target in tables[i + 1:]:
                paths = self._shortest_paths(
                    source, target, adjacency
                )

                if not paths:
                    continue

                number_of_shortest_paths = len(paths)

                for path in paths:
                    path_edges = {
                        frozenset({
                            path[index],
                            path[index + 1],
                        })
                        for index in range(len(path) - 1)
                    }

                    if edge_key in path_edges:
                        total += 1.0 / number_of_shortest_paths

        return total

    @staticmethod
    def _shortest_paths(
        source: str,
        target: str,
        adjacency: Dict[str, set],
    ) -> list[list[str]]:
        if source == target:
            return [[source]]

        queue = [[source]]
        shortest_paths = []
        shortest_length = None

        while queue:
            path = queue.pop(0)

            if (
                shortest_length is not None
                and len(path) > shortest_length
            ):
                continue

            current = path[-1]

            for neighbor in adjacency[current]:
                if neighbor in path:
                    continue

                new_path = path + [neighbor]

                if neighbor == target:
                    if shortest_length is None:
                        shortest_length = len(new_path)

                    if len(new_path) == shortest_length:
                        shortest_paths.append(new_path)
                else:
                    queue.append(new_path)

        return shortest_paths

    @classmethod
    def _cardinality_score(
        cls,
        cardinality: Cardinality,
    ) -> float:
        return cls.CARDINALITY_SCORES.get(cardinality, 0.0)

    def _weighted_score(
        self,
        evidence: RelationshipEvidence,
    ) -> float:
        values = evidence.to_dict()

        score = sum(
            values[name] * weight
            for name, weight in self.WEIGHTS.items()
        )

        return round(max(0.0, min(1.0, score)), 4)


def relationship_analysis_node(state: dict) -> dict:
    """LangGraph node for relationship scoring."""
    analyzer = RelationshipAnalyzer(
        semantic_profiles=state.get("semantic_profiles", {}),
        primary_keys=state.get("primary_keys", {}),
        semantic_priors=state.get("semantic_priors", {}),
    )

    results = analyzer.analyze(state["hypergraph"])
    return {"relationship_results": results}
