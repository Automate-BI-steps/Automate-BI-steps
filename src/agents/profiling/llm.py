"""Revue sémantique par LLM (Groq).

Le LLM complète les règles déterministes là où il faut « comprendre » les
données : fautes de saisie, valeurs absurdes, rôle métier des colonnes,
manquants légitimes, et rédaction du résumé. Tout ce qu'il propose est
vérifié contre les données réelles avant d'entrer dans le rapport.
"""

import json
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from .issues import make_issue
from .readers import null_mask

SemanticRole = Literal["identifier", "foreign_key", "measure", "date", "category", "text", "flag"]
MAX_VALUES_PER_COLUMN = 80


class RoleCorrection(BaseModel):
    column: str
    semantic_role: SemanticRole
    reason: str


class SuspectedValue(BaseModel):
    column: str
    value: str = Field(description="Valeur exacte, copiée telle quelle depuis la liste fournie")
    issue_type: Literal["typo_suspected", "out_of_range", "other"]
    suggested_value: str | None = Field(None, description="Correction proposée, si évidente")
    reason: str
    confidence: float = Field(ge=0, le=1)


class MissingAssessment(BaseModel):
    column: str
    is_expected: bool = Field(description="True si le vide est normal d'un point de vue métier")
    reason: str


class TableReview(BaseModel):
    role_corrections: list[RoleCorrection] = Field(default_factory=list)
    suspected_values: list[SuspectedValue] = Field(default_factory=list)
    missing_assessments: list[MissingAssessment] = Field(default_factory=list)


SYSTEM_PROMPT = """Tu es un expert en qualité des données qui audite des fichiers CSV \
destinés à un projet Power BI. On te donne le profil d'une table et les valeurs \
distinctes de ses colonnes texte. Réponds uniquement à propos de cette table.

1. role_corrections : uniquement si le rôle sémantique proposé est clairement faux.
2. suspected_values : valeurs individuelles probablement erronées (faute de frappe, \
caractères parasites, valeur absurde). Copie la valeur EXACTEMENT. Ne signale pas les \
noms propres étrangers corrects, les abréviations usuelles ni les différences d'accent \
ou de casse. Sois strict : en cas de doute, ne signale rien.
3. missing_assessments : pour chaque colonne qui a des valeurs manquantes, indique si \
le vide est normal d'un point de vue métier (ex. date de livraison d'une commande non \
encore expédiée, responsable du directeur général)."""


def _table_payload(table: dict, df: pd.DataFrame, business_context: str | None) -> str:
    columns = []
    for col in table["columns"]:
        entry = {
            "column": col["column_name"],
            "semantic_role": col["semantic_role"],
            "expected_type": col["expected_type"],
            "null_count": col["null_count"],
            "distinct_count": col["distinct_count"],
            "sample_values": col["sample_values"],
        }
        if col["semantic_role"] in {"category", "text"}:
            values = df[col["column_name"]]
            distinct = values[~null_mask(values)].drop_duplicates()
            entry["distinct_values"] = distinct.head(MAX_VALUES_PER_COLUMN).tolist()
        if "min" in col:
            entry["range"] = [col["min"], col["max"]]
        columns.append(entry)
    payload = {"table": table["table_name"], "row_count": table["row_count"], "columns": columns}
    if business_context:
        payload["business_context"] = business_context
    return json.dumps(payload, ensure_ascii=False)


def review_table(llm, table: dict, df: pd.DataFrame, business_context: str | None) -> TableReview:
    structured = llm.with_structured_output(TableReview, method="json_schema")
    return structured.invoke([
        ("system", SYSTEM_PROMPT),
        ("human", _table_payload(table, df, business_context)),
    ])


def apply_review(review: TableReview, table: dict, df: pd.DataFrame,
                 issues: list[dict]) -> list[dict]:
    """Intègre la revue LLM après vérification ; retourne les nouvelles issues."""
    columns = {c["column_name"]: c for c in table["columns"]}
    name, rows = table["table_name"], table["row_count"]

    for fix in review.role_corrections:
        col = columns.get(fix.column)
        # Les clés étrangères sont prouvées par les données : le LLM ne les écrase pas.
        if col and col["semantic_role"] not in {"foreign_key", fix.semantic_role}:
            col["semantic_role_rule"] = col["semantic_role"]
            col["semantic_role"] = fix.semantic_role
            col["semantic_role_source"] = "llm"
            col["semantic_role_reason"] = fix.reason

    for assessment in review.missing_assessments:
        if not assessment.is_expected:
            continue
        for issue in issues:
            if (issue["table"], issue["column"], issue["issue_type"]) == \
                    (name, assessment.column, "missing_values") and not issue["is_expected"]:
                issue.update(
                    is_expected=True, severity="info", suggested_action="keep_as_null",
                    suggested_action_details=f"Vide jugé normal par le LLM : {assessment.reason}",
                )

    already = {(i["table"], i["column"], i["examples"][0]) for i in issues
               if i["issue_type"] == "typo_suspected" and i["examples"]}
    new_issues = []
    for sv in review.suspected_values:
        if sv.column not in df.columns or (name, sv.column, sv.value) in already:
            continue
        matches = int((df[sv.column].str.strip() == sv.value.strip()).sum())
        if not matches:
            continue  # valeur inventée par le LLM : ignorée
        correction = f"Remplacer par '{sv.suggested_value}' si confirmé." \
            if sv.suggested_value else None
        new_issues.append(make_issue(
            name, sv.column, sv.issue_type, "low",
            f"'{sv.value}' semble erronée : {sv.reason}",
            matches, rows, [sv.value] + ([sv.suggested_value] if sv.suggested_value else []),
            "Revue sémantique par LLM", "correct_value" if sv.suggested_value else "investigate",
            correction, detected_by="llm", confidence=round(sv.confidence, 2)))
    return new_issues


def write_narrative(llm, summary: dict, issues: list[dict]) -> str:
    top = [
        {k: i[k] for k in ("table", "column", "issue_type", "severity", "description")}
        for i in issues if i["severity"] in {"critical", "high", "medium"}
    ][:25]
    prompt = (
        "Rédige en français un résumé de 5 à 8 phrases du diagnostic qualité ci-dessous, "
        "destiné à l'équipe qui va nettoyer les données. Mentionne les points prioritaires "
        "et ce qui est sain. Pas de titre, pas de liste.\n\n"
        + json.dumps({"summary": summary, "main_issues": top}, ensure_ascii=False)
    )
    return llm.invoke(prompt).content.strip()
