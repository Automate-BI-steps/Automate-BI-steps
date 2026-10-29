
from collections import deque


def dimension_candidate_node(state: dict) -> dict:
    """
    Discover direct and indirect dimension candidates using BFS.

    Direct candidates are one edge away from the confirmed fact.
    Indirect candidates are reachable through two or more edges.
    The shortest path to each candidate is retained.
    """
    fact = state.get("confirmed_fact")
    hypergraph = state.get("hypergraph")

    if not fact:
        raise ValueError(
            "Cannot select dimensions without a confirmed fact."
        )

    if hypergraph is None:
        raise ValueError(
            "Cannot select dimensions without a hypergraph."
        )

    if fact not in hypergraph.tables:
        raise ValueError(
            f"Confirmed fact '{fact}' is not present in the hypergraph."
        )

    # BFS: table -> shortest path from the fact table.
    paths: dict[str, list[str]] = {fact: [fact]}
    queue = deque([fact])

    while queue:
        current = queue.popleft()

        for neighbor in hypergraph.neighbors(current):
            if neighbor not in paths:
                paths[neighbor] = paths[current] + [neighbor]
                queue.append(neighbor)

    # Exclude the fact table itself.
    paths.pop(fact, None)

    direct_candidates = sorted(
        table for table, path in paths.items()
        if len(path) == 2
    )

    indirect_candidates = sorted(
        table for table, path in paths.items()
        if len(path) > 2
    )

    all_candidates = sorted(
        set(direct_candidates) | set(indirect_candidates)
    )

    indirect_paths = {
        table: paths[table]
        for table in indirect_candidates
    }

    return {
        # Keep the existing field for compatibility.
        "dimension_candidates": all_candidates,

        # Expose direct and indirect candidates separately.
        "direct_dimension_candidates": direct_candidates,
        "indirect_dimension_candidates": indirect_candidates,

        # Store the shortest path for each indirect candidate.
        "dimension_candidate_paths": indirect_paths,
    }
