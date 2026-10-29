"""Graphe global du pipeline BI : chaque agent est un nœud (ou un sous-graphe) branché ici.

    START -> profiling -> profiling_handoff -> cleaning -> verification -> etl -> semantic_kpi -> END

Le state (src/state.py) est la mémoire partagée : l'Agent 2 lit `profiling`
(rapport JSON + chemins des CSV) et écrit `cleaning` ; `timeline` garde la
trace de chaque passage de relais. Le backend web (api/app.py) exécute les
mêmes nœuds pas à pas via src/orchestrator.py pour pouvoir s'arrêter quand
l'utilisateur doit valider les valeurs extrêmes.

L'Agent 3 (etl) et l'Agent 4 (semantic_kpi) lisent tous les deux `cleaning.clean_data_dir` :
ils tournent donc en parallèle dans ce graphe (chacun dans son dossier, code non modifié —
voir src/agents/etl/ et src/agents/semantic_kpi/), pas l'un après l'autre.
"""

from datetime import datetime

from langgraph.graph import END, START, StateGraph

from src.agents.cleaning import cleaning_node, verification_node
from src.agents.etl.node import etl_node
from src.agents.profiling import build_profiling_graph
from src.agents.semantic_kpi.node import semantic_kpi_node
from src.state import BIState


def profiling_handoff(state: BIState) -> dict:
    """Note de passage de relais Agent 1 -> Agent 2, conservée dans la mémoire du run."""
    prof = state.get("profiling", {})
    summary = prof.get("summary", {})
    files = [f for f in prof.get("metadata", {}).get("files", []) if f.get("read_status") != "error"]
    return {"timeline": [{
        "at": datetime.now().isoformat(timespec="seconds"), "agent": "profiling", "event": "done",
        "summary": f"{summary.get('tables_count', 0)} table(s), {summary.get('issues_count', 0)} problème(s), "
                   f"score {summary.get('global_quality_score')} / 100. Transmis au nettoyage : "
                   f"{len(files)} CSV + rapport {prof.get('artifacts', {}).get('json_report')}.",
    }]}


def build_pipeline():
    graph = StateGraph(BIState)
    graph.add_node("profiling", build_profiling_graph())
    graph.add_node("profiling_handoff", profiling_handoff)
    graph.add_node("cleaning", cleaning_node)
    graph.add_node("verification", verification_node)
    graph.add_node("etl", etl_node)
    graph.add_node("semantic_kpi", semantic_kpi_node)
    graph.add_edge(START, "profiling")
    graph.add_edge("profiling", "profiling_handoff")
    graph.add_edge("profiling_handoff", "cleaning")
    graph.add_edge("cleaning", "verification")
    graph.add_edge("verification", "etl")
    graph.add_edge("verification", "semantic_kpi")
    graph.add_edge("etl", END)
    graph.add_edge("semantic_kpi", END)
    return graph.compile()
