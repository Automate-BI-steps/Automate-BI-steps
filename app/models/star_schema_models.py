"""Structured models for a physical star-schema plan."""

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SchemaColumn:
    name: str
    source_table: str
    source_column: str
    data_type: str
    role: str = "attribute"


@dataclass
class SchemaJoin:
    left_table: str
    left_columns: list[str]
    right_table: str
    right_columns: list[str]
    join_type: str = "left"
    evidence: str = "inferred_relationship"


@dataclass
class SchemaTable:
    name: str
    table_type: str
    source_tables: list[str] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    columns: list[SchemaColumn] = field(default_factory=list)
    joins: list[SchemaJoin] = field(default_factory=list)


@dataclass
class StarSchemaPlan:
    fact_table: str
    grain: str
    tables: list[SchemaTable] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)