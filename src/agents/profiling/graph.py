"""Graphe LangGraph de l'Agent 1 : profilage & diagnostic qualité.

    START -> load_files -┬-> profile_tables -> find_relationships -> detect_issues
                         │        -> llm_review -> summarize -> export_report -> END
                         └-(aucun fichier lisible)-> summarize
"""

from langgraph.graph import END, START, StateGraph

from src.state import BIState

from . import nodes


def build_profiling_graph():
    graph = StateGraph(BIState)
    graph.add_node("load_files", nodes.load_files)
    graph.add_node("profile_tables", nodes.profile_tables)
    graph.add_node("find_relationships", nodes.find_relationships)
    graph.add_node("detect_issues", nodes.detect_issues)
    graph.add_node("llm_review", nodes.llm_review_node)
    graph.add_node("summarize", nodes.summarize)
    graph.add_node("export_report", nodes.export_report)

    graph.add_edge(START, "load_files")
    graph.add_conditional_edges("load_files", nodes.has_readable_files,
                                {"profile": "profile_tables", "empty": "summarize"})
    graph.add_edge("profile_tables", "find_relationships")
    graph.add_edge("find_relationships", "detect_issues")
    graph.add_edge("detect_issues", "llm_review")
    graph.add_edge("llm_review", "summarize")
    graph.add_edge("summarize", "export_report")
    graph.add_edge("export_report", END)
    return graph.compile()
