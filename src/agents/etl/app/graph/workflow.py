
"""Complete LangGraph workflow preserving the original analytical stage order."""

from langgraph.graph import END, START, StateGraph

from app.graph.state import BiFlowState
from app.graph.nodes.data_profiler_node import load_database_node
from app.graph.nodes.semantic_node import semantic_analysis_node
from app.graph.nodes.foreign_key_node import foreign_key_inference_node
from app.graph.nodes.hypergraph_node import hypergraph_build_node
from app.graph.nodes.relationship_node import relationship_analysis_node
from app.graph.nodes.grain_node import grain_analysis_node
from app.graph.nodes.event_node import business_event_analysis_node
from app.graph.nodes.measure_node import measure_analysis_node
from app.graph.nodes.relationship_score_node import (
    relationship_score_aggregation_node,
)
from app.graph.nodes.fact_candidate_node import fact_candidate_node
from app.graph.nodes.fact_reasoning_node import fact_reasoning_node
from app.graph.nodes.fact_confirmation_node import fact_confirmation_node
from app.graph.nodes.dimension_candidate_node import (
    dimension_candidate_node,
)
from app.graph.nodes.dimension_reasoning_node import (
    dimension_reasoning_node,
)
from app.graph.nodes.summary_node import pipeline_summary_node

from app.reasoning.star_schema_builder import (
    star_schema_build_node,
)
from app.validation.star_schema_validator import (
    star_schema_validation_node,
)


DEFAULT_PRIMARY_KEYS = {
    "categories": ["categoryID"],
    "customers": ["customerID"],
    "employees": ["employeeID"],
    "orders": ["orderID"],
    "order_details": ["orderID", "productID"],
    "products": ["productID"],
    "shippers": ["shipperID"],
}


def _after_reasoning(state: BiFlowState) -> str:
    """Route according to the success or failure of fact reasoning."""
    if state.get("fact_reasoning_error"):
        return "stop_on_reasoning_error"

    return "confirm_fact"


def build_workflow():
    """Build and compile the complete BiFlow LangGraph workflow."""
    graph = StateGraph(BiFlowState)

    # 1. Register existing analytical nodes.
    graph.add_node("load_database", load_database_node)
    graph.add_node("semantic_analysis", semantic_analysis_node)
    graph.add_node("foreign_key_inference", foreign_key_inference_node)
    graph.add_node("hypergraph_build", hypergraph_build_node)
    graph.add_node("relationship_analysis", relationship_analysis_node)
    graph.add_node("grain_analysis", grain_analysis_node)
    graph.add_node("business_event_analysis", business_event_analysis_node)
    graph.add_node("measure_analysis", measure_analysis_node)

    graph.add_node(
        "relationship_score_aggregation",
        relationship_score_aggregation_node,
    )
    graph.add_node("fact_candidate_build", fact_candidate_node)
    graph.add_node("fact_reasoning", fact_reasoning_node)
    graph.add_node("fact_confirmation", fact_confirmation_node)

    # 2. Register dimension-selection nodes.
    graph.add_node(
        "dimension_candidate_selection",
        dimension_candidate_node,
    )
    graph.add_node(
        "dimension_reasoning",
        dimension_reasoning_node,
    )

    # 3. Register physical star-schema construction and validation.
    graph.add_node(
        "star_schema_build",
        star_schema_build_node,
    )
    graph.add_node(
        "star_schema_validation",
        star_schema_validation_node,
    )

    graph.add_node("pipeline_summary", pipeline_summary_node)

    # 4. Preserve the original analytical execution order.
    graph.add_edge(START, "load_database")
    graph.add_edge("load_database", "semantic_analysis")
    graph.add_edge("semantic_analysis", "foreign_key_inference")
    graph.add_edge("foreign_key_inference", "hypergraph_build")
    graph.add_edge("hypergraph_build", "relationship_analysis")
    graph.add_edge("relationship_analysis", "grain_analysis")
    graph.add_edge("grain_analysis", "business_event_analysis")
    graph.add_edge("business_event_analysis", "measure_analysis")

    graph.add_edge(
        "measure_analysis",
        "relationship_score_aggregation",
    )
    graph.add_edge(
        "relationship_score_aggregation",
        "fact_candidate_build",
    )
    graph.add_edge("fact_candidate_build", "fact_reasoning")

    # 5. Stop if fact reasoning fails.
    graph.add_conditional_edges(
        "fact_reasoning",
        _after_reasoning,
        {
            "confirm_fact": "fact_confirmation",
            "stop_on_reasoning_error": END,
        },
    )

    # 6. Confirm the fact and select dimensions.
    graph.add_edge(
        "fact_confirmation",
        "dimension_candidate_selection",
    )
    graph.add_edge(
        "dimension_candidate_selection",
        "dimension_reasoning",
    )

    # 7. Build and validate the proposed physical star schema.
    graph.add_edge(
        "dimension_reasoning",
        "star_schema_build",
    )
    graph.add_edge(
        "star_schema_build",
        "star_schema_validation",
    )

    # 8. Summarize the complete pipeline.
    graph.add_edge(
        "star_schema_validation",
        "pipeline_summary",
    )
    graph.add_edge("pipeline_summary", END)

    return graph.compile()
