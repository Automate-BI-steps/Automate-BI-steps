
"""Plan-driven physical star-schema materialization."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

import pandas as pd


class StarSchemaMaterializer:
    """Materialize fact and dimension tables from a StarSchemaPlan."""

    SUPPORTED_JOIN_TYPES = {"left", "inner", "right", "outer"}

    def materialize(
        self,
        tables_data: dict[str, pd.DataFrame],
        plan: Any,
    ) -> dict[str, Any]:
        """Materialize the plan and return tables plus validation results."""

        errors: list[str] = []
        warnings: list[str] = []
        checks: dict[str, Any] = {}
        output_tables: dict[str, pd.DataFrame] = {}

        try:
            plan_dict = self._normalize_plan(plan)
            fact_source = plan_dict.get("fact_table")
            planned_tables = plan_dict.get("tables", [])

            if not fact_source:
                raise ValueError("The plan does not define a fact_table.")

            if not isinstance(tables_data, dict):
                raise TypeError("tables_data must be a dictionary.")

            if not planned_tables:
                raise ValueError("The schema plan contains no tables.")

            errors.extend(plan_dict.get("errors", []))
            warnings.extend(plan_dict.get("warnings", []))

            # ---------------------------------------------------------
            # 1. Validate source references and column lineage.
            # ---------------------------------------------------------
            for table in planned_tables:
                table_name = table.get("name", "<unnamed>")
                source_names = table.get("source_tables", [])

                for source_name in source_names:
                    if source_name not in tables_data:
                        errors.append(
                            f"Planned table '{table_name}' references "
                            f"missing source '{source_name}'."
                        )
                    elif not isinstance(
                        tables_data[source_name], pd.DataFrame
                    ):
                        errors.append(
                            f"Source '{source_name}' is not a DataFrame."
                        )

                for column in table.get("columns", []):
                    source_name = column.get("source_table")
                    source_column = column.get("source_column")

                    if source_name not in tables_data:
                        continue

                    if not isinstance(tables_data[source_name], pd.DataFrame):
                        continue

                    if source_column not in tables_data[source_name].columns:
                        errors.append(
                            f"Column lineage for '{table_name}."
                            f"{column.get('name')}' references missing "
                            f"column '{source_name}.{source_column}'."
                        )

            if errors:
                return self._result(
                    output_tables, errors, warnings, checks
                )

            # ---------------------------------------------------------
            # 2. Materialize each physical table from the plan.
            # ---------------------------------------------------------
            for table in planned_tables:
                name = table.get("name")
                table_type = str(
                    table.get("table_type", "")
                ).lower()
                source_names = table.get("source_tables", [])
                columns = table.get("columns", [])

                if not name:
                    errors.append("A planned table has no name.")
                    continue

                if table_type not in {"fact", "dimension"}:
                    errors.append(
                        f"Table '{name}' has unsupported type "
                        f"'{table_type}'."
                    )
                    continue

                if not source_names:
                    errors.append(
                        f"Table '{name}' has no source tables."
                    )
                    continue

                try:
                    combined = self._join_sources(
                        source_names=source_names,
                        joins=table.get("joins", []),
                        tables_data=tables_data,
                    )

                    # A fact table's joins must not silently change its grain.
                    if (
                        table_type == "fact"
                        and source_names[0] == fact_source
                    ):
                        source_row_count = len(tables_data[fact_source])
                        checks[f"{name}_source_rows"] = source_row_count
                        checks[f"{name}_joined_rows"] = len(combined)
                        checks[f"{name}_row_count_preserved"] = (
                            len(combined) == source_row_count
                        )

                        if len(combined) != source_row_count:
                            raise ValueError(
                                f"Fact row count changed from "
                                f"{source_row_count} to {len(combined)}."
                            )

                    # Resolve the requested output columns from their lineage.
                    result_columns: dict[str, pd.Series] = {}

                    for column in columns:
                        output_name = column["name"]
                        source_name = column["source_table"]
                        source_column = column["source_column"]

                        internal_name = self._internal_name(
                            source_name, source_column
                        )

                        if internal_name not in combined.columns:
                            raise ValueError(
                                f"Could not resolve source column "
                                f"'{source_name}.{source_column}' for "
                                f"output '{name}.{output_name}'."
                            )

                        if output_name in result_columns:
                            raise ValueError(
                                f"Duplicate output column '{output_name}' "
                                f"in planned table '{name}'."
                            )

                        result_columns[output_name] = combined[
                            internal_name
                        ]

                    physical = pd.DataFrame(
                        result_columns,
                        index=combined.index,
                    ).reset_index(drop=True)

                    output_tables[name] = physical

                    checks[f"{name}_rows"] = len(physical)
                    checks[f"{name}_columns"] = len(physical.columns)

                    # Validate the planned physical primary key.
                    key_names = self._resolve_output_keys(
                        table.get("primary_key", []),
                        columns,
                        source_names,
                    )

                    if not key_names:
                        warnings.append(
                            f"Table '{name}' has no resolvable primary key; "
                            "key uniqueness could not be verified."
                        )
                    else:
                        missing_keys = [
                            key for key in key_names
                            if key not in physical.columns
                        ]

                        if missing_keys:
                            errors.append(
                                f"Table '{name}' is missing planned key "
                                f"columns: {missing_keys}."
                            )
                        else:
                            null_key_rows = int(
                                physical[key_names]
                                .isna()
                                .any(axis=1)
                                .sum()
                            )
                            duplicate_key_rows = int(
                                physical.duplicated(key_names).sum()
                            )

                            checks[f"{name}_key_columns"] = key_names
                            checks[f"{name}_null_key_rows"] = null_key_rows
                            checks[
                                f"{name}_duplicate_key_rows"
                            ] = duplicate_key_rows

                            if null_key_rows:
                                errors.append(
                                    f"Table '{name}' has {null_key_rows} "
                                    "rows with null primary-key values."
                                )

                            if duplicate_key_rows:
                                errors.append(
                                    f"Table '{name}' has "
                                    f"{duplicate_key_rows} duplicate rows "
                                    f"for key {key_names}."
                                )

                    if table_type == "fact":
                        checks[f"{name}_grain"] = plan_dict.get(
                            "grain", "Unspecified"
                        )

                except (
                    KeyError,
                    TypeError,
                    ValueError,
                    pd.errors.MergeError,
                ) as exc:
                    errors.append(
                        f"Failed to materialize '{name}': {exc}"
                    )

            # ---------------------------------------------------------
            # 3. Ensure the planned fact output was materialized.
            # ---------------------------------------------------------
            fact_outputs = [
                table
                for table in planned_tables
                if str(table.get("table_type", "")).lower() == "fact"
                and table.get("name") in output_tables
                and fact_source in table.get("source_tables", [])
            ]

            if not fact_outputs:
                errors.append(
                    f"No materialized fact output was found for "
                    f"source '{fact_source}'."
                )

            # ---------------------------------------------------------
            # 4. Validate relationship metadata.
            # ---------------------------------------------------------
            for table in planned_tables:
                if str(table.get("table_type", "")).lower() != "dimension":
                    continue

                dimension_name = table.get("name")
                physical_dimension = output_tables.get(dimension_name)

                for join in table.get("joins", []):
                    left = join.get("left_table")
                    right = join.get("right_table")
                    left_columns = join.get("left_columns", [])
                    right_columns = join.get("right_columns", [])
                    evidence = join.get("evidence", "")

                    if left not in tables_data or right not in tables_data:
                        errors.append(
                            f"Join for '{dimension_name}' references "
                            f"missing source '{left}' or '{right}'."
                        )
                        continue

                    if (
                        not left_columns
                        or len(left_columns) != len(right_columns)
                    ):
                        errors.append(
                            f"Join for '{dimension_name}' has invalid "
                            "join-column definitions."
                        )
                        continue

                    left_df = tables_data[left]
                    right_df = tables_data[right]

                    left_missing = [
                        col for col in left_columns
                        if col not in left_df.columns
                    ]
                    right_missing = [
                        col for col in right_columns
                        if col not in right_df.columns
                    ]

                    # IMPORTANT:
                    # propagated_order_key means the fact-side key is
                    # brought into the physical fact through an intermediate
                    # source, such as orders. It need not exist in the raw
                    # fact source DataFrame.
                    if evidence == "propagated_order_key" and left_missing:
                        if right_missing:
                            errors.append(
                                f"Propagated join for '{dimension_name}' "
                                f"references missing target columns in "
                                f"'{right}': {right_missing}."
                            )
                        else:
                            checks[
                                f"{dimension_name}_join_{left}_to_{right}"
                            ] = "propagated_key"
                        continue

                    if left_missing:
                        errors.append(
                            f"Join for '{dimension_name}' references "
                            f"missing columns in '{left}': {left_missing}."
                        )
                        continue

                    if right_missing:
                        errors.append(
                            f"Join for '{dimension_name}' references "
                            f"missing columns in '{right}': {right_missing}."
                        )
                        continue

                    checks[
                        f"{dimension_name}_join_{left}_to_{right}"
                    ] = "source_columns_verified"

                    # Check referential integrity for the declared join.
                    # Missing foreign-key matches are reported as warnings
                    # here; the dimension's own key checks remain errors.
                    target_keys = right_df[right_columns].drop_duplicates()
                    probe = left_df[left_columns].copy()
                    probe.columns = [
                        f"__key_{i}" for i in range(len(left_columns))
                    ]

                    target = target_keys.copy()
                    target.columns = probe.columns.tolist()

                    probe_index = pd.MultiIndex.from_frame(probe)
                    target_index = pd.MultiIndex.from_frame(target)

                    unmatched = int(
                        (~probe_index.isin(target_index)).sum()
                    )

                    checks[
                        f"{dimension_name}_join_{left}_to_{right}_unmatched"
                    ] = unmatched

                    if unmatched:
                        warnings.append(
                            f"Join for '{dimension_name}' has {unmatched} "
                            f"source rows without a matching target in "
                            f"'{right}'."
                        )

                    # If this is an actual dimension lookup used to build
                    # the physical dimension, ensure its primary key is
                    # represented in the resulting output.
                    if (
                        physical_dimension is not None
                        and right in table.get("source_tables", [])
                        and right_columns
                    ):
                        checks[
                            f"{dimension_name}_{right}_join_checked"
                        ] = True

            checks["materialized_table_count"] = len(output_tables)
            checks["planned_table_count"] = len(planned_tables)
            checks["materialization_complete"] = (
                len(output_tables) == len(planned_tables)
            )

        except (KeyError, TypeError, ValueError) as exc:
            errors.append(str(exc))

        return self._result(output_tables, errors, warnings, checks)

    @staticmethod
    def _normalize_plan(plan: Any) -> dict[str, Any]:
        """Accept a plan dataclass or its dictionary representation."""

        if isinstance(plan, dict):
            return plan

        if is_dataclass(plan) and not isinstance(plan, type):
            return asdict(plan)

        if hasattr(plan, "to_dict"):
            result = plan.to_dict()
            if isinstance(result, dict):
                return result

        raise TypeError(
            "plan must be a StarSchemaPlan, dataclass, or dictionary."
        )

    @staticmethod
    def _internal_name(
        source_table: str,
        source_column: str,
    ) -> str:
        return f"{source_table}::{source_column}"

    def _join_sources(
        self,
        source_names: list[str],
        joins: list[dict[str, Any]],
        tables_data: dict[str, pd.DataFrame],
    ) -> pd.DataFrame:
        """Join only the source tables used by one physical output."""

        if not source_names:
            raise ValueError("Cannot join an empty source-table list.")

        first = source_names[0]

        if first not in tables_data:
            raise ValueError(f"Missing source table '{first}'.")

        combined = self._namespace_frame(first, tables_data[first])
        included = {first}
        pending = list(joins)
        required_sources = set(source_names)

        while included != required_sources:
            progress = False

            for join in pending[:]:
                left = join.get("left_table")
                right = join.get("right_table")

                # Ignore fact-to-dimension relationship edges while building
                # a dimension, and vice versa. Only join sources that belong
                # to the current physical output.
                if left not in required_sources or right not in required_sources:
                    pending.remove(join)
                    continue

                if left in included and right not in included:
                    new_table = right
                    existing_keys = join.get("left_columns", [])
                    new_keys = join.get("right_columns", [])
                    existing_table = left
                elif right in included and left not in included:
                    new_table = left
                    existing_keys = join.get("right_columns", [])
                    new_keys = join.get("left_columns", [])
                    existing_table = right
                else:
                    continue

                if (
                    not existing_keys
                    or len(existing_keys) != len(new_keys)
                ):
                    raise ValueError(
                        f"Invalid join between '{left}' and '{right}'."
                    )

                join_type = str(join.get("join_type", "left")).lower()

                if join_type not in self.SUPPORTED_JOIN_TYPES:
                    raise ValueError(
                        f"Unsupported join type '{join_type}'."
                    )

                left_on = [
                    self._internal_name(existing_table, col)
                    for col in existing_keys
                ]
                right_on = [
                    self._internal_name(new_table, col)
                    for col in new_keys
                ]

                new_df = self._namespace_frame(
                    new_table, tables_data[new_table]
                )

                missing_existing = [
                    key for key in left_on
                    if key not in combined.columns
                ]
                missing_new = [
                    key for key in right_on
                    if key not in new_df.columns
                ]

                if missing_existing or missing_new:
                    raise ValueError(
                        f"Join between '{left}' and '{right}' references "
                        f"missing keys: existing={missing_existing}, "
                        f"new={missing_new}."
                    )

                # A lookup side must be unique to prevent row multiplication.
                if new_df[right_on].isna().any().any():
                    raise ValueError(
                        f"Join key in '{new_table}' contains null values."
                    )

                if new_df.duplicated(right_on).any():
                    raise ValueError(
                        f"Join target '{new_table}' is not unique on "
                        f"{new_keys}; the join could multiply rows."
                    )

                before = len(combined)

                combined = combined.merge(
                    new_df,
                    how=join_type,
                    left_on=left_on,
                    right_on=right_on,
                    validate="many_to_one",
                    sort=False,
                )

                if join_type == "left" and len(combined) != before:
                    raise ValueError(
                        f"Left join to '{new_table}' changed row count "
                        f"from {before} to {len(combined)}."
                    )

                included.add(new_table)
                pending.remove(join)
                progress = True
                break

            if not progress:
                missing = sorted(required_sources - included)
                raise ValueError(
                    "Cannot connect all planned source tables using "
                    f"the plan's joins. Unconnected sources: {missing}."
                )

        return combined

    def _namespace_frame(
        self,
        table_name: str,
        df: pd.DataFrame,
    ) -> pd.DataFrame:
        """Prefix source columns to prevent collisions across tables."""

        if not df.columns.is_unique:
            raise ValueError(
                f"Source table '{table_name}' has duplicate column names."
            )

        return df.rename(
            columns={
                column: self._internal_name(table_name, str(column))
                for column in df.columns
            }
        ).copy()

    @staticmethod
    def _resolve_output_keys(
        primary_key: list[str],
        columns: list[dict[str, Any]],
        source_tables: list[str],
    ) -> list[str]:
        """Map source key names to output column names."""

        resolved: list[str] = []

        for key in primary_key:
            exact = [
                column["name"]
                for column in columns
                if column.get("name") == key
            ]

            if exact:
                resolved.append(exact[0])
                continue

            matches = [
                column["name"]
                for column in columns
                if column.get("source_column") == key
                and column.get("source_table") in source_tables
            ]

            if len(matches) == 1:
                resolved.append(matches[0])

        return resolved

    @staticmethod
    def _result(
        tables: dict[str, pd.DataFrame],
        errors: list[str],
        warnings: list[str],
        checks: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "tables": tables,
            "validation": {
                "valid": not errors,
                "errors": errors,
                "warnings": list(dict.fromkeys(warnings)),
                "checks": checks,
            },
        }
