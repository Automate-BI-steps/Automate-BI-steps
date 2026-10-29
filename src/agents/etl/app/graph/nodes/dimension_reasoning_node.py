
from typing import Any

from app.reasoning.dimension_reasoner import DimensionReasoner


# Dimensions supported by the current StarSchemaBuilder.
INDIRECT_DIMENSION_KEYS = {
    "customers": "customerID",
    "employees": "employeeID",
    "shippers": "shipperID",
}


def _deterministic_dimension_fallback(
    fact_table: str,
    direct_candidates: list[str],
    indirect_candidates: list[str],
    candidate_paths: dict[str, list[str]],
    tables_data: dict,
    primary_keys: dict[str, list[str]],
) -> list[str]:
    """
    Select dimensions from schema evidence when LLM reasoning fails.

    The selection is restricted to tables supported by the current builder
    and having configured primary keys.
    """
    candidates = set(direct_candidates) | set(indirect_candidates)
    selected: list[str] = []

    # Products can join directly to order_details and can absorb categories.
    if (
        "products" in candidates
        and "products" in tables_data
        and primary_keys.get("products")
    ):
        selected.append("products")

    # These dimensions are reached through orders, whose keys and
    # attributes are propagated into the proposed fact output.
    for table_name in INDIRECT_DIMENSION_KEYS:
        path = candidate_paths.get(table_name, [])

        valid_path = (
            table_name in candidates
            and bool(path)
            and path[0] == fact_table
            and path[-1] == table_name
            and "orders" in path
        )

        if (
            valid_path
            and table_name in tables_data
            and primary_keys.get(table_name)
        ):
            selected.append(table_name)

    # Support other direct dimensions when they have a configured key.
    # Avoid tables handled specially by the builder.
    excluded = {
        fact_table,
        "orders",
        "products",
        "categories",
        *INDIRECT_DIMENSION_KEYS.keys(),
    }

    for table_name in sorted(direct_candidates):
        if table_name in excluded:
            continue

        if (
            table_name in tables_data
            and primary_keys.get(table_name)
        ):
            selected.append(table_name)

    # Preserve order while removing duplicates.
    return list(dict.fromkeys(selected))


def dimension_reasoning_node(state: dict) -> dict:
    """Select dimensions with LLM reasoning and a deterministic fallback."""
    fact_table = state.get("confirmed_fact")
    hypergraph = state.get("hypergraph")
    tables_data = state.get("tables_data", {})
    primary_keys = state.get("primary_keys", {})

    direct_candidates = state.get("direct_dimension_candidates", [])
    indirect_candidates = state.get("indirect_dimension_candidates", [])
    candidate_paths = state.get("dimension_candidate_paths", {})

    if hypergraph is None:
        return {
            "selected_dimensions": [],
            "dimension_selection_details": [],
            "dimension_selection_reasoning": [],
            "dimension_selection_error": "Dimension reasoning requires a hypergraph.",
            "dimension_selection_fallback_used": False,
        }

    if not fact_table:
        return {
            "selected_dimensions": [],
            "dimension_selection_details": [],
            "dimension_selection_reasoning": [],
            "dimension_selection_error": "Dimension reasoning requires a confirmed fact.",
            "dimension_selection_fallback_used": False,
        }

    candidates = set(direct_candidates) | set(indirect_candidates)

    relationship_evidence: list[dict[str, Any]] = []
    seen_edges = set()

    for candidate in sorted(candidates):
        path = (
            [fact_table, candidate]
            if candidate in direct_candidates
            else candidate_paths.get(candidate, [])
        )

        if (
            not path
            or path[0] != fact_table
            or path[-1] != candidate
        ):
            continue

        for source, target in zip(path, path[1:]):
            for edge in hypergraph.edges:
                matches = (
                    edge.source_table == source
                    and edge.target_table == target
                ) or (
                    edge.source_table == target
                    and edge.target_table == source
                )

                if not matches:
                    continue

                edge_key = (
                    edge.source_table,
                    edge.target_table,
                    tuple(edge.source_columns),
                    tuple(edge.target_columns),
                )

                if edge_key in seen_edges:
                    continue

                seen_edges.add(edge_key)

                relationship_evidence.append({
                    "source_table": edge.source_table,
                    "target_table": edge.target_table,
                    "source_columns": edge.source_columns,
                    "target_columns": edge.target_columns,
                    "relationship_type": edge.relationship_type.value,
                    "cardinality": edge.cardinality.value,
                    "constraint_name": edge.constraint_name,
                    "evidence": edge.evidence,
                })

    original_error = None
    selected_dimensions: list[str] = []
    details: list[dict[str, Any]] = []
    reasoning = []

    try:
        reasoner = DimensionReasoner(
            model=state.get("reasoning_model", "phi4-mini-reasoning"),
            base_url=state.get(
                "ollama_base_url",
                "http://localhost:11434",
            ),
            timeout=state.get("reasoning_timeout", 300),
        )

        decision = reasoner.reason(
            fact_table=fact_table,
            direct_candidates=direct_candidates,
            indirect_candidates=indirect_candidates,
            candidate_paths=candidate_paths,
            relationship_evidence=relationship_evidence,
        )

        for selection in decision.selected_dimensions:
            table_name = selection.table_name

            # Reject names not actually discovered from the schema.
            if table_name not in candidates:
                continue

            # Reject dimensions that cannot be built from the available data.
            if (
                table_name not in tables_data
                or not primary_keys.get(table_name)
            ):
                continue

            if table_name not in selected_dimensions:
                selected_dimensions.append(table_name)
                details.append({
                    "table_name": table_name,
                    "dimension_type": selection.dimension_type,
                    "path": selection.path,
                    "reasoning": selection.reasoning,
                })

        reasoning = getattr(decision, "reasoning", [])

    except Exception as exc:
        original_error = str(exc)

    fallback_used = not selected_dimensions

    if fallback_used:
        selected_dimensions = _deterministic_dimension_fallback(
            fact_table=fact_table,
            direct_candidates=direct_candidates,
            indirect_candidates=indirect_candidates,
            candidate_paths=candidate_paths,
            tables_data=tables_data,
            primary_keys=primary_keys,
        )

        details = [
            {
                "table_name": name,
                "dimension_type": "deterministic_fallback",
                "path": (
                    [fact_table, name]
                    if name in direct_candidates
                    else candidate_paths.get(name, [])
                ),
                "reasoning": (
                    "Selected from discovered relationship paths, "
                    "available source data, and configured primary keys."
                ),
            }
            for name in selected_dimensions
        ]

        if not original_error:
            original_error = (
                "LLM returned no usable dimensions; deterministic "
                "fallback selection was applied."
            )

    return {
        "selected_dimensions": selected_dimensions,
        "dimension_selection_details": details,
        "dimension_selection_reasoning": reasoning,
        "dimension_selection_error": original_error,
        "dimension_selection_fallback_used": fallback_used,
    }
