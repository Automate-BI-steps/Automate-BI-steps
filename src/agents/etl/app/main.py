
"""CLI entry point for the BiFlow LangGraph pipeline."""

from app.graph.workflow import DEFAULT_PRIMARY_KEYS, build_workflow
from app.reasoning.star_schema_materializer import StarSchemaMaterializer


# =============================================================================
# REPORT HELPERS
# =============================================================================

def _section(title: str) -> None:
    print()
    print("=" * 80)
    print(title)
    print("=" * 80)


def _print_report(state: dict) -> None:
    """Print analysis, relationship, scoring, and fact-selection results."""

    tables_data = state.get("tables_data", {})
    semantic_profiles = state.get("semantic_profiles", {})
    relationships = state.get("relationships", [])
    hypergraph = state.get("hypergraph")
    relationship_results = state.get("relationship_results", [])
    grain_results = state.get("grain_results", [])
    event_results = state.get("event_results", [])
    measure_results = state.get("measure_results", [])
    candidates = state.get("fact_candidates", [])

    # Source database
    _section("REAL DATABASE")

    for name, df in tables_data.items():
        print(
            f"{name:20} rows={len(df):6} "
            f"columns={len(df.columns):3}"
        )

    # Semantic analysis
    _section("SEMANTIC ANALYSIS")

    for name, profile in semantic_profiles.items():
        print(
            f"{name:20} -> domain={profile.domain.value:10}"
            f" type={profile.semantic_type.value:15}"
            f" concept={profile.concept:20}"
            f" score={profile.score:.4f}"
        )

    # Relationships
    _section("REAL RELATIONSHIPS")

    if not relationships:
        print("No relationships detected.")

    for rel in relationships:
        print(f"\n{rel['source_table']} -> {rel['target_table']}")
        print(f"  Source columns       : {rel['source_columns']}")
        print(f"  Target columns       : {rel['target_columns']}")
        print(f"  Cardinality          : {rel['cardinality']}")
        print(f"  FK coverage          : {rel['fk_coverage']:.4f}")
        print(f"  Target unique        : {rel['target_unique']:.4f}")
        print(
            f"  Referential integrity: "
            f"{rel['referential_integrity']:.4f}"
        )
        print(
            f"  Name similarity      : "
            f"{rel.get('name_similarity', 0.0):.4f}"
        )

    # Hypergraph
    _section("HYPERGRAPH")

    if hypergraph is None:
        print("No hypergraph available.")
    else:
        print(f"Tables : {len(hypergraph.tables)}")
        print(f"Edges  : {len(hypergraph.edges)}")

        for edge in hypergraph.edges:
            print(f"\n{edge.source_table} -> {edge.target_table}")
            print(
                f"  Columns     : "
                f"{edge.source_columns} -> {edge.target_columns}"
            )
            print(f"  Type        : {edge.relationship_type.value}")
            print(f"  Cardinality : {edge.cardinality.value}")

    # Relationship scores
    _section("RELATIONSHIP SCORES")

    for result in relationship_results:
        e = result.evidence

        print(f"\n{result.source_table} -> {result.target_table}")
        print(f"  Relationship Score   : {result.score:.4f}")
        print(f"  Semantic Role        : {e.semantic_role:.4f}")
        print(f"  Grain Interaction    : {e.grain_interaction:.4f}")
        print(f"  Hypergraph Relevance : {e.hypergraph_relevance:.4f}")
        print(f"  Cardinality Relevance: {e.cardinality_relevance:.4f}")

    # Grain scores
    _section("GRAIN SCORES")

    for result in grain_results:
        e = result.evidence

        print(f"\n{result.table_name}")
        print(f"  Grain                : {result.grain_description}")
        print(f"  Grain Score          : {result.score:.4f}")
        print(f"  Key Clarity          : {e.key_clarity:.4f}")
        print(f"  Atomicity            : {e.atomicity:.4f}")
        print(f"  Event Level          : {e.event_level:.4f}")
        print(f"  Fact-Oriented Grain  : {e.fact_oriented_grain:.4f}")

    # Business event scores
    _section("BUSINESS EVENT SCORES")

    for result in event_results:
        e = result.evidence

        print(f"\n{result.table_name}")
        print(f"  Event Role            : {result.event_description}")
        print(f"  Business Event Score  : {result.score:.4f}")
        print(f"  Semantic Event        : {e.semantic_event:.4f}")
        print(f"  Event Identity        : {e.event_identity:.4f}")
        print(f"  Temporal Evidence     : {e.temporal_evidence:.4f}")
        print(f"  Occurrence Evidence   : {e.occurrence_evidence:.4f}")

    # Measure scores
    _section("MEASURE SCORES")

    for result in measure_results:
        e = result.evidence

        print(f"\n{result.table_name}")
        print(f"  Measure Columns       : {result.measure_columns}")
        print(f"  Measure Score         : {result.score:.4f}")
        print(f"  Quantitative Evidence : {e.quantitative_evidence:.4f}")
        print(f"  Grain Alignment       : {e.grain_alignment:.4f}")
        print(f"  Semantic Amount       : {e.semantic_amount_evidence:.4f}")
        print(f"  Measure Density       : {e.measure_density:.4f}")

    # Fact candidates
    _section("FACT CANDIDATES")

    for candidate in candidates:
        print(f"\n{candidate.table_name}")
        print(f"  Grain Score        : {candidate.grain_score:.4f}")
        print(f"  Relationship Score : {candidate.relationship_score:.4f}")
        print(f"  Event Score        : {candidate.event_score:.4f}")
        print(f"  Measure Score      : {candidate.measure_score:.4f}")

    # Fact reasoning
    _section("FACT TABLE REASONING")

    if state.get("fact_reasoning_error"):
        print("ERROR: Fact reasoning failed.")
        print(f"Reason: {state['fact_reasoning_error']}")
        return

    decision = state.get("fact_decision")

    if decision is None:
        print("ERROR: No fact decision was produced.")
        return

    _section("FACT DECISION")
    print(f"Selected Fact   : {decision.selected_fact}")
    print(f"Candidate Score : {decision.candidate_score:.4f}")
    print("\nReasoning:")

    for reason in decision.reasoning:
        print(f"  - {reason}")

    _section("FACT CONFIRMED")
    print(f"Fact table: {state.get('confirmed_fact')}")
    print("The pipeline can continue to dimension selection.")

    # Pipeline summary
    summary = state.get("pipeline_summary", {})

    _section("PIPELINE SUMMARY")
    print(f"Tables analyzed        : {summary.get('tables_analyzed', 0)}")
    print(
        f"Relationships detected : "
        f"{summary.get('relationships_detected', 0)}"
    )
    print(f"Hypergraph edges       : {summary.get('hypergraph_edges', 0)}")
    print(f"Relationship scores    : {summary.get('relationship_scores', 0)}")
    print(f"Grain scores           : {summary.get('grain_scores', 0)}")
    print(f"Business event scores  : {summary.get('business_event_scores', 0)}")
    print(f"Measure scores         : {summary.get('measure_scores', 0)}")
    print(f"Fact candidates        : {summary.get('fact_candidates', 0)}")
    print(f"Fact selected          : {summary.get('fact_selected', '')}")

    candidate_score = summary.get("fact_candidate_score")

    if isinstance(candidate_score, (int, float)):
        print(f"Fact Candidate Score   : {candidate_score:.4f}")
    else:
        print(f"Fact Candidate Score   : {candidate_score}")

    print("=" * 80)


# =============================================================================
# STAR-SCHEMA VISUALIZATION
# =============================================================================

def _print_star_schema_diagram(
    plan: dict,
    materialized_tables: dict,
) -> None:
    """Display a terminal diagram based on the actual generated schema plan."""

    planned_tables = plan.get("tables", [])

    fact_tables = [
        table
        for table in planned_tables
        if str(table.get("table_type", "")).lower() == "fact"
    ]

    dimension_tables = [
        table
        for table in planned_tables
        if str(table.get("table_type", "")).lower() == "dimension"
    ]

    _section("GENERATED STAR SCHEMA - VISUAL DIAGRAM")

    if not fact_tables:
        print("No fact table is defined in the generated plan.")
        return

    if len(fact_tables) > 1:
        print(
            f"Note: {len(fact_tables)} fact tables were planned. "
            "Each fact table is listed below."
        )

    def table_summary(table: dict) -> str:
        name = table.get("name", "<unnamed>")
        df = materialized_tables.get(name)

        if df is None:
            return f"{name} [NOT MATERIALIZED]"

        return (
            f"{name} "
            f"[{len(df)} rows x {len(df.columns)} columns]"
        )

    for fact in fact_tables:
        fact_name = fact.get("name", "<unnamed>")

        print()
        print("                         +--------------------------------------+")
        print(f"                         | FACT: {fact_name:<28} |")

        fact_df = materialized_tables.get(fact_name)

        if fact_df is not None:
            print(
                f"                         | {str(len(fact_df)) + ' rows':<36} |"
            )
        else:
            print("                         | NOT MATERIALIZED                   |")

        print("                         +--------------------------------------+")
        print("                                      ^")
        print("                                      |")
        print("              +-----------------------+-----------------------+")

        # Show the planned dimensions. The arrows represent the intended
        # dimensional relationships, not a claim that every join was executed.
        if not dimension_tables:
            print("              No dimensions are defined in this plan.")
        else:
            for index, dimension in enumerate(dimension_tables):
                prefix = "              " if index == len(dimension_tables) - 1 else "              "
                print(f"{prefix}+--------------------------------------+")
                print(f"{prefix}| DIM: {dimension.get('name', '<unnamed>'):<29} |")
                print(f"{prefix}+--------------------------------------+")
                if index != len(dimension_tables) - 1:
                    print(f"{prefix}                  |")

        print()
        print("TABLE DETAILS")
        print("-" * 80)
        print(f"FACT: {table_summary(fact)}")
        print(f"GRAIN: {plan.get('grain', 'Not specified')}")

        if fact_df is not None:
            print(f"FACT COLUMNS: {', '.join(map(str, fact_df.columns))}")

        print("\nDIMENSIONS:")

        if not dimension_tables:
            print(
                "  None were defined in the plan. "
                "Check the dimension-selection and schema-building stages."
            )

        for dimension in dimension_tables:
            name = dimension.get("name", "<unnamed>")
            print(f"\n  {table_summary(dimension)}")

            df = materialized_tables.get(name)

            if df is not None:
                print(f"    Columns: {', '.join(map(str, df.columns))}")
            else:
                print("    Columns: unavailable; table was not materialized.")

        # Show join metadata from the plan, without inventing relationships.
        print("\nPLANNED JOINS:")
        joins_found = False

        for table in planned_tables:
            for join in table.get("joins", []):
                joins_found = True

                left_table = join.get("left_table", "?")
                right_table = join.get("right_table", "?")
                left_columns = join.get("left_columns", [])
                right_columns = join.get("right_columns", [])

                print(
                    f"  {left_table} ({', '.join(left_columns)}) "
                    f"--> {right_table} ({', '.join(right_columns)})"
                )

        if not joins_found:
            print("  No join metadata is available.")

    print()
    print("=" * 80)


# =============================================================================
# PHYSICAL MATERIALIZATION AND VALIDATION
# =============================================================================

def _materialize_star_schema(state: dict) -> bool:
    """Materialize, validate, and display the physical star schema."""

    _section("PHYSICAL STAR-SCHEMA MATERIALIZATION")

    tables_data = state.get("tables_data")

    if not isinstance(tables_data, dict):
        print("ERROR: Missing or invalid 'tables_data' in workflow state.")
        return False

    plan = state.get("star_schema_plan")

    if not plan:
        print(
            "ERROR: The workflow did not generate a star-schema plan. "
            f"Build error: {state.get('star_schema_build_error')}"
        )
        return False

    if not isinstance(plan, dict):
        if hasattr(plan, "to_dict"):
            plan = plan.to_dict()
        else:
            print("ERROR: Unsupported star-schema plan format.")
            return False

    planned_tables = plan.get("tables", [])

    if not planned_tables:
        print("ERROR: The star-schema plan contains no tables.")
        return False

    try:
        result = StarSchemaMaterializer().materialize(
            tables_data=tables_data,
            plan=plan,
        )
    except Exception as exc:
        print(f"ERROR: Physical materialization failed: {exc}")
        return False

    validation = result.get("validation", {})
    materialized_tables = result.get("tables", {})

    print(f"Validation status: {validation.get('valid', False)}")

    print("\nValidation errors:")
    errors = validation.get("errors", [])

    if errors:
        for error in errors:
            print(f"  - {error}")
    else:
        print("  None")

    print("\nValidation warnings:")
    warnings = validation.get("warnings", [])

    if warnings:
        for warning in warnings:
            print(f"  - {warning}")
    else:
        print("  None")

    print("\nMaterialized tables:")

    if not materialized_tables:
        print("  No tables were materialized.")
    else:
        for name, df in materialized_tables.items():
            print(
                f"  {name:25} "
                f"rows={len(df):6} "
                f"columns={len(df.columns):3}"
            )

    print("\nMaterialized table previews:")

    for name, df in materialized_tables.items():
        print(f"\n--- {name} ---")
        print(df.head(3).to_string(index=False))

    # Print the visual diagram even when validation fails, to help debugging.
    _print_star_schema_diagram(plan, materialized_tables)

    if not validation.get("valid", False):
        print("\nFAIL: Physical star-schema validation failed.")
        return False

    # A fact-only output is not considered a complete star schema.
    dimension_plans = [
        table for table in planned_tables
        if str(table.get("table_type", "")).lower() == "dimension"
    ]

    if not dimension_plans:
        print(
            "\nFAIL: The plan contains no dimension tables. "
            "Check dimension selection and schema building."
        )
        return False

    missing_dimensions = [
        table.get("name", "<unnamed>")
        for table in dimension_plans
        if table.get("name") not in materialized_tables
    ]

    if missing_dimensions:
        print(
            "\nFAIL: Some planned dimensions were not materialized: "
            + ", ".join(missing_dimensions)
        )
        return False

    # Confirm that a planned fact table was materialized.
    expected_fact_source = plan.get("fact_table")

    fact_candidates = [
        table
        for table in planned_tables
        if str(table.get("table_type", "")).lower() == "fact"
        and table.get("name") in materialized_tables
        and expected_fact_source in table.get("source_tables", [])
    ]

    if not fact_candidates:
        print(
            "\nFAIL: No materialized fact output was found for source "
            f"'{expected_fact_source}'."
        )
        return False

    fact = fact_candidates[0]
    fact_name = fact["name"]
    fact_df = materialized_tables[fact_name]

    print("\nFACT TABLE VERIFICATION")
    print(f"  Fact source : {expected_fact_source}")
    print(f"  Output name : {fact_name}")
    print(f"  Row count   : {len(fact_df)}")
    print(f"  Column count: {len(fact_df.columns)}")

    print(
        "\nPASS: Physical star-schema materialization "
        "and validation succeeded."
    )

    return True


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    initial_state = {
        "input_dir": "data/input",
        "primary_keys": DEFAULT_PRIMARY_KEYS,
        # Ollama Cloud model. The reasoning clients must use an
        # OpenAI-compatible client for this remote base URL.
        "reasoning_model": "gemma4:31b-cloud",
        "ollama_base_url": "https://ollama.com/v1",
        "reasoning_timeout": 120,
    }

    _section("BIFLOW ETL AGENT - LANGGRAPH PIPELINE")

    try:
        result = build_workflow().invoke(initial_state)
    except Exception as exc:
        print(f"ERROR: LangGraph pipeline failed: {exc}")
        return

    # Display the analysis and reasoning report.
    _print_report(result)

    # Materialize and visualize the generated star schema.
    success = _materialize_star_schema(result)

    _section("FINAL EXECUTION STATUS")

    if success:
        print(
            "SUCCESS: ETL workflow completed through "
            "physical star-schema materialization."
        )
    else:
        print("FAILED: ETL workflow did not complete successfully.")

    print("=" * 80)


if __name__ == "__main__":
    main()
