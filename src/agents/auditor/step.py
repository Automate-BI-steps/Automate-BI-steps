"""Étape « auditor » du pipeline (Agent 6) : state -> CSV du contrat (adapter.py) -> Auditor de Dhouha -> state.auditor.

    state (profilage, nettoyage, ETL, KPI, dashboard) --> build_contract --> PipelineAuditor.run --> state.auditor

Utilisée par src/graph.py (nœud LangGraph) et src/orchestrator.py (commande `auditor`). Les fichiers sont écrits
dans <run_dir>/auditor/ : audit_trail.csv, lineage.csv, reconciliation.csv, xai_explanations.csv,
human_review_queue.csv et rapport_audit.pdf, le rapport final à télécharger (+ les CSV du contrat et
pipeline_context.json). xai_report.md (rapport Markdown de Dhouha) n'est gardé que si le PDF n'a pas pu être produit.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.agents.auditor.adapter import AuditorInputError, PipelineAuditor, build_contract
from src.agents.auditor.core import AuditorConfig

ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = {"audit_trail": "audit_trail.csv", "lineage": "lineage.csv", "reconciliation": "reconciliation.csv",
             "xai_explanations": "xai_explanations.csv", "human_review_queue": "human_review_queue.csv", "xai_report": "xai_report.md",
             "report_pdf": "rapport_audit.pdf"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def audit_dir(state: dict) -> Path:
    base = (state.get("run") or {}).get("run_dir") or (state.get("input") or {}).get("work_dir")
    return (Path(base) if base else ROOT / "outputs" / "run") / "auditor"


def _records(df, cols, limit):
    return [{c: ("" if v != v else v) for c, v in row.items()} for row in df[cols].head(limit).to_dict("records")]


def run_auditor_step(state: dict) -> dict:
    out = audit_dir(state)
    try:
        info = build_contract(state, out)
        aud = PipelineAuditor(out, cfg=AuditorConfig.from_env(), log=lambda _m: None)
        summary = aud.run()
    except AuditorInputError as exc:
        return _error(str(exc))
    except Exception as exc:  # noqa: BLE001 - l'erreur est remontée dans le state
        return _error(f"{type(exc).__name__}: {exc}")

    warnings = list(info["warnings"])
    try:  # rapport final en PDF (téléchargé depuis l'appli, pas affiché) ; le Markdown devient inutile
        from src.agents.auditor.pdf_report import build_pdf

        build_pdf(aud, state, out / ARTIFACTS["report_pdf"])
        (out / ARTIFACTS["xai_report"]).unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001 - reportlab absent ou erreur de mise en page : le Markdown reste
        warnings.append(f"Rapport PDF non produit ({type(exc).__name__}: {exc}) ; rapport Markdown xai_report.md conservé.")

    recon = summary["reconciliation"]
    mismatches = int(recon.get("MISMATCH", 0))
    matched = int(recon.get("MATCH", 0))
    section = {
        "status": "WARN" if mismatches else "OK",
        "summary": {**summary, "kpis": info["kpis"], "visuals": info["visuals"], "cleaning_events": info["events"]},
        "artifacts": {k: str(out / v) for k, v in ARTIFACTS.items() if (out / v).exists()},
        "limitations": info["limitations"],
        "warnings": warnings,
        "reconciliation": _records(aud.recon, ["kpi_id", "scope_type", "scope_value", "period", "published", "recomputed", "status"], 80),
        "explanations": _records(aud.xai, ["question_type", "subject_id", "question", "answer", "status", "evidence_ids"], 40),
        "review_queue": _records(aud.queue, ["event_id", "column", "rule_id", "approval_status", "priority", "reason"], 30),
        "lineage": _records(aud.lineage, ["visual_title", "kpi_id", "kpi_name", "cleaning_rules", "profiler_issues"], 40),
    }
    msg = (f"Audit : {summary['events']} événements tracés, {matched} chiffre(s) recalculé(s) indépendamment qui concordent"
           + (f", {mismatches} écart(s)" if mismatches else "") + f", {summary['pending_review']} point(s) en attente de validation humaine. "
           f"Explications : {summary['model']}. Rapport : {next((out / ARTIFACTS[k] for k in ('report_pdf', 'xai_report') if (out / ARTIFACTS[k]).exists()), out)}.")
    return {"auditor": section, "timeline": [{"at": _now(), "agent": "auditor", "event": "done", "summary": msg}]}


def _error(message: str) -> dict:
    return {"auditor": {"status": "ERROR", "errors": [message], "artifacts": {}},
            "timeline": [{"at": _now(), "agent": "auditor", "event": "error", "summary": message}],
            "errors": [{"agent": "auditor", "node": "auditor", "table": None, "error": message}]}


def auditor_graph_node(state: dict) -> dict:
    return run_auditor_step(state)
