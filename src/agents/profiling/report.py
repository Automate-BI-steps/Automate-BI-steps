"""Export du rapport de profilage : JSON (pour l'Agent 2) et Markdown (lecture humaine)."""

import json
from pathlib import Path

SEVERITY_ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}


def write_json(profiling: dict, path: Path) -> None:
    path.write_text(json.dumps(profiling, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(headers: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def write_markdown(profiling: dict, path: Path) -> None:
    """Les sections reprennent les noms et l'ordre de la spec (section `profiling`)."""
    meta, summary = profiling["metadata"], profiling["summary"]
    out = [
        "# Rapport de profilage — Agent 1",
        "",
        "## metadata",
        "",
        f"- **run_id** : `{meta['run_id']}`",
        f"- **generated_at** : {meta['generated_at']}",
        f"- **source_dir** : `{meta['source_dir']}`",
        f"- **spec_version** : {meta['spec_version']}",
        f"- **llm** : {meta['llm']['model'] if meta['llm']['enabled'] else 'désactivé'}"
        f" ({meta['llm']['status']})",
        "",
        "### files",
        "",
        _table(["Fichier", "Taille (octets)", "Encodage", "Séparateur", "En-tête", "Statut"],
               [[f["file_name"], f["size_bytes"], f["encoding"], repr(f["delimiter"]),
                 f["has_header"], f["read_status"]] for f in meta["files"]]),
        "",
        "## tables",
    ]
    for t in profiling["tables"]:
        out += [
            "",
            f"### `{t['table_name']}` — {t['row_count']} lignes × {t['column_count']} colonnes",
            "",
            f"- Clés primaires candidates : {t['primary_key_candidates'] or 'aucune'}",
            f"- Lignes dupliquées : {t['duplicate_row_count']}",
            "",
            _table(
                ["Colonne", "Type détecté", "Type attendu", "Rôle", "Manquants %",
                 "Distinctes", "Min", "Max", "Exemples"],
                [[c["column_name"], c["detected_type"], c["expected_type"],
                  c["semantic_role"], c["null_pct"], c["distinct_count"],
                  c.get("min", c.get("min_date")), c.get("max", c.get("max_date")),
                  ", ".join(c["sample_values"][:3])] for c in t["columns"]],
            ),
        ]
    out += [
        "",
        "## relationships",
        "",
        _table(["ID", "De", "Vers", "Cardinalité", "Correspondance %", "Orphelins",
                "Méthode", "Confiance"],
               [[r["relationship_id"], f"{r['from_table']}.{r['from_column']}",
                 f"{r['to_table']}.{r['to_column']}", r["cardinality"], r["match_pct"],
                 r["orphan_count"], r["detection_method"], r["confidence"]]
                for r in profiling["relationships"]]),
        "",
        "## issues",
        "",
        _table(["ID", "Sévérité", "Table", "Colonne", "Type", "Lignes (%)", "Description",
                "Exemples", "Action suggérée", "Attendu", "Source"],
               [[i["issue_id"], f"{SEVERITY_ICON[i['severity']]} {i['severity']}", i["table"],
                 i["column"], i["issue_type"], f"{i['affected_rows']} ({i['affected_pct']} %)",
                 i["description"], i["examples"][:3], i["suggested_action"],
                 "oui" if i["is_expected"] else "non", i["detected_by"]]
                for i in profiling["issues"]]),
        "",
        "## summary",
        "",
        summary.get("narrative", ""),
        "",
        f"- Tables : **{summary['tables_count']}** — lignes : **{summary['total_rows']}**",
        f"- Problèmes détectés : **{summary['issues_count']}** "
        + " · ".join(f"{SEVERITY_ICON[s]} {s} : {n}"
                     for s, n in summary["issues_by_severity"].items()),
        f"- Score global de qualité : **{summary['global_quality_score']} / 100**",
        f"- Prêt pour le nettoyage : **{'oui' if summary['ready_for_cleaning'] else 'non'}**",
        "",
        _table(["Table", "Score qualité"],
               [[t, s] for t, s in summary["quality_score_by_table"].items()]),
        "",
        "## artifacts",
        "",
        *[f"- **{k}** : `{v}`" for k, v in profiling.get("artifacts", {}).items()],
        "",
    ]
    path.write_text("\n".join(out), encoding="utf-8")
