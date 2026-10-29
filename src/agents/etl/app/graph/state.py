from typing import Any, TypedDict
import pandas as pd


class BiFlowState(TypedDict, total=False):
    """Shared state for the BiFlow LangGraph pipeline.

    Keys are added as their corresponding existing stages are migrated.
    The state stores stage outputs; it does not contain analytical logic.
    """
    input_dir: str
    pipeline_summary: dict[str, Any]
    confirmed_fact: str
    fact_reasoning_error: str | None
    reasoning_timeout: int
    ollama_base_url: str
    reasoning_model: str
    tables_data: dict[str, pd.DataFrame]
    table_names: list[str]
    semantic_profiles: dict[str, Any]
    primary_keys: dict[str, list[str]]
    relationships: list[dict[str, Any]]
    hypergraph: Any
    relationship_results: list[Any]
    relationship_table_scores: dict[str, float]
    grain_results: list[Any]
    event_results: list[Any]
    measure_results: list[Any]
    fact_candidates: list[Any]
    fact_decision: Any
    dimension_candidates: list[str]
    selected_dimensions: list[str]
    dimension_selection_error: str | None
    dimension_selection_reasoning: list[str]
    
    # Direct and indirect dimension candidates
    direct_dimension_candidates: list[str]
    indirect_dimension_candidates: list[str]
    dimension_candidate_paths: dict[str, list[str]]
    dimension_selection_details: list[dict[str, Any]]

    

    star_schema_plan: dict[str, Any] | None
    star_schema_build_error: str | None
    star_schema_validation: dict[str, Any]
    star_schema_validation_error: str | None
