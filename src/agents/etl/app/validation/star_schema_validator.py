
"""Validate a proposed flattened star-schema plan."""

from typing import Any


class StarSchemaValidator:
    """Validate schema structure, column lineage and source relationships."""

    def validate(self, state: dict[str, Any]) -> dict[str, Any]:
        plan = state.get("star_schema_plan")
        tables_data = state.get("tables_data", {})
        hypergraph = state.get("hypergraph")
        primary_keys = state.get("primary_keys", {})

        errors: list[str] = []
        warnings: list[str] = []

        if not isinstance(plan, dict):
            return {
                "valid": False,
                "errors": [
                    "Star-schema plan is missing or is not a dictionary."
                ],
                "warnings": [],
            }

        fact_name = plan.get("fact_table")
        schema_tables = plan.get("tables", [])

        if not fact_name:
            errors.append("The plan does not specify a fact table.")

        if not isinstance(schema_tables, list):
            return {
                "valid": False,
                "errors": ["Plan field 'tables' must be a list."],
                "warnings": [],
            }

        facts = [
            table
            for table in schema_tables
            if isinstance(table, dict)
            and table.get("table_type") == "fact"
        ]

        if len(facts) != 1:
            errors.append(
                f"Expected exactly one fact table; found {len(facts)}."
            )

        fact_plan = facts[0] if len(facts) == 1 else None

        if fact_plan and fact_plan.get("name") != fact_name:
            errors.append(
                "The fact_table field does not match the declared fact table."
            )

        if fact_name not in tables_data:
            errors.append(
                f"Fact source table '{fact_name}' is missing from tables_data."
            )

        schema_names: set[str] = set()

        # Validate planned tables and column lineage.
        for schema_table in schema_tables:
            if not isinstance(schema_table, dict):
                errors.append("A schema table entry is not a dictionary.")
                continue

            name = schema_table.get("name")
            table_type = schema_table.get("table_type")
            columns = schema_table.get("columns", [])
            source_tables = schema_table.get("source_tables", [])
            declared_pk = schema_table.get("primary_key", [])

            if not name:
                errors.append("A schema table has no name.")
                continue

            if name in schema_names:
                errors.append(f"Duplicate schema table name '{name}'.")
            schema_names.add(name)

            if table_type not in {"fact", "dimension"}:
                errors.append(
                    f"Schema table '{name}' has invalid type '{table_type}'."
                )

            if not isinstance(source_tables, list):
                errors.append(
                    f"'{name}': source_tables must be a list."
                )
                source_tables = []

            for source in source_tables:
                if source not in tables_data:
                    errors.append(
                        f"'{name}' references missing source table '{source}'."
                    )

            if not isinstance(columns, list):
                errors.append(f"'{name}': columns must be a list.")
                continue

            planned_names: set[str] = set()

            for column in columns:
                if not isinstance(column, dict):
                    errors.append(
                        f"'{name}' contains an invalid column definition."
                    )
                    continue

                column_name = column.get("name")
                source_table = column.get("source_table")
                source_column = column.get("source_column")

                if not column_name or not source_table or not source_column:
                    errors.append(
                        f"'{name}' has a column missing name or source metadata."
                    )
                    continue

                if column_name in planned_names:
                    errors.append(
                        f"'{name}' has duplicate planned column "
                        f"'{column_name}'."
                    )
                planned_names.add(column_name)

                if source_table not in source_tables:
                    errors.append(
                        f"'{name}.{column_name}' uses source '{source_table}' "
                        "which is not listed in source_tables."
                    )
                    continue

                if source_table not in tables_data:
                    continue

                if source_column not in tables_data[source_table].columns:
                    errors.append(
                        f"'{name}.{column_name}' references nonexistent source "
                        f"column '{source_table}.{source_column}'."
                    )

            if table_type == "dimension":
                if not declared_pk:
                    warnings.append(
                        f"Dimension '{name}' has no declared primary key."
                    )

                for key in declared_pk:
                    if not any(
                        isinstance(column, dict)
                        and column.get("source_column") == key
                        and column.get("role") == "key"
                        for column in columns
                    ):
                        errors.append(
                            f"Dimension '{name}' primary key '{key}' is not "
                            "represented as a key column."
                        )

        # Validate joins once, including propagated order-key lineage.
        if hypergraph is None:
            errors.append(
                "Hypergraph is unavailable; joins cannot be verified."
            )
        else:
            fact_columns = (
                fact_plan.get("columns", []) if fact_plan else []
            )

            for schema_table in schema_tables:
                if not isinstance(schema_table, dict):
                    continue

                schema_name = schema_table.get("name", "<unnamed>")
                joins = schema_table.get("joins", [])

                if not isinstance(joins, list):
                    errors.append(f"'{schema_name}': joins must be a list.")
                    continue

                for join in joins:
                    if not isinstance(join, dict):
                        errors.append(
                            f"'{schema_name}' has an invalid join entry."
                        )
                        continue

                    left_table = join.get("left_table")
                    right_table = join.get("right_table")
                    left_cols = join.get("left_columns", [])
                    right_cols = join.get("right_columns", [])
                    evidence = join.get("evidence")

                    if (
                        not left_table
                        or not right_table
                        or not isinstance(left_cols, list)
                        or not isinstance(right_cols, list)
                        or not left_cols
                        or len(left_cols) != len(right_cols)
                    ):
                        errors.append(
                            f"'{schema_name}' has an invalid join definition."
                        )
                        continue

                    if evidence == "propagated_order_key":
                        # The fact-side key comes from orders, not from the
                        # original fact DataFrame. Check its declared lineage.
                        if left_table != fact_name:
                            errors.append(
                                f"'{schema_name}': propagated join must start "
                                f"from fact '{fact_name}'."
                            )
                            continue

                        lineage_valid = all(
                            any(
                                isinstance(column, dict)
                                and column.get("name") == left_col
                                and column.get("source_table") == "orders"
                                and column.get("source_column") == left_col
                                for column in fact_columns
                            )
                            for left_col in left_cols
                        )

                        if not lineage_valid:
                            errors.append(
                                f"'{schema_name}': propagated join keys do not "
                                "match the fact plan's source lineage."
                            )

                        fact_to_orders = self._edge_exists(
                            hypergraph,
                            fact_name,
                            ["orderID"],
                            "orders",
                            ["orderID"],
                        )

                        orders_to_dimension = self._edge_exists(
                            hypergraph,
                            "orders",
                            left_cols,
                            right_table,
                            right_cols,
                        )

                        if not fact_to_orders:
                            errors.append(
                                "The fact-to-orders relationship is missing "
                                "from the hypergraph."
                            )

                        if not orders_to_dimension:
                            errors.append(
                                f"'{schema_name}': orders-to-'{right_table}' "
                                "relationship is not supported by the hypergraph."
                            )

                        continue

                    # Ordinary source joins must reference real columns.
                    if (
                        left_table not in tables_data
                        or right_table not in tables_data
                    ):
                        errors.append(
                            f"'{schema_name}' join references unavailable "
                            f"tables '{left_table}' or '{right_table}'."
                        )
                        continue

                    if any(
                        column not in tables_data[left_table].columns
                        for column in left_cols
                    ):
                        errors.append(
                            f"Join uses nonexistent columns in '{left_table}'."
                        )
                        continue

                    if any(
                        column not in tables_data[right_table].columns
                        for column in right_cols
                    ):
                        errors.append(
                            f"Join uses nonexistent columns in '{right_table}'."
                        )
                        continue

                    if not self._edge_exists(
                        hypergraph,
                        left_table,
                        left_cols,
                        right_table,
                        right_cols,
                    ):
                        errors.append(
                            f"Join {left_table}{left_cols} -> "
                            f"{right_table}{right_cols} is not supported "
                            "by a hypergraph edge."
                        )

        # Validate fact grain against the source fact table.
        if fact_plan and fact_name in tables_data:
            fact_pk = fact_plan.get("primary_key", [])
            source_df = tables_data[fact_name]

            if not fact_pk:
                warnings.append(
                    "Fact table has no configured primary key; grain "
                    "uniqueness cannot be fully checked."
                )
            else:
                missing = [
                    key for key in fact_pk
                    if key not in source_df.columns
                ]

                if missing:
                    errors.append(
                        f"Fact primary-key columns are missing: {missing}."
                    )
                elif source_df.duplicated(subset=fact_pk).any():
                    errors.append(
                        f"Fact source '{fact_name}' is not unique at its "
                        f"declared grain {fact_pk}."
                    )

            configured_keys = primary_keys.get(fact_name, [])
            if configured_keys and list(fact_pk) != list(configured_keys):
                warnings.append(
                    "Fact plan primary key differs from configured primary_keys."
                )

        # Validate each dimension's natural key against its base source.
        for schema_table in schema_tables:
            if not isinstance(schema_table, dict):
                continue

            if schema_table.get("table_type") != "dimension":
                continue

            name = schema_table.get("name", "<unnamed>")
            sources = schema_table.get("source_tables", [])
            dimension_pk = schema_table.get("primary_key", [])

            if not sources or not dimension_pk:
                continue

            base_source = sources[0]
            if base_source not in tables_data:
                continue

            source_df = tables_data[base_source]
            missing = [
                key for key in dimension_pk
                if key not in source_df.columns
            ]

            if missing:
                errors.append(
                    f"Dimension '{name}' key columns {missing} are absent "
                    f"from base source '{base_source}'."
                )
                continue

            if source_df[dimension_pk].isna().any().any():
                errors.append(
                    f"Dimension '{name}' has null primary-key values "
                    f"in '{base_source}'."
                )

            if source_df.duplicated(subset=dimension_pk).any():
                errors.append(
                    f"Dimension '{name}' key {dimension_pk} is not unique "
                    f"in '{base_source}'."
                )

        warnings.append(
            "This validates the proposed schema and source evidence only. "
            "It does not prove physical materialization or preservation of "
            "the final joined row count."
        )

        return {
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
        }

    @staticmethod
    def _edge_exists(
        hypergraph: Any,
        left_table: str,
        left_cols: list[str],
        right_table: str,
        right_cols: list[str],
    ) -> bool:
        expected = (tuple(left_cols), tuple(right_cols))
        reverse = (tuple(right_cols), tuple(left_cols))

        for edge in hypergraph.edges:
            edge_columns = (
                tuple(edge.source_columns),
                tuple(edge.target_columns),
            )

            if (
                edge.source_table == left_table
                and edge.target_table == right_table
                and edge_columns == expected
            ):
                return True

            if (
                edge.source_table == right_table
                and edge.target_table == left_table
                and edge_columns == reverse
            ):
                return True

        return False


def star_schema_validation_node(state: dict) -> dict:
    """LangGraph node: validate the proposed star-schema plan."""
    try:
        result = StarSchemaValidator().validate(state)
        return {
            "star_schema_validation": result,
            "star_schema_validation_error": None,
        }
    except Exception as exc:
        return {
            "star_schema_validation": {
                "valid": False,
                "errors": [str(exc)],
                "warnings": [],
            },
            "star_schema_validation_error": str(exc),
        }
