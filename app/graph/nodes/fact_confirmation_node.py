"""Confirm the LLM-selected fact table after FactReasoner validation."""


def fact_confirmation_node(state: dict) -> dict:
    decision = state.get("fact_decision")
    if decision is None:
        raise ValueError("Cannot confirm a fact table without a valid fact decision.")
    return {"confirmed_fact": decision.selected_fact}
