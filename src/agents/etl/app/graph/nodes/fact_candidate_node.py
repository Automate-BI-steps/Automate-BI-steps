"""Build fact-candidate inputs from the existing analyzer outputs."""
from app.models.fact_reasoning_models import FactCandidateInput


def fact_candidate_node(state: dict) -> dict:
    grain_map = {result.table_name: result for result in state["grain_results"]}
    event_map = {result.table_name: result for result in state["event_results"]}
    measure_map = {result.table_name: result for result in state["measure_results"]}
    relationship_table_scores = state.get("relationship_table_scores", {})

    fact_candidates = []
    for table_name in state["table_names"]:
        grain = grain_map[table_name]
        event = event_map[table_name]
        measure = measure_map[table_name]
        relationship_score = relationship_table_scores.get(table_name, 0.0)

        candidate = FactCandidateInput(
            table_name=table_name,
            grain_score=grain.score,
            relationship_score=relationship_score,
            event_score=event.score,
            measure_score=measure.score,
            evidence={
                "grain": {
                    "description": grain.grain_description,
                    "key_clarity": grain.evidence.key_clarity,
                    "atomicity": grain.evidence.atomicity,
                    "event_level": grain.evidence.event_level,
                    "fact_oriented_grain": grain.evidence.fact_oriented_grain,
                },
                "relationship": {"score": relationship_score},
                "business_event": {
                    "description": event.event_description,
                    "semantic_event": event.evidence.semantic_event,
                    "event_identity": event.evidence.event_identity,
                    "temporal_evidence": event.evidence.temporal_evidence,
                    "occurrence_evidence": event.evidence.occurrence_evidence,
                },
                "measure": {
                    "columns": measure.measure_columns,
                    "quantitative_evidence": measure.evidence.quantitative_evidence,
                    "grain_alignment": measure.evidence.grain_alignment,
                    "semantic_amount_evidence": measure.evidence.semantic_amount_evidence,
                    "measure_density": measure.evidence.measure_density,
                },
            },
        )
        fact_candidates.append(candidate)

    return {"fact_candidates": fact_candidates}
