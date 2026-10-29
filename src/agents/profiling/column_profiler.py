"""Profilage des tables et des colonnes (section `tables` de la spec)."""

import re
from datetime import date
from itertools import combinations

import pandas as pd

from .readers import null_mask

# --------------------------------------------------------------------------
# Indices tirés du nom de colonne (dernier mot : unitPrice -> "price")
# --------------------------------------------------------------------------

ID_TOKENS = {"id", "code", "key", "ref", "no", "num", "number"}
DATE_TOKENS = {"date", "datetime", "timestamp", "at", "dt"}
FLOAT_TOKENS = {"price", "amount", "freight", "cost", "total", "discount", "salary",
                "revenue", "weight", "tax", "fee", "margin", "profit"}
INT_TOKENS = {"quantity", "qty", "units", "stock", "count", "age", "year"}
BOOL_VALUES = {"true", "false", "yes", "no", "y", "n", "oui", "non", "t", "f"}

DATE_PATTERNS = [
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "%Y-%m-%d", "YYYY-MM-DD", "date"),
    (re.compile(r"^\d{4}/\d{2}/\d{2}$"), "%Y/%m/%d", "YYYY/MM/DD", "date"),
    (re.compile(r"^\d{2}/\d{2}/\d{4}$"), "%d/%m/%Y", "DD/MM/YYYY", "date"),
    (re.compile(r"^\d{2}-\d{2}-\d{4}$"), "%d-%m-%Y", "DD-MM-YYYY", "date"),
    (re.compile(r"^\d{2}\.\d{2}\.\d{4}$"), "%d.%m.%Y", "DD.MM.YYYY", "date"),
    (re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?Z?$"), None,
     "YYYY-MM-DD hh:mm:ss", "datetime"),
]
INT_RE = re.compile(r"^[+-]?\d+$")
FLOAT_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")
MOJIBAKE_RE = re.compile("�|Ã[\u0080-¿]|Â[\u0080-¿]")


def name_tokens(column: str) -> list[str]:
    """Découpe un nom camelCase / snake_case en mots minuscules."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", column)
    return [t for t in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if t]


def last_token(column: str) -> str:
    tokens = name_tokens(column)
    return tokens[-1] if tokens else ""


def is_id_like(column: str) -> bool:
    return last_token(column) in ID_TOKENS


# --------------------------------------------------------------------------
# Inférence de type valeur par valeur
# --------------------------------------------------------------------------

def value_kind(value: str) -> tuple[str, str | None]:
    """Retourne (type, format de date éventuel) pour une valeur non nulle."""
    v = value.strip()
    if INT_RE.match(v):
        return "integer", None
    if FLOAT_RE.match(v):
        return "float", None
    if v.lower() in BOOL_VALUES:
        return "boolean", None
    for regex, _, label, kind in DATE_PATTERNS:
        if regex.match(v):
            return kind, label
    return "string", None


def parse_dates(values: pd.Series) -> pd.Series:
    """Parse les dates selon le format reconnu pour chaque valeur (NaT sinon)."""
    out = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    stripped = values.str.strip()
    for regex, fmt, _, _ in DATE_PATTERNS:
        mask = stripped.str.match(regex.pattern) & out.isna()
        if mask.any():
            kwargs = {"format": fmt} if fmt else {"format": "ISO8601"}
            out[mask] = pd.to_datetime(stripped[mask], errors="coerce", **kwargs)
    return out


def _family(kind: str) -> str:
    return "numeric" if kind in {"integer", "float"} else kind


def _merge_numeric(kinds: set[str]) -> str:
    return "integer" if kinds == {"integer"} else "float"


def infer_types(column: str, values: pd.Series) -> dict:
    """Type détecté (observé), type attendu et répartition des types."""
    kinds = values.map(lambda v: value_kind(v)[0])
    counts = kinds.value_counts()
    families = kinds.map(_family).value_counts()
    total = int(counts.sum())

    if total == 0:
        detected = "string"
    elif len(families) == 1:
        fam = families.index[0]
        detected = _merge_numeric(set(counts.index)) if fam == "numeric" else fam
    else:
        detected = "mixed"

    # Type attendu : famille dominante (>= 90 %), corrigée par le nom de colonne.
    expected = "string"
    if total:
        dom_family, dom_count = families.index[0], int(families.iloc[0])
        if dom_count / total >= 0.9:
            if dom_family == "numeric":
                numeric_kinds = {k for k in counts.index if k in {"integer", "float"}}
                expected = _merge_numeric(numeric_kinds)
            else:
                expected = dom_family
    token = last_token(column)
    if token in DATE_TOKENS and expected not in {"date", "datetime"}:
        expected = "date"
    elif token in FLOAT_TOKENS and expected not in {"integer", "float"}:
        expected = "float"
    elif token in INT_TOKENS and expected not in {"integer", "float"}:
        expected = "integer"

    # Valeurs non conformes au type attendu (utile pour les issues).
    if expected in {"integer", "float"}:
        nonconform = values[~kinds.isin(["integer", "float"])]
    elif expected in {"date", "datetime"}:
        nonconform = values[~kinds.isin(["date", "datetime"])]
    elif expected == "boolean":
        nonconform = values[kinds != "boolean"]
    else:
        nonconform = values.iloc[0:0]

    return {
        "detected_type": detected,
        "expected_type": expected,
        "type_distribution": {k: int(v) for k, v in counts.items()},
        "_nonconform": nonconform,
    }


# --------------------------------------------------------------------------
# Profil d'une colonne
# --------------------------------------------------------------------------

def _py(value):
    """Convertit les scalaires numpy/pandas en types Python sérialisables."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float):
        return round(value, 4)
    return value


def _semantic_role(column: str, expected: str, distinct: int, unique_pct: float,
                   non_null_values: pd.Series) -> str:
    if expected in {"date", "datetime"}:
        return "date"
    lowered = set(non_null_values.str.strip().str.lower().unique())
    if 0 < distinct <= 2 and lowered <= (BOOL_VALUES | {"0", "1"}):
        return "flag"
    if is_id_like(column):
        return "identifier"
    if expected in {"integer", "float"}:
        return "measure"
    if unique_pct < 80:
        return "category"
    return "text"


def profile_column(df: pd.DataFrame, column: str, position: int) -> dict:
    raw = df[column]
    nulls = null_mask(raw)
    values = raw[~nulls]
    row_count = len(raw)
    null_count = int(nulls.sum())
    distinct = int(values.nunique())
    non_null = len(values)
    unique_pct = round(100 * distinct / non_null, 2) if non_null else 0.0

    types = infer_types(column, values)
    expected = types["expected_type"]

    profile = {
        "column_name": column,
        "position": position,
        "detected_type": types["detected_type"],
        "expected_type": expected,
        "type_distribution": types["type_distribution"],
        "semantic_role": _semantic_role(column, expected, distinct, unique_pct, values),
        "null_count": null_count,
        "null_pct": round(100 * null_count / row_count, 2) if row_count else 0.0,
        "distinct_count": distinct,
        "unique_pct": unique_pct,
        "is_unique": bool(non_null and distinct == non_null),
        "sample_values": [str(v) for v in values.drop_duplicates().head(5)],
        "top_values": [{"value": str(v), "count": int(c)}
                       for v, c in values.value_counts().head(5).items()],
        "nonconforming_count": len(types["_nonconform"]),
        "nonconforming_samples": [str(v) for v in types["_nonconform"].drop_duplicates().head(5)],
    }

    if expected in {"integer", "float"}:
        profile.update(_numeric_stats(values))
    elif expected in {"date", "datetime"}:
        profile.update(_date_stats(values))
    if expected in {"string", "boolean"} or profile["semantic_role"] in {"category", "text"}:
        profile.update(_text_stats(values))
    return profile


def _numeric_stats(values: pd.Series) -> dict:
    nums = pd.to_numeric(values.str.strip(), errors="coerce").dropna()
    if nums.empty:
        return {}
    q1, q3 = nums.quantile(0.25), nums.quantile(0.75)
    iqr = q3 - q1
    low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    outliers = nums[(nums < low) | (nums > high)]
    return {
        "min": _py(nums.min()),
        "max": _py(nums.max()),
        "mean": _py(nums.mean()),
        "median": _py(nums.median()),
        "std": _py(nums.std()) if len(nums) > 1 else None,
        "q1": _py(q1),
        "q3": _py(q3),
        "zero_count": int((nums == 0).sum()),
        "negative_count": int((nums < 0).sum()),
        "outlier_count": int(len(outliers)),
        "outlier_method": "IQR 1.5",
        "outlier_bounds": [_py(low), _py(high)],
        "outlier_samples": [_py(v) for v in outliers.drop_duplicates().sort_values(ascending=False).head(5)],
    }


def _date_stats(values: pd.Series) -> dict:
    parsed = parse_dates(values)
    valid = parsed.dropna()
    formats = sorted({fmt for v in values if (fmt := value_kind(v)[1])})
    today = pd.Timestamp(date.today())
    return {
        "min_date": valid.min().isoformat() if not valid.empty else None,
        "max_date": valid.max().isoformat() if not valid.empty else None,
        "detected_formats": formats,
        "invalid_date_count": int(parsed.isna().sum()),
        "invalid_date_samples": [str(v) for v in values[parsed.isna()].drop_duplicates().head(5)],
        "future_date_count": int((valid > today).sum()),
    }


def _text_stats(values: pd.Series) -> dict:
    if values.empty:
        return {}
    lengths = values.str.len()
    spaced = values[values != values.str.strip()]
    normalized = values.str.strip().str.lower()
    variants = values.str.strip().groupby(normalized).nunique()
    case_groups = variants[variants > 1]
    invalid = values[values.str.contains(MOJIBAKE_RE)]
    return {
        "min_length": int(lengths.min()),
        "max_length": int(lengths.max()),
        "leading_trailing_spaces_count": int(len(spaced)),
        "whitespace_samples": [repr(v) for v in spaced.drop_duplicates().head(5)],
        "case_variants_count": int(len(case_groups)),
        "case_variant_samples": [
            sorted(values[normalized == key].str.strip().unique().tolist())
            for key in case_groups.index[:5]
        ],
        "invalid_char_count": int(len(invalid)),
        "invalid_char_samples": [str(v) for v in invalid.drop_duplicates().head(5)],
    }


# --------------------------------------------------------------------------
# Profil d'une table
# --------------------------------------------------------------------------

def primary_key_candidates(df: pd.DataFrame, columns: list[dict]) -> list[list[str]]:
    """Colonnes (ou couples de colonnes) uniques et sans valeur manquante."""
    def no_nulls(col: str) -> bool:
        return not null_mask(df[col]).any()

    singles = [c["column_name"] for c in columns
               if c["is_unique"] and c["null_count"] == 0 and len(df) > 0]
    singles.sort(key=lambda c: (not is_id_like(c), c))
    if singles:
        return [[c] for c in singles]

    # Aucune clé simple : on teste les couples (colonnes identifiantes d'abord).
    ordered = sorted(df.columns, key=lambda c: (not is_id_like(c), list(df.columns).index(c)))
    pairs = []
    for a, b in combinations(ordered[:10], 2):
        if no_nulls(a) and no_nulls(b) and not df.duplicated(subset=[a, b]).any():
            pairs.append([a, b])
            if len(pairs) >= 3:
                break
    return pairs


def profile_table(meta: dict, df: pd.DataFrame) -> dict:
    columns = [profile_column(df, col, i) for i, col in enumerate(df.columns)]
    return {
        "table_name": meta["table_name"],
        "file_name": meta["file_name"],
        "row_count": int(len(df)),
        "column_count": int(df.shape[1]),
        "primary_key_candidates": primary_key_candidates(df, columns),
        "duplicate_row_count": int(df.duplicated().sum()),
        "columns": columns,
    }
