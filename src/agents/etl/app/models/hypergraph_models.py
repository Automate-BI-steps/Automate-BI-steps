from dataclasses import dataclass, field
from enum import Enum
from typing import List


class RelationshipType(str, Enum):
    FOREIGN_KEY = "foreign_key"


class Cardinality(str, Enum):
    ONE_TO_ONE = "1:1"
    MANY_TO_ONE = "N:1"
    ONE_TO_MANY = "1:N"
    MANY_TO_MANY = "N:N"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Hyperedge:
    source_table: str
    target_table: str

    source_columns: List[str]
    target_columns: List[str]

    relationship_type: RelationshipType
    cardinality: Cardinality = Cardinality.UNKNOWN

    constraint_name: str | None = None

    # Structural evidence; NOT a score.
    evidence: dict = field(default_factory=dict)


@dataclass
class Hypergraph:
    tables: List[str]
    edges: List[Hyperedge] = field(default_factory=list)

    def add_edge(self, edge: Hyperedge) -> None:
        self.edges.append(edge)

    def outgoing(self, table_name: str) -> List[Hyperedge]:
        return [
            edge
            for edge in self.edges
            if edge.source_table == table_name
        ]

    def incoming(self, table_name: str) -> List[Hyperedge]:
        return [
            edge
            for edge in self.edges
            if edge.target_table == table_name
        ]

    def neighbors(self, table_name: str) -> List[str]:
        neighbors = set()

        for edge in self.edges:
            if edge.source_table == table_name:
                neighbors.add(edge.target_table)

            if edge.target_table == table_name:
                neighbors.add(edge.source_table)

        return sorted(neighbors)