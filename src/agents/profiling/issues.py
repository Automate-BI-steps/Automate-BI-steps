"""Détection déterministe des problèmes de qualité (section `issues` de la spec).

Chaque règle produit des issues sans identifiant : les `issue_id` sont
attribués à la fin du pipeline, une fois les issues du LLM ajoutées.
"""

from difflib import SequenceMatcher
from itertools import combinations

import pandas as pd

from .column_profiler import is_id_like, last_token, name_tokens, parse_dates
from .readers import is_utf8_compatible, null_mask, read_table
from .relationships import primary_key

ISSUE_TYPES = [
    "encoding_error", "missing_values", "duplicate_rows", "duplicate_keys",
    "type_mismatch", "mixed_types", "inconsistent_format", "invalid_date",
    "date_logic_violation", "out_of_range", "outlier", "whitespace",
    "case_inconsistency", "typo_suspected", "orphan_foreign_key",
    "constant_column", "high_cardinality", "other",
]
SEVERITIES = ["critical", "high", "medium", "low", "info"]
SUGGESTED_ACTIONS = [
    "reencode", "drop_rows", "drop_duplicates", "impute_mean", "impute_median",
    "impute_mode", "impute_constant", "keep_as_null", "cast_type",
    "standardize_format", "trim_whitespace", "normalize_case", "correct_value",
    "cap_outliers", "flag_only", "investigate", "no_action",
]

# Colonnes dont le vide signifie souvent "l'événement n'a pas encore eu lieu".
EVENT_TOKENS = {"shipped", "delivered", "delivery", "closed", "end", "ended", "cancelled",
                "canceled", "returned", "paid", "completed", "resolved", "termination",
                "terminated", "left", "discontinued", "received"}
# Couples de dates : la date "début" doit précéder la date "fin".
START_TOKENS = {"order", "start", "created", "creation", "begin", "open", "opened",
                "birth", "issue", "issued"}
END_TOKENS = {"shipped", "ship", "delivered", "delivery", "end", "closed", "required",
              "due", "return", "returned", "received", "hire", "paid"}
# Dates planifiées : une date future est normale.
PLANNED_TOKENS = {"required", "due", "expected", "planned", "expiry", "expiration"}
# Mesures qui ne peuvent pas être négatives.
NON_NEGATIVE_TOKENS = {"price", "quantity", "qty", "freight", "amount", "cost", "total",
                       "discount", "salary", "stock", "units", "weight", "fee", "age"}


def make_issue(table: str, column: str | None, issue_type: str, severity: str,
               description: str, affected_rows: int, row_count: int, examples: list,
               rule: str, suggested_action: str | None = None,
               suggested_action_details: str | None = None, is_expected: bool = False,
               detected_by: str = "rule", confidence: float = 1.0) -> dict:
    return {
        "issue_id": None,
        "table": table,
        "column": column,
        "issue_type": issue_type,
        "severity": severity,
        "description": description,
        "affected_rows": int(affected_rows),
        "affected_pct": round(100 * affected_rows / row_count, 2) if row_count else 0.0,
        "examples": [_jsonable(e) for e in examples[:5]],
        "rule": rule,
        "is_expected": is_expected,
        "suggested_action": suggested_action,
        "suggested_action_details": suggested_action_details,
        "related_issues": [],
        "detected_by": detected_by,
        "confidence": confidence,
    }


def _jsonable(value):
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "item"):
        return value.item()
    return value


# --------------------------------------------------------------------------
# Règles au niveau fichier / table
# --------------------------------------------------------------------------

def file_issues(meta: dict, table: dict | None) -> list[dict]:
    issues = []
    name, rows = meta["table_name"], table["row_count"] if table else 0
    if meta["read_status"] == "error":
        issues.append(make_issue(
            name, None, "other", "critical",
            f"Fichier illisible : {meta['read_message']}", 0, 0, [],
            "Échec de lecture du fichier", "investigate"))
        return issues

    if not is_utf8_compatible(meta["encoding"]):
        # Ce que verrait un outil qui lit le fichier en UTF-8 (ex. Power BI par défaut).
        as_utf8 = read_table(meta, encoding="utf-8")
        broken = as_utf8.apply(lambda s: s.str.contains("�", regex=False))
        affected = int(broken.any(axis=1).sum())
        examples = as_utf8.where(broken).stack().dropna().drop_duplicates().head(5).tolist()
        issues.append(make_issue(
            name, None, "encoding_error", "medium",
            f"Le fichier est encodé en {meta['encoding']} : lu en UTF-8, les caractères "
            "accentués sont remplacés par '�'.",
            affected, rows, examples, "Encodage détecté différent de UTF-8",
            "reencode",
            f"Relire le fichier en {meta['encoding']} puis le sauvegarder en UTF-8."))
    return issues


def table_issues(table: dict, df: pd.DataFrame) -> list[dict]:
    issues = []
    name, rows = table["table_name"], table["row_count"]

    if table["duplicate_row_count"]:
        dups = df[df.duplicated(keep=False)].drop_duplicates()
        issues.append(make_issue(
            name, None, "duplicate_rows", "high",
            f"{table['duplicate_row_count']} ligne(s) entièrement dupliquée(s).",
            table["duplicate_row_count"], rows, dups.head(5).to_dict("records"),
            "Lignes identiques sur toutes les colonnes", "drop_duplicates"))

    # Si la table a un identifiant de référence, duplicate_keys couvre déjà le problème.
    if not table["primary_key_candidates"] and rows and not primary_key(table):
        issues.append(make_issue(
            name, None, "other", "medium",
            "Aucune clé primaire candidate (simple ou composée de 2 colonnes) trouvée.",
            0, rows, [], "Aucune colonne ni couple de colonnes unique et non nul",
            "investigate"))

    issues += _date_logic_issues(table, df)
    return issues


def _date_logic_issues(table: dict, df: pd.DataFrame) -> list[dict]:
    issues = []
    dates = [c["column_name"] for c in table["columns"] if c["semantic_role"] == "date"]
    starts = [c for c in dates if set(name_tokens(c)) & START_TOKENS]
    ends = [c for c in dates if set(name_tokens(c)) & END_TOKENS]
    for start in starts:
        for end in ends:
            if start == end:
                continue
            d_start, d_end = parse_dates(df[start]), parse_dates(df[end])
            bad = d_start.notna() & d_end.notna() & (d_end < d_start)
            if bad.any():
                examples = df.loc[bad, [start, end]].head(5).to_dict("records")
                issues.append(make_issue(
                    table["table_name"], f"{start},{end}", "date_logic_violation", "high",
                    f"{int(bad.sum())} ligne(s) où {end} est antérieure à {start}.",
                    int(bad.sum()), table["row_count"], examples,
                    f"{end} >= {start}", "investigate"))
    return issues


# --------------------------------------------------------------------------
# Règles au niveau colonne
# --------------------------------------------------------------------------

def column_issues(table: dict, df: pd.DataFrame, self_ref_columns: set[str]) -> list[dict]:
    issues = []
    for col in table["columns"]:
        issues += _missing_issue(table, col, self_ref_columns)
        issues += _type_issues(table, col)
        issues += _key_issues(table, col, df)
        issues += _range_issues(table, col, df)
        issues += _text_issues(table, col, df)
    return issues


def _missing_issue(table: dict, col: dict, self_ref_columns: set[str]) -> list[dict]:
    n = col["null_count"]
    if not n:
        return []
    name, column, rows = table["table_name"], col["column_name"], table["row_count"]
    role, pct = col["semantic_role"], col["null_pct"]
    tokens = set(name_tokens(column))
    expected, details = False, None

    if role == "date" and tokens & EVENT_TOKENS:
        expected = True
        details = "Probablement un événement qui n'a pas encore eu lieu : conserver le vide."
    elif column in self_ref_columns and n <= max(1, 0.05 * rows):
        expected = True
        details = "Auto-référence hiérarchique : le vide correspond probablement à la racine."

    if expected:
        severity, action = "info", "keep_as_null"
    elif role == "identifier":
        severity, action = "critical", "drop_rows"
    elif role == "foreign_key":
        severity, action = "high", "investigate"
    else:
        severity = "high" if pct >= 50 else "medium" if pct >= 5 else "low"
        action = {"measure": "impute_median", "category": "impute_constant",
                  "flag": "impute_mode"}.get(role, "keep_as_null")
        if action == "impute_constant":
            details = "Remplacer par une modalité explicite, ex. 'Unknown'."

    return [make_issue(
        name, column, "missing_values", severity,
        f"{n} valeur(s) manquante(s) ({pct} %) dans {column}.",
        n, rows, [], "Valeur vide ou jeton nul (NA, null, None…)",
        action, details, is_expected=expected)]


def _type_issues(table: dict, col: dict) -> list[dict]:
    name, column, rows = table["table_name"], col["column_name"], table["row_count"]
    detected, expected = col["detected_type"], col["expected_type"]
    n_bad, samples = col["nonconforming_count"], col["nonconforming_samples"]
    issues = []

    if expected in {"date", "datetime"}:
        if col.get("invalid_date_count"):
            issues.append(make_issue(
                name, column, "invalid_date", "medium",
                f"{col['invalid_date_count']} date(s) non interprétable(s) dans {column}.",
                col["invalid_date_count"], rows, col["invalid_date_samples"],
                "Valeur ne correspondant à aucun format de date reconnu",
                "cast_type", "Convertir en date ; les valeurs invalides deviennent nulles."))
        if len(col.get("detected_formats", [])) > 1:
            issues.append(make_issue(
                name, column, "inconsistent_format", "medium",
                f"Plusieurs formats de date dans {column} : {', '.join(col['detected_formats'])}.",
                0, rows, col["detected_formats"], "Plus d'un format de date détecté",
                "standardize_format", "Uniformiser au format ISO YYYY-MM-DD."))
        return issues

    if n_bad and detected == "mixed":
        issues.append(make_issue(
            name, column, "mixed_types", "high" if col["semantic_role"] == "measure" else "medium",
            f"{column} devrait être de type {expected} mais contient {n_bad} valeur(s) "
            "d'un autre type.",
            n_bad, rows, samples, f"Valeurs non conformes au type attendu ({expected})",
            "cast_type", "Convertir ; les valeurs non convertibles sont à corriger ou annuler."))
    elif detected != expected and expected in {"integer", "float", "boolean"} \
            and detected not in {"integer", "float"}:
        issues.append(make_issue(
            name, column, "type_mismatch", "high",
            f"{column} devrait être numérique ({expected}) d'après son nom, "
            f"mais le type observé est {detected}.",
            n_bad, rows, samples, "Type observé différent du type attendu",
            "cast_type"))
    return issues


def _key_issues(table: dict, col: dict, df: pd.DataFrame) -> list[dict]:
    name, column, rows = table["table_name"], col["column_name"], table["row_count"]
    # Clé de référence de la table (identifiant en 1re colonne) qui devrait être unique.
    if column == primary_key(table) and not col["is_unique"]:
        values = df[column][~null_mask(df[column])]
        dups = values[values.duplicated(keep=False)]
        return [make_issue(
            name, column, "duplicate_keys", "critical",
            f"La clé {column} contient {dups.nunique()} valeur(s) en double.",
            len(dups), rows, dups.drop_duplicates().head(5).tolist(),
            "Identifiant en première colonne non unique", "investigate",
            "Vérifier s'il s'agit de doublons à supprimer ou d'identifiants à corriger.")]
    return []


def _range_issues(table: dict, col: dict, df: pd.DataFrame) -> list[dict]:
    name, column, rows = table["table_name"], col["column_name"], table["row_count"]
    token = last_token(column)
    issues = []

    if col["semantic_role"] == "measure" and "min" in col:
        nums = pd.to_numeric(df[column].str.strip(), errors="coerce")
        if token in NON_NEGATIVE_TOKENS and col["negative_count"]:
            neg = nums[nums < 0]
            issues.append(make_issue(
                name, column, "out_of_range", "high",
                f"{len(neg)} valeur(s) négative(s) dans {column}.",
                len(neg), rows, neg.head(5).tolist(), f"{column} >= 0", "investigate"))
        if token == "discount":
            bad = nums[(nums < 0) | (nums > 1)]
            if len(bad):
                issues.append(make_issue(
                    name, column, "out_of_range", "medium",
                    f"{len(bad)} remise(s) hors de l'intervalle [0, 1].",
                    len(bad), rows, bad.head(5).tolist(), "0 <= discount <= 1",
                    "investigate", "Vérifier s'il s'agit de pourcentages (ex. 15 au lieu de 0.15)."))
        if col["outlier_count"]:
            issues.append(make_issue(
                name, column, "outlier", "low",
                f"{col['outlier_count']} valeur(s) aberrante(s) dans {column} "
                f"(hors de [{col['outlier_bounds'][0]}, {col['outlier_bounds'][1]}]).",
                col["outlier_count"], rows, col["outlier_samples"],
                "Méthode IQR : < Q1 - 1.5·IQR ou > Q3 + 1.5·IQR", "flag_only",
                "Vérifier avant de corriger : il peut s'agir de vraies grosses valeurs."))

    if col["semantic_role"] == "date" and "min_date" in col:
        dates = parse_dates(df[column])
        if col["future_date_count"] and token not in PLANNED_TOKENS:
            future = df[column][dates > pd.Timestamp.today()]
            issues.append(make_issue(
                name, column, "out_of_range", "low",
                f"{col['future_date_count']} date(s) dans le futur dans {column}.",
                col["future_date_count"], rows, future.head(5).tolist(),
                "Date <= aujourd'hui", "investigate"))
        old = df[column][dates < pd.Timestamp("1900-01-01")]
        if len(old):
            issues.append(make_issue(
                name, column, "out_of_range", "medium",
                f"{len(old)} date(s) antérieure(s) à 1900 dans {column}.",
                len(old), rows, old.head(5).tolist(), "Date >= 1900-01-01", "investigate"))

    if col["distinct_count"] == 1 and rows > 1:
        issues.append(make_issue(
            name, column, "constant_column", "low",
            f"{column} ne contient qu'une seule valeur.",
            rows, rows, col["sample_values"], "Une seule valeur distincte", "investigate",
            "Colonne probablement inutile pour l'analyse."))
    return issues


def _text_issues(table: dict, col: dict, df: pd.DataFrame) -> list[dict]:
    name, column, rows = table["table_name"], col["column_name"], table["row_count"]
    issues = []
    if col.get("invalid_char_count"):
        issues.append(make_issue(
            name, column, "encoding_error", "medium",
            f"{col['invalid_char_count']} valeur(s) avec des caractères invalides dans {column}.",
            col["invalid_char_count"], rows, col["invalid_char_samples"],
            "Présence de '�' ou de séquences mal décodées (Ã©, Â…)", "reencode"))
    if col.get("leading_trailing_spaces_count"):
        issues.append(make_issue(
            name, column, "whitespace", "low",
            f"{col['leading_trailing_spaces_count']} valeur(s) avec des espaces en début/fin.",
            col["leading_trailing_spaces_count"], rows, col["whitespace_samples"],
            "valeur != valeur.strip()", "trim_whitespace"))
    if col.get("case_variants_count"):
        issues.append(make_issue(
            name, column, "case_inconsistency", "low",
            f"{col['case_variants_count']} valeur(s) écrite(s) avec des casses différentes.",
            0, rows, col["case_variant_samples"],
            "Même valeur en minuscules, écritures différentes", "normalize_case"))
    if col["semantic_role"] in {"category", "text"}:
        issues += _near_duplicate_issues(table, col, df)
    return issues


def _near_duplicate_issues(table: dict, col: dict, df: pd.DataFrame) -> list[dict]:
    """Valeurs presque identiques (ex. 'Germany' / 'Germny') : faute probable."""
    column = col["column_name"]
    values = df[column][~null_mask(df[column])].str.strip()
    counts = values.value_counts()
    if len(counts) > 300:
        return []
    issues = []
    keys = [v for v in counts.index if len(v) >= 4 and not any(ch.isdigit() for ch in v)]
    for a, b in combinations(keys, 2):
        la, lb = a.lower(), b.lower()
        if la == lb:
            continue  # déjà couvert par case_inconsistency
        matcher = SequenceMatcher(None, la, lb)
        if matcher.real_quick_ratio() < 0.9 or matcher.ratio() < 0.9:
            continue
        rare, common = (a, b) if counts[a] <= counts[b] else (b, a)
        issues.append(make_issue(
            table["table_name"], column, "typo_suspected", "low",
            f"'{rare}' ressemble fortement à '{common}' : faute de saisie probable.",
            int(counts[rare]), table["row_count"], [rare, common],
            "Similarité de chaîne >= 0.9 entre deux valeurs distinctes",
            "correct_value", f"Remplacer '{rare}' par '{common}' si confirmé.",
            confidence=0.6))
    return issues


def orphan_issues(relationships: list[dict], row_counts: dict[str, int]) -> list[dict]:
    issues = []
    for rel in relationships:
        if rel["orphan_count"]:
            issues.append(make_issue(
                rel["from_table"], rel["from_column"], "orphan_foreign_key", "high",
                f"{rel['orphan_count']} ligne(s) de {rel['from_table']}.{rel['from_column']} "
                f"sans correspondance dans {rel['to_table']}.{rel['to_column']}.",
                rel["orphan_count"], row_counts[rel["from_table"]], rel["orphan_samples"],
                f"{rel['from_column']} ∈ {rel['to_table']}.{rel['to_column']}",
                "investigate",
                "Ajouter les clés manquantes dans la table cible ou isoler ces lignes."))
    return issues
