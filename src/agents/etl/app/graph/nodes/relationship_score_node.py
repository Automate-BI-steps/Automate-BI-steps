"""LangGraph node for aggregating edge-level relationship scores by source table."""
from typing import Any


def aggregate_relationship_scores(

    relationship_results,

):

    """

    Convert edge-level relationship scores into

    one relationship score per source table.



    The score is the average of the outgoing

    relationship scores for that table.



    Tables without outgoing relationships receive 0.0.

    """



    scores = {}



    for result in relationship_results:



        source_table = result.source_table



        scores.setdefault(

            source_table,

            [],

        )



        scores[source_table].append(

            result.score

        )



    return {

        table_name: round(

            sum(values) / len(values),

            4,

        )

        for table_name, values in scores.items()

    }


def relationship_score_aggregation_node(state: dict) -> dict:
    """LangGraph node: average outgoing relationship scores per source table."""
    scores = aggregate_relationship_scores(state.get("relationship_results", []))
    return {"relationship_table_scores": scores}
