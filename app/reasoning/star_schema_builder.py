
"""Build a proposed flattened star-schema plan from pipeline evidence."""

from collections import deque
from typing import Any

from app.models.star_schema_models import (
    SchemaColumn,
    SchemaJoin,
    SchemaTable,
    StarSchemaPlan,
)


class StarSchemaBuilder:
    """Construct a schema plan without executing SQL or modifying data."""

    ORDER_ATTRIBUTES = (
        "customerID",
        "employeeID",
        "shipperID",
        "orderDate",
        "requiredDate",
        "shippedDate",
        "freight",
    )

    INDIRECT_DIMENSION_KEYS = {
        "customers": "customerID",
        "employees": "employeeID",
        "shippers": "shipperID",
    }

    def build(self, state: dict[str, Any]) -> StarSchemaPlan:
        fact_name = state.get("confirmed_fact")
        tables_data = state.get("tables_data", {})
        hypergraph = state.get("hypergraph")
        selected = state.get("selected_dimensions", [])
        primary_keys = state.get("primary_keys", {})

        plan = StarSchemaPlan(
            fact_table=fact_name or "",
            grain=self._describe_grain(fact_name, primary_keys),
        )

        if not fact_name or fact_name not in tables_data:
            plan.errors.append(
                f"Confirmed fact table is missing: {fact_name!r}."
            )
            return plan

        if hypergraph is None:
            plan.errors.append("Hypergraph is unavailable.")
            return plan

        fact_df = tables_data[fact_name]
        fact_pk = primary_keys.get(fact_name, [])

        fact_columns = self._columns_for_table(
            fact_name, tables_data, primary_keys
        )
        fact_source_tables = [fact_name]
        fact_joins: list[SchemaJoin] = []

        # Include order-level columns in the proposed fact output through
        # the validated fact -> orders path. This is lineage metadata only;
        # actual joins must be executed by a later materialization component.
        orders_path = self._find_path(
            fact_name, "orders", hypergraph, tables_data
        )

        if orders_path and "orders" in tables_data:
            orders_df = tables_data["orders"]
            fact_source_tables.append("orders")

            for column in self.ORDER_ATTRIBUTES:
                if column not in orders_df.columns:
                    continue

                if any(item.name == column for item in fact_columns):
                    plan.warnings.append(
                        f"Skipped duplicate proposed fact column '{column}'."
                    )
                    continue

                fact_columns.append(
                    SchemaColumn(
                        name=column,
                        source_table="orders",
                        source_column=column,
                        data_type=str(orders_df[column].dtype),
                        role=(
                            "foreign_key"
                            if column.endswith("ID")
                            else "order_attribute"
                        ),
                    )
                )

            joins = self._joins_for_path(
                orders_path, hypergraph, tables_data
            )
            if len(joins) == len(orders_path) - 1:
                fact_joins.extend(joins)
            else:
                plan.errors.append(
                    "Could not validate every join in the fact-to-orders path."
                )
        else:
            plan.warnings.append(
                "No validated path to orders; order-level columns "
                "were not added to the proposed fact."
            )

        plan.tables.append(
            SchemaTable(
                name=fact_name,
                table_type="fact",
                source_tables=fact_source_tables,
                primary_key=list(fact_pk),
                columns=fact_columns,
                joins=fact_joins,
            )
        )

        for dimension_name in selected:
            if dimension_name == fact_name:
                plan.warnings.append(
                    f"Skipped fact '{dimension_name}' as a dimension."
                )
                continue

            if dimension_name not in tables_data:
                plan.errors.append(
                    f"Selected dimension '{dimension_name}' is missing."
                )
                continue

            dimension_pk = primary_keys.get(dimension_name, [])
            if not dimension_pk:
                plan.errors.append(
                    f"Dimension '{dimension_name}' has no configured primary key."
                )
                continue

            # Categories is flattened into dim_product, so it must not be
            # emitted as a second standalone dimension.
            if dimension_name == "categories":
                if any(
                    table.name == "dim_product"
                    and "categories" in table.source_tables
                    for table in plan.tables
                ):
                    continue

                plan.warnings.append(
                    "Categories was selected but could not be flattened "
                    "into dim_product."
                )
                continue

            if dimension_name == "products":
                self._build_product_dimension(
                    plan=plan,
                    fact_name=fact_name,
                    hypergraph=hypergraph,
                    tables_data=tables_data,
                    primary_keys=primary_keys,
                )
                continue

            if dimension_name in self.INDIRECT_DIMENSION_KEYS:
                self._build_order_linked_dimension(
                    plan=plan,
                    fact_name=fact_name,
                    dimension_name=dimension_name,
                    hypergraph=hypergraph,
                    tables_data=tables_data,
                    primary_keys=primary_keys,
                    orders_path=orders_path,
                    fact_columns=fact_columns,
                )
                continue

            direct_join = self._make_join(
                fact_name, dimension_name, hypergraph, tables_data
            )
            if direct_join is None:
                plan.warnings.append(
                    f"Skipped '{dimension_name}': no validated direct "
                    "fact relationship or flattening rule exists."
                )
                continue

            plan.tables.append(
                SchemaTable(
                    name=f"dim_{dimension_name}",
                    table_type="dimension",
                    source_tables=[dimension_name],
                    primary_key=list(dimension_pk),
                    columns=self._columns_for_table(
                        dimension_name, tables_data, primary_keys
                    ),
                    joins=[direct_join],
                )
            )

        return plan

    def _build_product_dimension(
        self,
        plan: StarSchemaPlan,
        fact_name: str,
        hypergraph: Any,
        tables_data: dict,
        primary_keys: dict[str, list[str]],
    ) -> None:
        if "products" not in tables_data:
            plan.errors.append("Products source table is missing.")
            return

        product_pk = primary_keys.get("products", [])
        if not product_pk:
            plan.errors.append("Products has no configured primary key.")
            return

        fact_join = self._make_join(
            fact_name, "products", hypergraph, tables_data
        )
        if fact_join is None:
            plan.errors.append(
                f"No unique validated join from '{fact_name}' to products."
            )
            return

        source_tables = ["products"]
        columns = self._columns_for_table(
            "products", tables_data, primary_keys
        )
        joins = [fact_join]

        category_path = self._find_path(
            "products", "categories", hypergraph, tables_data
        )

        if category_path and "categories" in tables_data:
            category_joins = self._joins_for_path(
                category_path, hypergraph, tables_data
            )

            if len(category_joins) == len(category_path) - 1:
                source_tables.append("categories")
                joins.extend(category_joins)

                category_df = tables_data["categories"]
                existing_names = {column.name for column in columns}

                for column in category_df.columns:
                    flattened_name = f"category_{column}"
                    if flattened_name in existing_names:
                        plan.warnings.append(
                            f"Skipped duplicate flattened column "
                            f"'{flattened_name}'."
                        )
                        continue

                    columns.append(
                        SchemaColumn(
                            name=flattened_name,
                            source_table="categories",
                            source_column=str(column),
                            data_type=str(category_df[column].dtype),
                            role=(
                                "key"
                                if column in primary_keys.get(
                                    "categories", []
                                )
                                else "attribute"
                            ),
                        )
                    )
                    existing_names.add(flattened_name)
            else:
                plan.warnings.append(
                    "Category relationship path could not be fully validated."
                )
        else:
            plan.warnings.append(
                "Categories could not be safely flattened into products."
            )

        plan.tables.append(
            SchemaTable(
                name="dim_product",
                table_type="dimension",
                source_tables=source_tables,
                primary_key=list(product_pk),
                columns=columns,
                joins=joins,
            )
        )

    def _build_order_linked_dimension(
        self,
        plan: StarSchemaPlan,
        fact_name: str,
        dimension_name: str,
        hypergraph: Any,
        tables_data: dict,
        primary_keys: dict[str, list[str]],
        orders_path: list[str] | None,
        fact_columns: list[SchemaColumn],
    ) -> None:
        key_name = self.INDIRECT_DIMENSION_KEYS[dimension_name]
        dimension_pk = primary_keys.get(dimension_name, [])

        if not orders_path:
            plan.warnings.append(
                f"Skipped '{dimension_name}': no validated path to orders."
            )
            return

        if not any(
            column.name == key_name
            and column.source_table == "orders"
            and column.source_column == key_name
            for column in fact_columns
        ):
            plan.warnings.append(
                f"Skipped '{dimension_name}': proposed fact output lacks "
                f"the order-sourced key '{key_name}'."
            )
            return

        orders_join = self._make_join(
            "orders", dimension_name, hypergraph, tables_data
        )
        if orders_join is None:
            plan.warnings.append(
                f"Skipped '{dimension_name}': orders relationship is "
                "missing or ambiguous."
            )
            return

        # This represents the future join after the order key is materialized
        # in the fact. The validator checks it using lineage plus both source
        # edges, rather than requiring the key in the original fact DataFrame.
        fact_to_dimension = SchemaJoin(
            left_table=fact_name,
            left_columns=[key_name],
            right_table=dimension_name,
            right_columns=list(orders_join.right_columns),
            evidence="propagated_order_key",
        )

        plan.tables.append(
            SchemaTable(
                name=f"dim_{dimension_name[:-1]}",
                table_type="dimension",
                source_tables=[dimension_name],
                primary_key=list(dimension_pk),
                columns=self._columns_for_table(
                    dimension_name, tables_data, primary_keys
                ),
                joins=[fact_to_dimension, orders_join],
            )
        )

    @staticmethod
    def _columns_for_table(
        table_name: str,
        tables_data: dict,
        primary_keys: dict[str, list[str]],
    ) -> list[SchemaColumn]:
        df = tables_data[table_name]
        keys = primary_keys.get(table_name, [])

        return [
            SchemaColumn(
                name=str(column),
                source_table=table_name,
                source_column=str(column),
                data_type=str(df[column].dtype),
                role="key" if column in keys else "attribute",
            )
            for column in df.columns
        ]

    @staticmethod
    def _describe_grain(
        fact_name: str | None,
        primary_keys: dict[str, list[str]],
    ) -> str:
        if not fact_name:
            return "Unspecified"

        keys = primary_keys.get(fact_name, [])
        if keys:
            return (
                f"One row per {fact_name} key "
                f"({', '.join(keys)})"
            )
        return f"Grain not configured for '{fact_name}'"

    @staticmethod
    def _find_path(
        start: str,
        end: str,
        hypergraph: Any,
        tables_data: dict,
    ) -> list[str] | None:
        """Return a unique shortest path, or None if ambiguous/unavailable."""
        if start == end:
            return [start]

        if start not in tables_data or end not in tables_data:
            return None

        adjacency: dict[str, set[str]] = {}
        for edge in hypergraph.edges:
            left = edge.source_table
            right = edge.target_table

            if (
                left in tables_data
                and right in tables_data
                and left != right
            ):
                adjacency.setdefault(left, set()).add(right)
                adjacency.setdefault(right, set()).add(left)

        queue = deque([[start]])
        shortest_paths: list[list[str]] = []
        shortest_edges: int | None = None

        while queue:
            path = queue.popleft()
            edge_count = len(path) - 1

            if shortest_edges is not None and edge_count > shortest_edges:
                break

            current = path[-1]
            if current == end:
                shortest_edges = edge_count
                shortest_paths.append(path)
                continue

            for neighbor in sorted(adjacency.get(current, set())):
                if neighbor not in path:
                    queue.append(path + [neighbor])

        if len(shortest_paths) != 1:
            return None

        return shortest_paths[0]

    @classmethod
    def _joins_for_path(
        cls,
        path: list[str],
        hypergraph: Any,
        tables_data: dict,
    ) -> list[SchemaJoin]:
        joins = []

        for left, right in zip(path, path[1:]):
            join = cls._make_join(
                left, right, hypergraph, tables_data
            )
            if join is None:
                return []
            joins.append(join)

        return joins

    @staticmethod
    def _make_join(
        left_table: str,
        right_table: str,
        hypergraph: Any,
        tables_data: dict,
    ) -> SchemaJoin | None:
        candidates = []

        for edge in hypergraph.edges:
            if (
                edge.source_table == left_table
                and edge.target_table == right_table
            ):
                candidates.append(
                    (
                        tuple(edge.source_columns),
                        tuple(edge.target_columns),
                    )
                )
            elif (
                edge.source_table == right_table
                and edge.target_table == left_table
            ):
                candidates.append(
                    (
                        tuple(edge.target_columns),
                        tuple(edge.source_columns),
                    )
                )

        unique = set(candidates)
        if len(unique) != 1:
            return None

        left_cols, right_cols = next(iter(unique))

        if not left_cols or len(left_cols) != len(right_cols):
            return None

        if left_table not in tables_data or right_table not in tables_data:
            return None

        if any(
            column not in tables_data[left_table].columns
            for column in left_cols
        ):
            return None

        if any(
            column not in tables_data[right_table].columns
            for column in right_cols
        ):
            return None

        return SchemaJoin(
            left_table=left_table,
            left_columns=list(left_cols),
            right_table=right_table,
            right_columns=list(right_cols),
            evidence="validated_hypergraph_path",
        )


def star_schema_build_node(state: dict) -> dict:
    """LangGraph node: construct a proposed physical schema plan."""
    try:
        plan = StarSchemaBuilder().build(state)
        return {
            "star_schema_plan": plan.to_dict(),
            "star_schema_build_error": None,
        }
    except Exception as exc:
        return {
            "star_schema_plan": None,
            "star_schema_build_error": str(exc),
        }
