
"""Build the final pipeline summary from graph state."""


def pipeline_summary_node(state: dict) -> dict:
    decision = state.get("fact_decision")

    return {
        "pipeline_summary": {
            # General analysis
            "tables_analyzed": len(state.get("table_names", [])),
            "relationships_detected": len(state.get("relationships", [])),
            "hypergraph_edges": len(
                getattr(state.get("hypergraph"), "edges", [])
            ),

            # Analytical stages
            "relationship_scores": len(
                state.get("relationship_results", [])
            ),
            "grain_scores": len(state.get("grain_results", [])),
            "business_event_scores": len(state.get("event_results", [])),
            "measure_scores": len(state.get("measure_results", [])),

            # Fact-table selection
            "fact_candidates": len(state.get("fact_candidates", [])),
            "fact_selected": (
                decision.selected_fact if decision else None
            ),
            "fact_candidate_score": (
                decision.candidate_score if decision else None
            ),
            "fact_reasoning_error": state.get("fact_reasoning_error"),

            # Dimension selection
            "dimension_candidates": state.get("dimension_candidates", []),
            "dimensions_selected": state.get("selected_dimensions", []),
            "dimension_selection_reasoning": state.get(
                "dimension_selection_reasoning", []
            ),
            "dimension_selection_error": state.get(
                "dimension_selection_error"
            ),
            "direct_dimension_candidates": state.get(
                "direct_dimension_candidates", []
            ),
            "indirect_dimension_candidates": state.get(
                "indirect_dimension_candidates", []
            ),
            "dimension_candidate_paths": state.get(
                "dimension_candidate_paths", {}
            ),
            "dimension_selection_details": state.get(
                "dimension_selection_details", []
            ),
        }
    }
