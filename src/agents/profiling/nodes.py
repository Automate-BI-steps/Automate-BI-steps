"""Nœuds LangGraph de l'Agent 1.

Chaque nœud lit le state et ne renvoie que les clés de `profiling` qu'il met à
jour (fusionnées par le reducer `merge_section`). Les DataFrames ne sont jamais
stockées dans le state : chaque nœud relit les CSV à partir des métadonnées.
"""

from collections import Counter
from datetime import datetime
from pathlib import Path

from src.config import GROQ_MODEL, OUTPUT_DIR, get_llm
from src.state import BIState

from . import issues as rules
from . import llm as llm_review
from .column_profiler import profile_table
from .readers import inspect_file, read_table
from .relationships import detect_relationships
from .report import write_json, write_markdown

SPEC_VERSION = "0.1"
SEVERITY_ORDER = {s: i for i, s in enumerate(rules.SEVERITIES)}
SEVERITY_PENALTY = {"critical": 25, "high": 10, "medium": 5, "low": 2, "info": 0}


def _error(node: str, exc: Exception, table: str | None = None) -> dict:
    return {"agent": "profiling", "node": node, "table": table,
            "error": f"{type(exc).__name__}: {exc}"}


def _readable(metadata: dict) -> list[dict]:
    return [f for f in metadata["files"] if f["read_status"] != "error"]


def _frames(metadata: dict) -> dict:
    return {f["table_name"]: read_table(f) for f in _readable(metadata)}


# --------------------------------------------------------------------------
# 1. Chargement des fichiers
# --------------------------------------------------------------------------

def load_files(state: BIState) -> dict:
    inp = state.get("input", {})
    source_dir = Path(inp["source_dir"])
    paths = [source_dir / f for f in inp["files"]] if inp.get("files") \
        else sorted(source_dir.glob("*.csv"))
    llm = get_llm()
    metadata = {
        "run_id": f"prof_{datetime.now():%Y-%m-%d_%H%M%S}",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_dir": str(source_dir),
        "spec_version": SPEC_VERSION,
        "llm": {"enabled": llm is not None, "model": GROQ_MODEL if llm else None,
                "status": "pending" if llm else "no GROQ_API_KEY"},
        "files": [inspect_file(p) for p in paths],
    }
    errors = [{"agent": "profiling", "node": "load_files", "table": f["table_name"],
               "error": f["read_message"]} for f in metadata["files"]
              if f["read_status"] == "error"]
    if not paths:
        errors.append({"agent": "profiling", "node": "load_files", "table": None,
                       "error": f"Aucun fichier CSV trouvé dans {source_dir}"})
    return {"profiling": {"metadata": metadata}, "errors": errors}


def has_readable_files(state: BIState) -> str:
    return "profile" if _readable(state["profiling"]["metadata"]) else "empty"


# --------------------------------------------------------------------------
# 2. Profilage des tables et colonnes
# --------------------------------------------------------------------------

def profile_tables(state: BIState) -> dict:
    metadata = state["profiling"]["metadata"]
    tables, errors = [], []
    for meta in _readable(metadata):
        try:
            tables.append(profile_table(meta, read_table(meta)))
        except Exception as exc:  # noqa: BLE001
            errors.append(_error("profile_tables", exc, meta["table_name"]))
    return {"profiling": {"tables": tables}, "errors": errors}


# --------------------------------------------------------------------------
# 3. Relations entre tables
# --------------------------------------------------------------------------

def find_relationships(state: BIState) -> dict:
    tables = state["profiling"]["tables"]
    frames = _frames(state["profiling"]["metadata"])
    # detect_relationships marque les colonnes clés étrangères dans `tables`.
    relationships = detect_relationships(tables, frames)
    return {"profiling": {"tables": tables, "relationships": relationships}}


# --------------------------------------------------------------------------
# 4. Détection des problèmes de qualité (règles déterministes)
# --------------------------------------------------------------------------

def detect_issues(state: BIState) -> dict:
    prof = state["profiling"]
    frames = _frames(prof["metadata"])
    tables = {t["table_name"]: t for t in prof["tables"]}
    self_refs = {(r["from_table"], r["from_column"]) for r in prof["relationships"]
                 if r["from_table"] == r["to_table"]}

    found, errors = [], []
    for meta in prof["metadata"]["files"]:
        found += rules.file_issues(meta, tables.get(meta["table_name"]))
    for name, table in tables.items():
        try:
            df = frames[name]
            own_self_refs = {c for t, c in self_refs if t == name}
            found += rules.table_issues(table, df)
            found += rules.column_issues(table, df, own_self_refs)
        except Exception as exc:  # noqa: BLE001
            errors.append(_error("detect_issues", exc, name))
    found += rules.orphan_issues(prof["relationships"],
                                 {n: t["row_count"] for n, t in tables.items()})
    return {"profiling": {"issues": found}, "errors": errors}


# --------------------------------------------------------------------------
# 5. Revue sémantique par LLM (optionnelle)
# --------------------------------------------------------------------------

def llm_review_node(state: BIState) -> dict:
    prof = state["profiling"]
    llm = get_llm()
    metadata = {**prof["metadata"], "llm": dict(prof["metadata"]["llm"])}
    if llm is None:
        return {}

    context = state.get("input", {}).get("business_context")
    frames = _frames(prof["metadata"])
    tables, found = prof["tables"], list(prof["issues"])
    errors, reviewed = [], 0
    for table in tables:
        name = table["table_name"]
        try:
            review = llm_review.review_table(llm, table, frames[name], context)
            found += llm_review.apply_review(review, table, frames[name], found)
            reviewed += 1
        except Exception as exc:  # noqa: BLE001 - le LLM ne doit jamais bloquer le profilage
            errors.append(_error("llm_review", exc, name))
    metadata["llm"]["status"] = f"ok ({reviewed}/{len(tables)} tables revues)" \
        if not errors else f"partiel ({reviewed}/{len(tables)} tables revues)"
    return {"profiling": {"metadata": metadata, "tables": tables, "issues": found},
            "errors": errors}


# --------------------------------------------------------------------------
# 6. Synthèse : identifiants, scores, résumé
# --------------------------------------------------------------------------

def _finalize_issues(found: list[dict]) -> list[dict]:
    found = sorted(found, key=lambda i: (SEVERITY_ORDER[i["severity"]], i["table"],
                                         i["column"] or "", i["issue_type"]))
    for n, issue in enumerate(found, start=1):
        issue["issue_id"] = f"ISS-{n:03d}"
    # Relie le problème d'encodage du fichier aux colonnes concernées.
    by_table = {}
    for issue in found:
        if issue["issue_type"] == "encoding_error":
            by_table.setdefault(issue["table"], []).append(issue)
    for group in by_table.values():
        for issue in group:
            issue["related_issues"] = [i["issue_id"] for i in group if i is not issue]
    return found


def _quality_score(table_issues: list[dict]) -> float:
    penalty = sum(SEVERITY_PENALTY[i["severity"]] for i in table_issues if not i["is_expected"])
    return float(max(0, 100 - penalty))


def summarize(state: BIState) -> dict:
    prof = state["profiling"]
    tables = prof.get("tables", [])
    found = _finalize_issues(prof.get("issues", []))
    scores = {t["table_name"]: _quality_score([i for i in found if i["table"] == t["table_name"]])
              for t in tables}
    total_rows = sum(t["row_count"] for t in tables)
    global_score = round(sum(scores[t["table_name"]] * t["row_count"] for t in tables)
                         / total_rows, 1) if total_rows else 0.0
    blocking = any(f["read_status"] == "error" for f in prof["metadata"]["files"]) \
        or not tables

    summary = {
        "tables_count": len(tables),
        "total_rows": total_rows,
        "issues_count": len(found),
        "issues_by_severity": {s: sum(i["severity"] == s for i in found)
                               for s in rules.SEVERITIES},
        "issues_by_type": dict(Counter(i["issue_type"] for i in found).most_common()),
        "quality_score_by_table": scores,
        "global_quality_score": global_score,
        "quality_score_method": "100 - pénalités (critical 25, high 10, medium 5, low 2) "
                                "hors problèmes attendus ; global pondéré par le nombre de lignes",
        "ready_for_cleaning": not blocking,
    }

    errors, llm = [], get_llm()
    narrative = None
    if llm is not None and found:
        try:
            narrative = llm_review.write_narrative(llm, summary, found)
        except Exception as exc:  # noqa: BLE001
            errors.append(_error("summarize", exc))
    summary["narrative"] = narrative or (
        f"{len(tables)} table(s) analysée(s), {total_rows} lignes, {len(found)} problème(s) "
        f"détecté(s) dont {summary['issues_by_severity']['critical']} critique(s) et "
        f"{summary['issues_by_severity']['high']} élevé(s). "
        f"Score global de qualité : {global_score}/100."
    )
    return {"profiling": {"issues": found, "summary": summary}, "errors": errors}


# --------------------------------------------------------------------------
# 7. Export des rapports
# --------------------------------------------------------------------------

def export_report(state: BIState) -> dict:
    prof = dict(state["profiling"])
    out_dir = OUTPUT_DIR / "profiling"
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "json_report": str(out_dir / "profiling_report.json"),
        "markdown_report": str(out_dir / "profiling_report.md"),
    }
    prof.setdefault("tables", [])
    prof.setdefault("relationships", [])
    prof.setdefault("issues", [])
    prof["artifacts"] = artifacts
    write_json(prof, Path(artifacts["json_report"]))
    write_markdown(prof, Path(artifacts["markdown_report"]))
    return {"profiling": {"artifacts": artifacts}}
