"""Lien entre le rapport de profilage (Agent 1) et le nettoyage (Agent 2).

Pour chaque problème `issues[]` du profilage, on retrouve les étapes de nettoyage qui y répondent
(outils du registre tools.TOOLS) et, si un second profilage des tables nettoyées est fourni, on vérifie
que le problème a bien disparu. Le résultat est écrit dans state["cleaning"]["issue_resolution"].
"""
from __future__ import annotations

import re

# Type de problème (spec Agent 1, ISSUE_TYPES) -> outils de nettoyage qui le traitent.
ISSUE_TOOLS: dict[str, set[str]] = {
    "encoding_error": {"fix_encoding"},
    "missing_values": {"standardize_missing", "impute_missing", "flag_missing", "drop_empty_rows", "drop_empty_columns"},
    "duplicate_rows": {"drop_duplicates", "drop_empty_rows"},
    "duplicate_keys": {"flag_duplicate_keys", "drop_duplicates"},
    "type_mismatch": {"cast_numeric", "parse_dates", "cast_boolean", "normalize_percent"},
    "mixed_types": {"cast_numeric", "parse_dates", "cast_boolean", "standardize_missing"},
    "inconsistent_format": {"parse_dates", "normalize_phone", "normalize_percent", "normalize_country",
                            "merge_category_variants", "round_money", "split_packaging"},
    "invalid_date": {"parse_dates"},
    "date_logic_violation": set(),
    "out_of_range": {"flag_negative", "cap_outliers"},
    "outlier": {"cap_outliers"},
    "whitespace": {"trim_whitespace"},
    "case_inconsistency": {"fix_case", "merge_category_variants", "normalize_keys"},
    "typo_suspected": {"merge_category_variants", "normalize_country"},
    "orphan_foreign_key": {"normalize_keys"},
    "constant_column": {"drop_empty_columns"},
    "high_cardinality": set(),
    "other": set(),
}

STATUS_LABELS = {
    "fixed": "Corrigé",
    "fixed_at_read": "Corrigé à la lecture (UTF-8)",
    "partly_fixed": "Corrigé en partie",
    "resolved": "Disparu après nettoyage",
    "kept": "Gardé (choix de l'utilisateur)",
    "flagged": "Signalé, à vérifier",
    "rejected": "Refusé par le LLM",
    "expected": "Attendu, rien à faire",
    "open": "Reste à traiter",
}
DONE = {"fixed", "fixed_at_read", "resolved", "kept", "expected"}


def _table(name: str) -> str:
    return re.sub(r"[^0-9a-zA-Z_]+", "_", str(name)).strip("_").lower() or "table"


def _renames(steps: list[dict]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for s in steps:
        if s["tool"] == "clean_column_names" and s["status"] == "applied":
            out.setdefault(s["table"], {}).update(s["params"].get("mapping", {}))
    return out


def _key(table: str, column: str | None, issue_type: str) -> tuple:
    return _table(table), column, issue_type


def resolve_issues(profiling: dict, cleaning_summary: dict, after_profiling: dict | None = None) -> dict:
    """Associe chaque problème du profilage aux étapes du nettoyage et à son état après nettoyage."""
    steps = cleaning_summary.get("steps", [])
    renames = _renames(steps)
    remaining = None
    if after_profiling is not None:
        remaining = {_key(i["table"], i["column"], i["issue_type"]) for i in after_profiling.get("issues", [])}

    items = []
    for issue in profiling.get("issues", []):
        table = _table(issue["table"])
        column = renames.get(table, {}).get(issue["column"], issue["column"])
        tools = ISSUE_TOOLS.get(issue["issue_type"], set())
        related = [s for s in steps if s["table"] == table and s["tool"] in tools
                   and (issue["column"] is None or s["column"] in (column, None))]
        statuses = {s["status"] for s in related}
        still = None if remaining is None else (table, column, issue["issue_type"]) in remaining

        if "applied" in statuses:
            status = "partly_fixed" if still else "fixed"
        elif issue["issue_type"] == "encoding_error" and still is not True:
            status = "fixed_at_read"  # l'agent de nettoyage relit avec l'encodage détecté et écrit en UTF-8
        elif "kept" in statuses:
            status = "kept"
        elif still is False:
            status = "resolved"
        elif "rejected" in statuses:
            status = "rejected"
        elif "flagged" in statuses:
            status = "flagged"
        elif issue.get("is_expected") or issue["severity"] == "info":
            status = "expected"
        else:
            status = "open"
        items.append({
            "issue_id": issue["issue_id"], "table": issue["table"], "column": issue["column"],
            "issue_type": issue["issue_type"], "severity": issue["severity"], "description": issue["description"],
            "suggested_action": issue.get("suggested_action"), "status": status, "status_label": STATUS_LABELS[status],
            "still_present": still,
            "steps": [{"step": s["step"], "tool": s["tool"], "label": s["label"], "column": s["column"],
                       "status": s["status"], "values_changed": s["values_changed"]} for s in related],
        })

    counts = {k: sum(i["status"] == k for i in items) for k in STATUS_LABELS}
    return {"items": items, "counts": {k: v for k, v in counts.items() if v},
            "handled": sum(i["status"] in DONE for i in items), "total": len(items)}
