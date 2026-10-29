"""State global partagé par tous les agents du pipeline BI.

Chaque agent possède sa propre section et n'écrit que dans celle-ci :
    run        -> identité du run et agent en cours (orchestrateur)
    input      -> entrées communes (orchestrateur / utilisateur)
    profiling  -> Agent 1 : profilage & diagnostic qualité
    cleaning   -> Agent 2 : data quality & cleaning (lit `profiling`)
    etl        -> Agent 3 : ETL / schéma en étoile (lit `cleaning`)
    kpi        -> Agent 4 : semantic & KPI (lit `cleaning` ; voir note dans src/agents/semantic_kpi/node.py
                  sur l'écart de schéma avec la sortie de l'Agent 3)
    modeling, dashboard -> agents suivants, pas encore intégrés
    timeline   -> mémoire des passages de relais : chaque agent y ajoute ce qu'il a
                  reçu, produit et transmis à l'agent suivant

Règle : aucune DataFrame dans le state, uniquement des chemins et des
résultats sérialisables en JSON. Le state complet est persisté à chaque
passage d'un agent à l'autre (outputs/runs/<run_id>/state.json, voir
src/orchestrator.py) : un agent repris plus tard (après une validation de
l'utilisateur par exemple) retrouve exactement la mémoire du run.
"""

import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


def merge_section(left: dict | None, right: dict | None) -> dict:
    """Fusion superficielle : un nœud ne renvoie que les clés qu'il met à jour."""
    return {**(left or {}), **(right or {})}


class RunSection(TypedDict, total=False):
    run_id: str
    created_at: str
    status: str  # created | profiling | cleaning | waiting_user | verifying | done | error
    current_agent: str | None
    run_dir: str  # dossier du run : state.json, input/, profiling/, clean/, verification/


class InputSection(TypedDict, total=False):
    source_dir: str  # dossier contenant les CSV
    files: list[str]  # optionnel : restreindre à certains fichiers
    business_context: str  # optionnel : contexte métier pour le LLM
    work_dir: str  # optionnel : dossier de travail du run (tables nettoyées, contrôle)
    planner: str  # nettoyage : "rules" ou "llm"
    model: str | None  # nettoyage : modèle Groq
    impute: bool  # nettoyage : compléter les valeurs manquantes
    outliers: str  # nettoyage sans interface : keep | cap | empty | advice


class ProfilingSection(TypedDict, total=False):
    """Sortie de l'Agent 1 (voir docs/spec_agent1_profiling.md)."""

    metadata: dict[str, Any]
    tables: list[dict[str, Any]]
    relationships: list[dict[str, Any]]
    issues: list[dict[str, Any]]
    summary: dict[str, Any]
    artifacts: dict[str, str]  # chemins des rapports exportés (JSON, Markdown)


class CleaningSection(TypedDict, total=False):
    """Sortie de l'Agent 2 (voir src/agents/cleaning/README.md)."""

    status: str  # waiting_user | done | error
    run_id: str  # identifiant de la trace (outputs/cleaning/history/runs/<run_id>)
    profiling_run_id: str  # rapport de profilage utilisé en entrée
    planner: dict[str, Any]
    clean_data_dir: str
    tables: list[dict[str, Any]]  # nom, chemin, lignes avant / après
    questions: list[dict[str, Any]]  # valeurs extrêmes en attente de l'accord de l'utilisateur
    answers: dict[str, str]
    summary: dict[str, Any]
    steps: list[dict[str, Any]]  # journal des corrections
    issue_resolution: dict[str, Any]  # chaque problème du profilage -> traitement et état final
    verification: dict[str, Any]  # second profilage des tables nettoyées (score avant / après)
    artifacts: dict[str, str]


class ETLSection(TypedDict, total=False):
    """Sortie de l'Agent 3 (voir src/agents/etl/README_agent3.md)."""

    status: str  # done | error
    input_dir: str
    confirmed_fact: str | None
    selected_dimensions: list[str]
    ollama_reasoning_used: bool  # False : repli déterministe (pas de clé OLLAMA_API_KEY ou réseau indisponible)
    fact_reasoning_error: str | None
    fact_reasoning_attempts: int | None  # essais du raisonnement LLM (le wrapper réessaie, pas l'agent)
    dimension_selection_error: str | None
    plan: dict[str, Any] | None  # StarSchemaPlan.to_dict() : grain, tables, colonnes, jointures
    validation: dict[str, Any]
    tables: dict[str, Any]  # nom -> {path, rows, columns} des tables (faits + dimensions) matérialisées
    output_dir: str


class BIState(TypedDict, total=False):
    run: Annotated[RunSection, merge_section]
    input: InputSection
    profiling: Annotated[ProfilingSection, merge_section]
    cleaning: Annotated[CleaningSection, merge_section]
    etl: Annotated[ETLSection, merge_section]
    kpi: Annotated[dict[str, Any], merge_section]
    modeling: Annotated[dict[str, Any], merge_section]
    dashboard: Annotated[dict[str, Any], merge_section]
    errors: Annotated[list[dict[str, Any]], operator.add]
    timeline: Annotated[list[dict[str, Any]], operator.add]
    # Optionnel : historique pour les agents conversationnels / ReAct.
    messages: Annotated[list[AnyMessage], add_messages]
