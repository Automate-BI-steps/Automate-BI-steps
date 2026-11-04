from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("bi_flow.dashboard_generator")

REQUIRED_COLUMNS = ("date", "country", "product", "direction", "value")
ALLOWED_DIRECTIONS = {"Import", "Export"}

# â”€â”€ Column-type heuristics â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_DATE_HINTS    = {"date", "time", "period", "year", "month", "day", "week", "quarter"}
_MEASURE_HINTS = {"value", "amount", "revenue", "quantity", "count", "total", "sum",
                  "price", "cost", "sales", "profit", "margin", "rate", "score", "balance"}


def classify_data_model(data_model: dict[str, Any]) -> dict[str, str]:
    """Classify each column in data_model as 'measure', 'dimension', or 'date'.

    Uses the ``columns`` dict from a star-schema data_model.json (Agent 3).
    Returns {column_name: 'measure' | 'dimension' | 'date'}.
    """
    columns: dict[str, str] = data_model.get("columns") or {}
    result: dict[str, str] = {}
    for col_name in columns:
        lower = col_name.lower()
        if any(hint in lower for hint in _DATE_HINTS):
            result[col_name] = "date"
        elif any(hint in lower for hint in _MEASURE_HINTS):
            result[col_name] = "measure"
        else:
            result[col_name] = "dimension"
    return result


def is_trade_preset(data_model: dict[str, Any], frame_cols: list[str]) -> bool:
    """Return True when the dataset looks like the trade preset.

    Criteria: a column aliasing 'direction' is present in the frame columns
    and, if data_model defines columns, also present in data_model.
    """
    direction_aliases = {"direction", "trade_direction", "flow"}
    lower_cols = {c.lower() for c in frame_cols}
    if not (lower_cols & direction_aliases):
        return False
    columns = data_model.get("columns") or {}
    if columns:
        return any(c.lower() in direction_aliases for c in columns)
    return True
PAGE_DEFINITIONS = (
    ("Executive Overview", "Compare headline imports, exports, balance, and trend."),
    ("Trade Performance", "How does trade change over time and by direction?"),
    ("Country Analysis", "Which countries contribute to trade?"),
    ("Product Analysis", "Which products contribute to trade?"),
    ("Business Insights", "What validated findings require attention?"),
    ("Data Quality & Traceability", "What quality and provenance evidence supports the report?"),
)
FACT_COLUMNS = (("Date", "dateTime"), ("Country", "string"), ("Product", "string"), ("Direction", "string"), ("Value", "double"))
INSIGHT_COLUMNS = (("Kind", "string"), ("Text", "string"))
QUALITY_COLUMNS = (("Check", "string"), ("Result", "string"))
# Power BI base theme shipped with the report; Desktop draws nothing on the canvas without one.
BASE_THEME = "CY24SU06"
BASE_THEME_FILE = Path(__file__).resolve().parents[3] / "resources" / f"{BASE_THEME}.json"


class Kpi(BaseModel):
    id: str
    name: str
    definition: str
    expression: str
    supported: bool = True
    source_columns: list[str] = Field(default_factory=list)
    # Optional DAX override for KPIs the generator has no built-in formula for.
    dax: str | None = None


class Bundle(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    data: Any
    data_model: dict[str, Any]
    kpis: list[Kpi]
    findings: dict[str, Any]
    quality: dict[str, Any]
    lineage: dict[str, Any] | None = None
    trade_mode: bool = True
    dimension_data: dict[str, Any] = Field(default_factory=dict)


@dataclass
class ValidationIssue:
    code: str
    severity: str
    file: str
    column: str | None
    rows: list[int]
    count: int
    policy: str
    message: str
    can_continue: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class InputValidationError(ValueError):
    def __init__(self, message: str, issues: list[ValidationIssue]):
        super().__init__(message)
        self.issues = issues


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value



def demo_bundle() -> Bundle:
    return Bundle(
        data=pd.DataFrame(
            [
                {"date": "2024-01-15", "country": "DE", "product": "Machinery", "direction": "Import", "value": 1000.0},
                {"date": "2024-01-15", "country": "US", "product": "Machinery", "direction": "Export", "value": 800.0},
                {"date": "2025-01-15", "country": "DE", "product": "Chemicals", "direction": "Import", "value": 1200.0},
                {"date": "2025-01-15", "country": "US", "product": "Chemicals", "direction": "Export", "value": 1500.0},
            ]
        ),
        data_model={"source": "deterministic_demo_fixture", "validated": True},
        kpis=[
            Kpi(id="total_imports", name="Total Imports", definition="Sum of value for imports", expression="SUM(value) where direction=Import", source_columns=["value", "direction"]),
            Kpi(id="total_exports", name="Total Exports", definition="Sum of value for exports", expression="SUM(value) where direction=Export", source_columns=["value", "direction"]),
            Kpi(id="trade_balance", name="Trade Balance", definition="Exports minus imports", expression="[Total Exports] - [Total Imports]", source_columns=["value", "direction"]),
            Kpi(id="transaction_count", name="Transaction Count", definition="Number of transactions", expression="COUNTROWS(FactTrade)"),
        ],
        findings={"findings": [], "recommendations": [], "reporting_period": "2024-2025"},
        quality={"status": "demo_fixture_validated", "issues": []},
        lineage={"source": "deterministic_demo_fixture"},
    )


def validate_frame(frame: pd.DataFrame, file_name: str = "cleaned_trade.csv", trade_mode: bool = True) -> tuple[pd.DataFrame, dict[str, str], list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    lookup = {str(column).strip().lower(): str(column) for column in frame.columns}
    aliases = {
        "date": ("date", "trade_date", "period"),
        "country": ("country", "country_code", "partner_country"),
        "product": ("product", "product_name", "hs_description"),
        "direction": ("direction", "trade_direction", "flow"),
        "value": ("value", "trade_value", "amount", "value_usd"),
    }
    if trade_mode:
        mapping = {key: next((lookup[name] for name in names if name in lookup), None) for key, names in aliases.items()}
        missing = [key for key, source in mapping.items() if source is None]
        if missing:
            issues.append(ValidationIssue("MISSING_REQUIRED_COLUMN", "error", file_name, None, [], len(missing), "reject", f"Missing required fields: {', '.join(missing)}", False))
            raise InputValidationError(issues[-1].message, issues)
        extras = [str(column) for column in frame.columns if str(column) not in mapping.values()]
        if extras:
            issues.append(ValidationIssue("UNEXPECTED_COLUMNS", "warning", file_name, None, [], len(extras), "accept_with_warning", f"Additional columns preserved: {', '.join(extras)}", True))
        result = frame.rename(columns={source: key for key, source in mapping.items()}).copy()
    else:
        date_aliases = aliases["date"]
        date_col = next((lookup[name] for name in date_aliases if name in lookup), None)
        mapping = {col: col for col in frame.columns}
        if date_col is not None and date_col != "date":
            mapping["date"] = date_col
            result = frame.rename(columns={date_col: "date"}).copy()
        else:
            result = frame.copy()

    if result.empty:
        issues.append(ValidationIssue("EMPTY_DATASET", "error", file_name, None, [], 0, "reject", "Trade dataset is empty", False))
        raise InputValidationError(issues[-1].message, issues)

    date_field = "date" if "date" in result.columns else None
    if date_field:
        parsed_dates = pd.to_datetime(result[date_field], errors="coerce")
        bad_dates = parsed_dates.isna()
        if bad_dates.any():
            rows = (result.index[bad_dates] + 2).tolist()[:100]
            issues.append(ValidationIssue("INVALID_DATE", "error", file_name, date_field, rows, int(bad_dates.sum()), "reject", "Dates must be parseable", False))
        result[date_field] = parsed_dates

    if trade_mode:
        numeric = pd.to_numeric(result["value"], errors="coerce")
        bad_values = numeric.isna()
        if bad_values.any():
            rows = (result.index[bad_values] + 2).tolist()[:100]
            issues.append(ValidationIssue("INVALID_NUMERIC_VALUE", "error", file_name, "value", rows, int(bad_values.sum()), "reject", "Trade values must be numeric", False))
        result["value"] = numeric
        blank = result[["country", "product", "direction"]].isna().any(axis=1)
        if blank.any():
            issues.append(ValidationIssue("MISSING_DIMENSION_VALUE", "error", file_name, None, (result.index[blank] + 2).tolist()[:100], int(blank.sum()), "reject", "Country, product, and direction are required", False))
        normalized_direction = result["direction"].astype("string").str.strip().str.title()
        invalid_direction = ~normalized_direction.isin(ALLOWED_DIRECTIONS)
        if invalid_direction.any():
            issues.append(ValidationIssue("INVALID_DIRECTION", "error", file_name, "direction", (result.index[invalid_direction] + 2).tolist()[:100], int(invalid_direction.sum()), "reject", "Direction must be Import or Export", False))
        result["direction"] = normalized_direction
        non_positive = result["value"].notna() & (result["value"] <= 0)
        if non_positive.any():
            issues.append(ValidationIssue("NON_POSITIVE_VALUE", "warning", file_name, "value", (result.index[non_positive] + 2).tolist()[:100], int(non_positive.sum()), "accept_with_warning", "Zero and negative values are retained for upstream review", True))
    else:
        for c in result.columns:
            if c != date_field and pd.api.types.is_numeric_dtype(result[c]):
                non_pos = result[c].notna() & (result[c] <= 0)
                if non_pos.any():
                    issues.append(ValidationIssue("NON_POSITIVE_VALUE", "warning", file_name, c, (result.index[non_pos] + 2).tolist()[:100], int(non_pos.sum()), "accept_with_warning", f"Zero and negative values in '{c}' are retained for upstream review", True))

    duplicates = result.duplicated(keep=False)
    if duplicates.any():
        issues.append(ValidationIssue("DUPLICATE_TRANSACTION", "warning", file_name, None, (result.index[duplicates] + 2).tolist()[:100], int(duplicates.sum()), "accept_with_warning", "Duplicate full rows are retained; transaction identity is unavailable", True))
    if "currency" in result.columns and result["currency"].dropna().nunique() > 1:
        issues.append(ValidationIssue("MULTIPLE_CURRENCIES", "error", file_name, "currency", [], int(result["currency"].nunique()), "reject", "Monetary values in multiple currencies cannot be aggregated without conversion policy", False))
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        raise InputValidationError("; ".join(issue.message for issue in errors), issues)
    return result, {key: str(source) for key, source in mapping.items() if source is not None}, issues


def load_bundle(root: Path, demo: bool) -> tuple[Bundle, list[ValidationIssue]]:
    if demo:
        return demo_bundle(), []

    issues: list[ValidationIssue] = []

    # â”€â”€ data_model.json (required) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if not (root / "data_model.json").exists():
        raise FileNotFoundError("Missing mandatory upstream inputs: data_model.json")

    data_model = read_json(root / "data_model.json")

    dim_file_stems = set()
    for d in data_model.get("dimensions", []):
        if isinstance(d, str):
            dim_file_stems.add(d.lower())
        elif isinstance(d, dict):
            for k in ("name", "table"):
                if d.get(k):
                    dim_file_stems.add(str(d[k]).lower())

    # â”€â”€ Find fact table â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    _SUPPORT_FILES = frozenset({
        "data_model.json", "kpi_catalog.json", "bi_analyst_findings.json",
        "data_quality_report.json", "xai_lineage.json", "user_context.json",
        "user_request.txt",
    })
    # Prefer cleaned_trade.csv/parquet for backward compat; then named fact table; then any other csv/parquet
    trade = next(
        (root / name for name in ("cleaned_trade.csv", "cleaned_trade.parquet") if (root / name).exists()),
        None,
    )
    if trade is None:
        fact_name = data_model.get("fact_table")
        if fact_name:
            candidates = [
                f"{fact_name}.csv", f"{fact_name}.parquet",
                f"{fact_name.lower()}.csv", f"{fact_name.lower()}.parquet",
                f"{fact_name.replace('Fact', '')}.csv", f"{fact_name.replace('Fact', '').lower()}.csv",
            ]
            for cand in candidates:
                if (root / cand).exists():
                    trade = root / cand
                    break

    if trade is None:
        for suffix in (".csv", ".parquet"):
            for candidate in sorted(root.glob(f"*{suffix}")):
                if candidate.name not in _SUPPORT_FILES and candidate.stem.lower() not in dim_file_stems:
                    trade = candidate
                    break
            if trade is not None:
                break

    if trade is None:
        raise FileNotFoundError("Missing mandatory upstream inputs: cleaned_trade.csv or cleaned_trade.parquet (or any .csv/.parquet fact table)")

    # â”€â”€ Load dimension tables if present â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    dimension_data: dict[str, Any] = {}
    for d in data_model.get("dimensions", []):
        d_name = d if isinstance(d, str) else (d.get("name") or d.get("table"))
        if d_name:
            for ext in (".csv", ".parquet"):
                p = root / f"{d_name}{ext}"
                if not p.exists():
                    p = root / f"{d_name.lower()}{ext}"
                if p.exists():
                    dimension_data[d_name] = pd.read_csv(p) if ext == ".csv" else pd.read_parquet(p)
                    break

    # â”€â”€ Load fact table â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    frame = pd.read_csv(trade) if trade.suffix == ".csv" else pd.read_parquet(trade)

    # â”€â”€ Auto-detect trade mode â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    trade_mode = is_trade_preset(data_model, list(frame.columns))

    if trade_mode:
        normalized, _, frame_issues = validate_frame(frame, trade.name, trade_mode=True)
    else:
        normalized, _, frame_issues = validate_frame(frame, trade.name, trade_mode=False)
    issues.extend(frame_issues)

    # â”€â”€ kpi_catalog.json (required if present; currently we keep it required) â”€â”€
    if not (root / "kpi_catalog.json").exists():
        raise FileNotFoundError("Missing mandatory upstream inputs: kpi_catalog.json")
    catalog = read_json(root / "kpi_catalog.json")

    # â”€â”€ bi_analyst_findings.json (optional) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if (root / "bi_analyst_findings.json").exists():
        findings = read_json(root / "bi_analyst_findings.json")
    else:
        findings = {
            "findings": [],
            "recommendations": [],
            "reporting_period": "unavailable",
            "confidence": "unavailable",
        }
        issues.append(ValidationIssue(
            "MISSING_FINDINGS", "warning", "bi_analyst_findings.json", None, [], 0,
            "accept_with_warning", "bi_analyst_findings.json not found; using fallback empty findings", True,
        ))
        logger.warning("bi_analyst_findings.json not found in %s; using fallback", root)

    # â”€â”€ data_quality_report.json (optional) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if (root / "data_quality_report.json").exists():
        quality = read_json(root / "data_quality_report.json")
    else:
        quality = {
            "status": "computed_fallback",
            "row_count": len(frame),
            "null_counts": {col: int(frame[col].isna().sum()) for col in frame.columns},
        }
        issues.append(ValidationIssue(
            "MISSING_QUALITY_REPORT", "warning", "data_quality_report.json", None, [], 0,
            "accept_with_warning", "data_quality_report.json not found; computed basic fallback", True,
        ))
        logger.warning("data_quality_report.json not found in %s; computed fallback", root)

    # â”€â”€ xai_lineage.json (optional) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if (root / "xai_lineage.json").exists():
        lineage = read_json(root / "xai_lineage.json")
    else:
        lineage = None
        issues.append(ValidationIssue(
            "MISSING_LINEAGE", "warning", "xai_lineage.json", None, [], 0,
            "accept_with_warning", "xai_lineage.json not found; lineage unavailable", True,
        ))
        logger.warning("xai_lineage.json not found in %s; lineage unavailable", root)

    bundle = Bundle(
        data=normalized,
        data_model=data_model,
        kpis=[Kpi.model_validate(item) for item in catalog.get("kpis", catalog.get("measures", []))],
        findings=findings,
        quality=quality,
        lineage=lineage,
        trade_mode=trade_mode,
        dimension_data=dimension_data,
    )
    return bundle, issues



# Built-in KPI formulas: DAX for the semantic model and an independent pandas
# calculation used to state the expected value of each measure.
KNOWN_KPIS: dict[str, tuple[str, Any]] = {
    "total_imports": (
        'CALCULATE(SUM(FactTrade[Value]), FactTrade[Direction] = "Import")',
        lambda data: float(data.loc[data["direction"] == "Import", "value"].sum()),
    ),
    "total_exports": (
        'CALCULATE(SUM(FactTrade[Value]), FactTrade[Direction] = "Export")',
        lambda data: float(data.loc[data["direction"] == "Export", "value"].sum()),
    ),
    # Self-contained so the balance does not depend on how the catalog names the other measures.
    "trade_balance": (
        'CALCULATE(SUM(FactTrade[Value]), FactTrade[Direction] = "Export") - CALCULATE(SUM(FactTrade[Value]), FactTrade[Direction] = "Import")',
        lambda data: float(data.loc[data["direction"] == "Export", "value"].sum() - data.loc[data["direction"] == "Import", "value"].sum()),
    ),
    "transaction_count": ("COUNTROWS(FactTrade)", lambda data: float(len(data))),
}


def python_kpis(bundle: Bundle, data: pd.DataFrame) -> dict[str, float]:
    return {kpi.id: KNOWN_KPIS[kpi.id][1](data) for kpi in bundle.kpis if kpi.supported and kpi.id in KNOWN_KPIS}


def _translate_expression(expr: str, fact_table: str = "FactTrade") -> str | None:
    """Translate a simple SUM/AVG/COUNT/DISTINCTCOUNT/COUNTROWS expression to DAX.

    Returns the DAX string on success, or ``None`` when the expression is not
    simple enough to auto-translate.
    """
    _PATTERN = re.compile(
        r"^(SUM|AVG|AVERAGE|COUNT|DISTINCTCOUNT)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)$",
        re.IGNORECASE,
    )
    _COUNTROWS = re.compile(r"^COUNTROWS\s*\(?\s*\)?$", re.IGNORECASE)

    expr_stripped = expr.strip()

    if _COUNTROWS.match(expr_stripped):
        return f"COUNTROWS({fact_table})"

    m = _PATTERN.match(expr_stripped)
    if m:
        func, col = m.group(1).upper(), m.group(2)
        col_title = col[0].upper() + col[1:]  # keep original casing after first char
        if func in ("AVG", "AVERAGE"):
            func = "AVERAGE"
        return f"{func}({fact_table}[{col_title}])"

    return None


def dax(kpis: list[Kpi], fact_table: str = "FactTrade") -> dict[str, str]:
    """Measure name -> DAX.

    Resolution order:
    1. ``kpi.dax`` field (explicit override)
    2. Built-in trade KPI formula from ``KNOWN_KPIS``
    3. Auto-translation of simple ``SUM/AVG/COUNT/DISTINCTCOUNT/COUNTROWS`` expressions
    4. Skip with a warning when none of the above applies
    """
    measures: dict[str, str] = {}
    for kpi in kpis:
        if not kpi.supported:
            continue
        if kpi.dax:
            measures[kpi.name] = kpi.dax
        elif kpi.id in KNOWN_KPIS:
            measures[kpi.name] = KNOWN_KPIS[kpi.id][0]
        else:
            translated = _translate_expression(kpi.expression, fact_table=fact_table)
            if translated is not None:
                measures[kpi.name] = translated
            else:
                logger.warning(
                    "KPI '%s' has no built-in formula, no 'dax' field, and its expression "
                    "'%s' could not be auto-translated to DAX; skipping.",
                    kpi.id, kpi.expression,
                )
    return measures


def not_visualised(kpis: list[Kpi]) -> list[dict[str, str]]:
    skipped = []
    for kpi in kpis:
        if not kpi.supported:
            skipped.append({"kpi_id": kpi.id, "reason": "Marked as unsupported in the KPI catalog"})
        elif not kpi.dax and kpi.id not in KNOWN_KPIS and _translate_expression(kpi.expression) is None:
            skipped.append({
                "kpi_id": kpi.id,
                "reason": "No DAX formula available and expression could not be auto-translated",
            })
    return skipped


def _fact_csv(data: pd.DataFrame) -> pd.DataFrame:
    """Return a DataFrame ready to write as FactTrade.csv.

    For the trade preset the five canonical columns are selected and renamed to
    title-case to match FACT_COLUMNS.  For generic datasets all columns are
    preserved and each column name is title-cased.
    """
    trade_cols_lower = {"date", "country", "product", "direction", "value"}
    has_all_trade = all(c in data.columns for c in ("date", "country", "product", "direction", "value"))
    if has_all_trade:
        frame = data[["date", "country", "product", "direction", "value"]].copy()
        frame.columns = [name for name, _ in FACT_COLUMNS]
    else:
        frame = data.copy()
        frame.columns = [str(c)[0].upper() + str(c)[1:] for c in frame.columns]
    return frame


def insight_rows(findings: dict[str, Any]) -> list[dict[str, str]]:
    rows = []
    for finding in findings.get("findings", []) or []:
        text = finding.get("text") if isinstance(finding, dict) else str(finding)
        if text:
            rows.append({"Kind": "Finding", "Text": str(text)})
    for recommendation in findings.get("recommendations", []) or []:
        rows.append({"Kind": "Recommendation", "Text": str(recommendation)})
    if not rows:
        rows.append({"Kind": "Info", "Text": "No validated findings supplied"})
    period = findings.get("reporting_period")
    if period:
        rows.append({"Kind": "Reporting period", "Text": str(period)})
    return rows


def quality_rows(quality: dict[str, Any], data: pd.DataFrame, issues: list[ValidationIssue]) -> list[dict[str, str]]:
    rows = [
        {"Check": "Upstream quality status", "Result": str(quality.get("status", "not_provided"))},
        {"Check": "Rows loaded", "Result": str(len(data))},
    ]
    for check, result in sorted((quality.get("checks") or {}).items()):
        rows.append({"Check": f"Upstream check: {check}", "Result": str(result)})
    for issue in quality.get("issues", []) or []:
        rows.append({"Check": "Upstream issue", "Result": json.dumps(issue, sort_keys=True) if not isinstance(issue, str) else issue})
    for issue in issues:
        rows.append({"Check": f"Generator {issue.severity}: {issue.code}", "Result": f"{issue.message} ({issue.count} row(s))"})
    rows.append({"Check": "Power BI Desktop validation", "Result": "NOT_RUN"})
    return rows


def _csv_source(file_name: str) -> str:
    return f'Csv.Document(File.Contents(SourceFolder & "{file_name}"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv])'


def _typed_load(file_name: str, columns: tuple[tuple[str, str], ...]) -> list[str]:
    m_types = {"dateTime": "type date", "string": "type text", "double": "type number"}
    names = ", ".join(f'"{name}"' for name, _ in columns)
    types = ", ".join(f'{{"{name}", {m_types[dtype]}}}' for name, dtype in columns)
    return [
        "let",
        f"    Source = {_csv_source(file_name)},",
        "    PromotedHeaders = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),",
        # Select by name so extra or reordered CSV columns cannot shift the load.
        f"    Selected = Table.SelectColumns(PromotedHeaders, {{{names}}}),",
        f'    Typed = Table.TransformColumnTypes(Selected, {{{types}}}, "en-US")',
        "in",
        "    Typed",
    ]


def model_bim(measures: dict[str, str], source_dir: Path, bundle: Bundle | None = None) -> dict[str, Any]:
    # The CSV folder is a Power Query parameter (Transform data > Edit parameters)
    # so the project can be repointed without regenerating it.
    folder = str(source_dir.resolve()) + os.sep

    trade_mode = getattr(bundle, "trade_mode", True) if bundle is not None else True

    if trade_mode or bundle is None:
        fact_cols = FACT_COLUMNS
        fact_table_name = "FactTrade"
    else:
        fact_table_name = bundle.data_model.get("fact_table", "FactTrade")
        col_list = []
        for c in bundle.data.columns:
            c_title = str(c)[0].upper() + str(c)[1:]
            if pd.api.types.is_numeric_dtype(bundle.data[c]):
                dtype = "double"
            elif "date" in str(c).lower() or pd.api.types.is_datetime64_any_dtype(bundle.data[c]):
                dtype = "dateTime"
            else:
                dtype = "string"
            col_list.append((c_title, dtype))
        fact_cols = tuple(col_list)

    expressions = [
        {"name": "SourceFolder", "kind": "m", "expression": f'"{folder}" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]'},
        {"name": "TradeData", "kind": "m", "expression": _typed_load("FactTrade.csv", fact_cols)},
    ]

    def table(name: str, columns: tuple[tuple[str, str], ...], expression: list[str]) -> dict[str, Any]:
        return {
            "name": name,
            "columns": [{"name": col, "dataType": dtype, "sourceColumn": col} for col, dtype in columns],
            "partitions": [{"name": name, "mode": "import", "source": {"type": "m", "expression": expression}}],
        }

    def dimension(name: str, column: str, dtype: str) -> dict[str, Any]:
        return table(name, ((column, dtype),), ["let", f'    Source = Table.Distinct(Table.SelectColumns(TradeData, {{"{column}"}}))', "in", "    Source"])

    fact = table(fact_table_name, fact_cols, ["let", "    Source = TradeData", "in", "    Source"])
    fact["measures"] = [{"name": measure, "expression": expression} for measure, expression in measures.items()]

    if trade_mode or bundle is None:
        tables = [
            fact,
            dimension("DimDate", "Date", "dateTime"),
            dimension("DimCountry", "Country", "string"),
            dimension("DimProduct", "Product", "string"),
            table("BusinessInsights", INSIGHT_COLUMNS, _typed_load("BusinessInsights.csv", INSIGHT_COLUMNS)),
            table("DataQuality", QUALITY_COLUMNS, _typed_load("DataQuality.csv", QUALITY_COLUMNS)),
        ]
        relationships = [
            {"name": "FactTradeDate", "fromTable": "FactTrade", "fromColumn": "Date", "toTable": "DimDate", "toColumn": "Date"},
            {"name": "FactTradeCountry", "fromTable": "FactTrade", "fromColumn": "Country", "toTable": "DimCountry", "toColumn": "Country"},
            {"name": "FactTradeProduct", "fromTable": "FactTrade", "fromColumn": "Product", "toTable": "DimProduct", "toColumn": "Product"},
        ]
    else:
        tables = [fact]
        relationships = []
        classification = classify_data_model(bundle.data_model)
        for col_name, c_type in classification.items():
            c_title = col_name[0].upper() + col_name[1:]
            if c_title in [col for col, _ in fact_cols]:
                dim_name = f"Dim{c_title}"
                d_type = "dateTime" if c_type == "date" else "string"
                tables.append(dimension(dim_name, c_title, d_type))
                relationships.append({
                    "name": f"{fact_table_name}{c_title}",
                    "fromTable": fact_table_name,
                    "fromColumn": c_title,
                    "toTable": dim_name,
                    "toColumn": c_title,
                })
        tables.append(table("BusinessInsights", INSIGHT_COLUMNS, _typed_load("BusinessInsights.csv", INSIGHT_COLUMNS)))
        tables.append(table("DataQuality", QUALITY_COLUMNS, _typed_load("DataQuality.csv", QUALITY_COLUMNS)))

    return {"compatibilityLevel": 1567, "model": {"culture": "en-US", "defaultPowerBIDataSourceVersion": "powerBI_V3", "expressions": expressions, "tables": tables, "relationships": relationships}}


# Field specs: (kind, table, property). kind is Column, Measure, or Sum (aggregated column).
def column(table: str, name: str) -> tuple[str, str, str]:
    return ("Column", table, name)


def measure(name: str) -> tuple[str, str, str]:
    return ("Measure", "FactTrade", name)


def total(table: str, name: str) -> tuple[str, str, str]:
    return ("Sum", table, name)


def _projection(spec: tuple[str, str, str]) -> dict[str, Any]:
    kind, table, name = spec
    source = {"Expression": {"SourceRef": {"Entity": table}}, "Property": name}
    if kind == "Measure":
        return {"field": {"Measure": source}, "queryRef": f"{table}.{name}", "nativeQueryRef": name}
    if kind == "Sum":
        # Function 0 is SUM in the PBIR aggregation enum.
        return {"field": {"Aggregation": {"Expression": {"Column": source}, "Function": 0}}, "queryRef": f"Sum({table}.{name})", "nativeQueryRef": f"Sum of {name}"}
    return {"field": {"Column": source}, "queryRef": f"{table}.{name}", "nativeQueryRef": name}


def visual(visual_id: str, visual_type: str, roles: dict[str, list[tuple[str, str, str]]], title: str) -> dict[str, Any]:
    """Build a visual descriptor; `roles` maps PBIR role buckets (Category, Y, Series, Values) to field specs."""
    query_state = {role: {"projections": [_projection(spec) for spec in specs]} for role, specs in roles.items() if specs}
    for role in ("Category", "Rows"):
        # Desktop marks the axis field active, as in reports it saves itself.
        if role in query_state:
            query_state[role]["projections"][0]["active"] = True
    visual_config: dict[str, Any] = {"visualType": visual_type}
    if query_state:
        # prototypeQuery is NOT included â€” the Query schema has additionalProperties:false
        # so any unknown field silently invalidates the visual. Desktop derives the DAX
        # query from queryState + field expressions alone.
        visual_config["query"] = {"queryState": query_state}
    if title:
        # Title belongs in visualContainerObjects (container formatting), not in objects.
        visual_config["visualContainerObjects"] = {
            "title": [{"properties": {"show": {"expr": {"Literal": {"Value": "true"}}}, "text": {"expr": {"Literal": {"Value": f"'{title}'"}}}}}]
        }
    visual_config["drillFilterOtherVisuals"] = True
    return {"id": visual_id, "config": visual_config}


def pages(bundle: Bundle) -> list[dict[str, Any]]:
    trade_mode = getattr(bundle, "trade_mode", True)
    fact_table_name = "FactTrade" if trade_mode else bundle.data_model.get("fact_table", "FactTrade")
    measures = dax(bundle.kpis, fact_table=fact_table_name)
    names = {kpi.id: kpi.name for kpi in bundle.kpis if kpi.name in measures}
    cards = [visual(f"{kpi_id}_card", "card", {"Values": [("Measure", fact_table_name, name)]}, name) for kpi_id, name in names.items()]
    if getattr(bundle, "trade_mode", True):
        flow_measures = [measure(names[kpi_id]) for kpi_id in ("total_imports", "total_exports") if kpi_id in names]
        value = total("FactTrade", "Value")
        definitions = [
            ("Executive Overview", cards + [visual("trend", "lineChart", {"Category": [column("DimDate", "Date")], "Y": flow_measures or [value]}, "Import and Export Trends")]),
            ("Trade Performance", [
                visual("performance", "lineChart", {"Category": [column("DimDate", "Date")], "Series": [column("FactTrade", "Direction")], "Y": [value]}, "Imports versus Exports"),
                visual("direction", "slicer", {"Values": [column("FactTrade", "Direction")]}, "Trade Direction"),
            ]),
            ("Country Analysis", [
                visual("countries", "clusteredBarChart", {"Category": [column("DimCountry", "Country")], "Series": [column("FactTrade", "Direction")], "Y": [value]}, "Trade by Country"),
                visual("country_detail", "tableEx", {"Values": [column("DimCountry", "Country"), column("FactTrade", "Direction"), value]}, "Country Comparison"),
            ]),
            ("Product Analysis", [
                visual("products", "clusteredBarChart", {"Category": [column("DimProduct", "Product")], "Series": [column("FactTrade", "Direction")], "Y": [value]}, "Trade by Product"),
                visual("product_detail", "tableEx", {"Values": [column("DimProduct", "Product"), column("DimCountry", "Country"), value]}, "Product and Country Detail"),
            ]),
            ("Business Insights", [visual("insights", "tableEx", {"Values": [column("BusinessInsights", "Kind"), column("BusinessInsights", "Text")]}, "Validated Business Insights")]),
            ("Data Quality & Traceability", [visual("quality", "tableEx", {"Values": [column("DataQuality", "Check"), column("DataQuality", "Result")]}, "Data Quality Indicators")]),
        ]
    else:
        definitions = [
            ("Executive Overview", cards or [visual("overview_table", "tableEx", {"Values": [column("BusinessInsights", "Kind"), column("BusinessInsights", "Text")]}, "Overview")]),
            ("Business Insights", [visual("insights", "tableEx", {"Values": [column("BusinessInsights", "Kind"), column("BusinessInsights", "Text")]}, "Validated Business Insights")]),
            ("Data Quality & Traceability", [visual("quality", "tableEx", {"Values": [column("DataQuality", "Check"), column("DataQuality", "Result")]}, "Data Quality Indicators")]),
        ]
    report_pages: list[dict[str, Any]] = []
    for display_name, visuals in definitions:
        slug = re.sub(r"[^A-Za-z0-9]", "", display_name)
        report_pages.append({"name": slug, "displayName": display_name, "visualContainers": visuals})
    return report_pages


def _referenced_fields(field: dict[str, Any]) -> tuple[str, str, str] | None:
    if "Aggregation" in field:
        field = field["Aggregation"].get("Expression", {})
    for kind in ("Measure", "Column"):
        if kind in field:
            return kind, field[kind].get("Expression", {}).get("SourceRef", {}).get("Entity"), field[kind].get("Property")
    return None


def validate_artifacts(output: Path, expected_pages: list[str], expected_measures: list[str], bundle: Bundle | None = None) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    model_dir = output / "dashboard_generator.SemanticModel"
    required = [output / "dashboard_generator.pbip", output / "dashboard_generator.Report" / "definition.pbir", model_dir / "definition.pbism", model_dir / "model.bim"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        issues.append(ValidationIssue("MISSING_ARTIFACT", "error", "generated", None, [], len(missing), "reject", "Missing generated artifacts: " + ", ".join(missing), False))
        return issues
    if (model_dir / "definition").exists():
        issues.append(ValidationIssue("MODEL_FORMAT_AMBIGUOUS", "error", "SemanticModel", None, [], 1, "reject", "Both model.bim and a TMDL definition folder exist", False))
    try:
        bim = json.loads((model_dir / "model.bim").read_text(encoding="utf-8"))
        tables = {table["name"]: table for table in bim["model"]["tables"]}
        trade_mode = getattr(bundle, "trade_mode", True) if bundle is not None else True
        if trade_mode:
            if not {"FactTrade", "DimDate", "DimCountry", "DimProduct"}.issubset(tables):
                issues.append(ValidationIssue("MODEL_TABLE_MISMATCH", "error", "model.bim", None, [], 1, "reject", "model.bim tables do not match the documented star schema", False))
            fact_tbl = tables.get("FactTrade")
        else:
            fact_table_name = bundle.data_model.get("fact_table", "FactTrade") if bundle else "FactTrade"
            if fact_table_name not in tables and "FactTrade" not in tables:
                issues.append(ValidationIssue("MODEL_TABLE_MISMATCH", "error", "model.bim", None, [], 1, "reject", f"model.bim missing fact table '{fact_table_name}'", False))
            fact_tbl = tables.get(fact_table_name) or tables.get("FactTrade")

        actual_measures = {measure["name"] for measure in fact_tbl.get("measures", [])} if fact_tbl else set()
        missing_measures = set(expected_measures) - actual_measures
        if missing_measures:
            issues.append(ValidationIssue("MODEL_MEASURE_MISSING", "error", "model.bim", None, [], len(missing_measures), "reject", "Missing measures: " + ", ".join(sorted(missing_measures)), False))
        for table in tables.values():
            for partition in table.get("partitions", []):
                if any('File.Contents("' in line for line in partition["source"]["expression"]):
                    issues.append(ValidationIssue("SOURCE_PATH_HARDCODED", "error", "model.bim", None, [], 1, "reject", f"Partition {partition['name']} hardcodes its file path instead of using SourceFolder", False))
        relationships = bim["model"].get("relationships", [])
        required_relationship_keys = {"name", "fromTable", "fromColumn", "toTable", "toColumn"}
        for relationship in relationships:
            if not required_relationship_keys.issubset(relationship):
                issues.append(ValidationIssue("RELATIONSHIP_DEFINITION_INVALID", "error", "model.bim", None, [], 1, "reject", f"Relationship {relationship.get('name')} is missing a required endpoint", False))
        report = json.loads((output / "dashboard_generator.Report" / "definition.pbir").read_text(encoding="utf-8"))
        if "datasetReference" not in report:
            issues.append(ValidationIssue("REPORT_MODEL_REFERENCE_MISSING", "error", "definition.pbir", None, [], 1, "reject", "Report has no semantic model reference", False))
        pages_file = output / "dashboard_generator.Report" / "definition" / "pages"
        if not (pages_file / "pages.json").exists():
            issues.append(ValidationIssue("PAGES_METADATA_MISSING", "error", "pages", None, [], 1, "reject", "definition/pages/pages.json is missing", False))
        report_json = json.loads((output / "dashboard_generator.Report" / "definition" / "report.json").read_text(encoding="utf-8"))
        base_theme = report_json.get("themeCollection", {}).get("baseTheme", {}).get("name")
        if not base_theme or not (output / "dashboard_generator.Report" / "StaticResources" / "SharedResources" / "BaseThemes" / f"{base_theme}.json").exists():
            issues.append(ValidationIssue("REPORT_THEME_MISSING", "error", "report.json", None, [], 1, "reject", "Report has no base theme file", False))
        actual_pages = {path.name for path in pages_file.iterdir() if path.is_dir()}
        if actual_pages != set(expected_pages):
            issues.append(ValidationIssue("PAGE_SET_MISMATCH", "error", "pages", None, [], 1, "reject", "Generated pages do not match required pages", False))
        model_columns = {name: {col["name"] for col in table["columns"]} for name, table in tables.items()}
        model_measures = {name: {m["name"] for m in table.get("measures", [])} for name, table in tables.items()}
        if not trade_mode and fact_table_name != "FactTrade":
            if fact_table_name in model_measures:
                model_measures["FactTrade"] = model_measures[fact_table_name]
            elif "FactTrade" in model_measures:
                model_measures[fact_table_name] = model_measures["FactTrade"]
            if fact_table_name in model_columns:
                model_columns["FactTrade"] = model_columns[fact_table_name]
            elif "FactTrade" in model_columns:
                model_columns[fact_table_name] = model_columns["FactTrade"]
        for visual_file in sorted(pages_file.glob("*/visuals/*/visual.json")):
            visual_data = json.loads(visual_file.read_text(encoding="utf-8"))
            if "visual" not in visual_data or "visualType" not in visual_data["visual"]:
                issues.append(ValidationIssue("VISUAL_DEFINITION_INVALID", "error", str(visual_file), None, [], 1, "reject", "Visual is missing its visual type", False))
            query_state = visual_data.get("visual", {}).get("query", {}).get("queryState", {})
            for projection_state in query_state.values():
                for projection in projection_state.get("projections", []):
                    reference = _referenced_fields(projection.get("field", {}))
                    if reference is None:
                        issues.append(ValidationIssue("VISUAL_FIELD_INVALID", "error", str(visual_file), None, [], 1, "reject", "Projection has no column, measure or aggregation", False))
                        continue
                    kind, table_name, name = reference
                    known = model_measures if kind == "Measure" else model_columns
                    if name not in known.get(table_name, set()):
                        code = "VISUAL_MEASURE_REFERENCE_INVALID" if kind == "Measure" else "VISUAL_COLUMN_REFERENCE_INVALID"
                        issues.append(ValidationIssue(code, "error", str(visual_file), None, [], 1, "reject", f"Unknown {kind.lower()} reference: {table_name}.{name}", False))
    except (OSError, KeyError, TypeError, ValueError) as exc:
        issues.append(ValidationIssue("ARTIFACT_PARSE_ERROR", "error", "generated", None, [], 1, "reject", str(exc), False))
    return issues


def _desktop_available() -> bool:
    return bool(shutil.which("PBIDesktop.exe") or Path("C:/Program Files/Microsoft Power BI Desktop/bin/PBIDesktop.exe").exists())


def _artifact_manifest(output: Path) -> list[dict[str, Any]]:
    return [{"path": str(path.relative_to(output)), "bytes": path.stat().st_size} for path in sorted(output.rglob("*")) if path.is_file() and path.name not in {"validation_summary.json", "dashboard_generation_report.json", "artifact_manifest.json", "automated_test_results.json"}]


def _run_id(fact_csv: str, bundle: Bundle) -> str:
    """Derived from the inputs so identical inputs give the same run id."""
    digest = hashlib.sha256()
    digest.update(fact_csv.encode("utf-8"))
    digest.update(json.dumps([kpi.model_dump() for kpi in bundle.kpis], sort_keys=True).encode("utf-8"))
    digest.update(json.dumps([bundle.findings, bundle.quality], sort_keys=True, default=str).encode("utf-8"))
    return str(uuid.UUID(digest.hexdigest()[:32]))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def generate(
    bundle: Bundle,
    output: Path,
    demo: bool,
    input_issues: list[ValidationIssue] | None = None,
    plan: Any | None = None,
    critic_report: Any | None = None,
    export_html: bool = True,
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()
    data, mapping, issues = validate_frame(bundle.data, trade_mode=getattr(bundle, "trade_mode", True))
    # load_bundle already validated the raw file; re-validating normalized data would repeat its warnings.
    all_issues = list(input_issues) if input_issues is not None else issues
    skipped = not_visualised(bundle.kpis)
    for item in skipped:
        all_issues.append(ValidationIssue("KPI_NOT_VISUALISED", "warning", "kpi_catalog.json", None, [], 1, "accept_with_warning", f"{item['kpi_id']}: {item['reason']}", True))
    output.mkdir(parents=True, exist_ok=True)
    for path in (output / "dashboard_generator.Report", output / "dashboard_generator.SemanticModel", output / "dashboard_generator.source"):
        if path.exists():
            shutil.rmtree(path)
    for path in (output / "dashboard_generator.pbip",):
        if path.exists():
            path.unlink()
    report_dir, model_dir = output / "dashboard_generator.Report", output / "dashboard_generator.SemanticModel"
    report_def = report_dir / "definition"
    (report_def / "pages").mkdir(parents=True, exist_ok=True)
    model_dir.mkdir(parents=True, exist_ok=True)
    source_dir = output / "dashboard_generator.source"
    source_dir.mkdir(parents=True, exist_ok=True)
    fact_csv = _fact_csv(data).to_csv(index=False, date_format="%Y-%m-%d", lineterminator="\n")
    (source_dir / "FactTrade.csv").write_text(fact_csv, encoding="utf-8")
    pd.DataFrame(insight_rows(bundle.findings), columns=[name for name, _ in INSIGHT_COLUMNS]).to_csv(source_dir / "BusinessInsights.csv", index=False, lineterminator="\n")
    pd.DataFrame(quality_rows(bundle.quality, data, all_issues), columns=[name for name, _ in QUALITY_COLUMNS]).to_csv(source_dir / "DataQuality.csv", index=False, lineterminator="\n")
    run_id = _run_id(fact_csv, bundle)
    trade_mode = getattr(bundle, "trade_mode", True)
    fact_table_name = "FactTrade" if trade_mode else bundle.data_model.get("fact_table", "FactTrade")
    measures, expected = dax(bundle.kpis, fact_table=fact_table_name), python_kpis(bundle, data)
    _write_json(output / "dashboard_generator.pbip", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0.0",
        "artifacts": [{"report": {"path": "dashboard_generator.Report"}}],
        "settings": {"enableAutoRecovery": True},
    })
    _write_json(report_dir / "definition.pbir", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/1.0.0/schema.json",
        "version": "4.0",
        "datasetReference": {"byPath": {"path": "../dashboard_generator.SemanticModel"}},
    })
    # pbism 1.0.0 means the model is read from model.bim; no TMDL folder is written
    # so Desktop never sees two competing model definitions.
    _write_json(model_dir / "definition.pbism", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "1.0.0",
    })
    _write_json(model_dir / "model.bim", model_bim(measures, source_dir, bundle=bundle))
    
    if plan is not None:
        from src.agents.dashboard.dashboard_compiler_adapter import compile_plan_to_report_pages
        report_pages = compile_plan_to_report_pages(plan, bundle)
    else:
        report_pages = pages(bundle)

    measure_visuals: dict[str, list[tuple[str, str]]] = {}
    for page in report_pages:
        page_dir = report_def / "pages" / page["name"]
        (page_dir / "visuals").mkdir(parents=True, exist_ok=True)
        # Layout: cards are 300Ã—150, other visuals are 580Ã—260. Wrap at 1240px.
        CARD_W, CARD_H = 300.0, 150.0
        WIDE_W, WIDE_H = 580.0, 260.0
        MARGIN = 20.0
        cursor_x, cursor_y, row_height = MARGIN, MARGIN, 0.0
        for index, item in enumerate(page["visualContainers"]):
            visual_dir = page_dir / "visuals" / item["id"]
            visual_dir.mkdir(parents=True, exist_ok=True)
            for projection_state in item["config"].get("query", {}).get("queryState", {}).values():
                for projection in projection_state["projections"]:
                    if "Measure" in projection["field"]:
                        measure_visuals.setdefault(projection["field"]["Measure"]["Property"], []).append((page["name"], item["id"]))
            vtype = item["config"].get("visualType", "")
            is_card = vtype == "card"
            vw = CARD_W if is_card else WIDE_W
            vh = CARD_H if is_card else WIDE_H
            if cursor_x + vw > 1240 and cursor_x > MARGIN:
                cursor_y += row_height + MARGIN
                cursor_x = MARGIN
                row_height = 0.0
            tab_order = float((index + 1) * 1000)
            # visual.json â€” schema 2.12.0: position lives here, NOT in page.json.
            visual_json = {
                "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.12.0/schema.json",
                "name": item["id"],
                "position": {
                    "x": cursor_x, "y": cursor_y, "z": float(index),
                    "width": vw, "height": vh, "tabOrder": tab_order,
                },
                "visual": item["config"],
            }
            _write_json(visual_dir / "visual.json", visual_json)
            cursor_x += vw + MARGIN
            row_height = max(row_height, vh)
        # page.json â€” schema 2.1.0: page metadata only; Desktop auto-discovers
        # visuals from the visuals/* subdirectories. No visualContainers here.
        _write_json(page_dir / "page.json", {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json",
            "name": page["name"],
            "displayName": page["displayName"],
            "displayOption": "FitToPage",
            "width": 1280,
            "height": 720,
        })
    # PBIR definition version written by Power BI Desktop (definition.pbir stays at 4.0).
    _write_json(report_def / "version.json", {"$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/versionMetadata/1.0.0/schema.json", "version": "2.0.0"})
    page_names = [page["name"] for page in report_pages]
    # Desktop reads page order from pages/pages.json; without it no page is shown.
    _write_json(report_def / "pages" / "pages.json", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.1.0/schema.json",
        "pageOrder": page_names,
        "activePageName": page_names[0],
        "landingPageName": page_names[0],
    })
    # Desktop needs a base theme registered as a shared resource package, with
    # its file under StaticResources; an empty themeCollection leaves the canvas blank.
    theme_dir = report_dir / "StaticResources" / "SharedResources" / "BaseThemes"
    theme_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BASE_THEME_FILE, theme_dir / f"{BASE_THEME}.json")
    _write_json(report_def / "report.json", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/1.0.0/schema.json",
        "themeCollection": {"baseTheme": {"name": BASE_THEME, "reportVersionAtImport": "5.56", "type": "SharedResources"}},
        "layoutOptimization": "None",
        "resourcePackages": [{"name": "SharedResources", "type": "SharedResources", "items": [{"name": BASE_THEME, "path": f"BaseThemes/{BASE_THEME}.json", "type": "BaseTheme"}]}],
    })
    all_issues.extend(validate_artifacts(output, page_names, list(measures), bundle=bundle))
    desktop_available = _desktop_available()
    status = "VALIDATION_FAILED" if any(issue.severity == "error" for issue in all_issues) else "PARTIAL_SUCCESS"
    upstream_agent = bundle.lineage.get("upstream_agent", "not_provided") if isinstance(bundle.lineage, dict) else "not_provided"
    lineage = []
    for kpi in bundle.kpis:
        if kpi.name not in measures:
            continue
        placements = measure_visuals.get(kpi.name, [])
        lineage.append({
            "kpi_id": kpi.id, "definition": kpi.definition, "source_dataset": "cleaned_trade", "source_columns": kpi.source_columns,
            "semantic_model": {"table": "FactTrade", "measure": kpi.name, "dax": measures[kpi.name]}, "logic": kpi.expression,
            "report_pages": sorted({page for page, _ in placements}), "report_visuals": [visual_id for _, visual_id in placements],
            "lineage_scope": "KPI-to-page and generated visual references; source transformation lineage is limited to the documented input mapping",
            "upstream_agent": upstream_agent,
            "validation_status": "python_validated" if kpi.id in expected else "not_python_validated",
            "unresolved_issues": [issue.code for issue in all_issues],
        })

    # Render offline standalone HTML dashboard
    html_path = None
    if export_html:
        from src.agents.dashboard.html_renderer import render_html_dashboard
        from src.agents.dashboard.dashboard_planner import create_deterministic_plan
        effective_plan = plan or create_deterministic_plan(bundle)
        html_file = output / "dashboard.html"
        render_html_dashboard(
            plan=effective_plan,
            bundle=bundle,
            kpi_values=expected,
            output_path=html_file,
            critic_report=critic_report,
            run_id=run_id,
        )
        html_path = str(html_file.relative_to(output))

    result = {
        "agent_name": "dashboard_generator",
        "run_id": run_id,
        "generated_at": generated_at,
        "status": status,
        "planning_mode": plan.planning_mode if plan else "deterministic",
        "project_format": "PBIP",
        "project_path": str(output / "dashboard_generator.pbip"),
        "html_dashboard_path": html_path,
        "report_pages": [page["displayName"] for page in report_pages],
        "semantic_model": {
            "fact_table": "FactTrade",
            "dimensions": ["DimDate", "DimCountry", "DimProduct"],
            "support_tables": ["BusinessInsights", "DataQuality"],
            "measures": list(measures),
            "source_folder_parameter": "SourceFolder",
        },
        "visual_lineage": lineage,
        "not_visualised": skipped,
        "warnings": ["Power BI Desktop opening, refresh, DAX evaluation, and rendering were not executed by Python."],
        "errors": [issue.as_dict() for issue in all_issues if issue.severity == "error"],
        "validation_issues": [issue.as_dict() for issue in all_issues],
        "demo_mode": demo,
        "source_mapping": mapping,
        "expected_kpi_values": expected,
        "desktop_validation": {
            "available": desktop_available,
            "status": "NOT_RUN",
            "reason": "Desktop integration is an external test and was not executed by the generator.",
        },
        "dashboard_plan": plan.model_dump() if plan else None,
        "critic_report": critic_report.model_dump() if critic_report else None,
    }
    _write_json(output / "visual_lineage.json", lineage)
    _write_json(output / "kpi_catalog.generated.json", [kpi.model_dump() for kpi in bundle.kpis])
    _write_json(output / "dax_measures.json", measures)
    _write_json(output / "dashboard_generation_report.json", result)
    manifest = _artifact_manifest(output)
    validation_summary = {
        "run_id": run_id,
        "generated_at": generated_at,
        "generator": {"module": "dashboard_generator.py", "python": sys.version.split()[0], "pandas": pd.__version__},
        "planning_mode": result["planning_mode"],
        "input_files": ["cleaned_trade.csv", "data_model.json", "kpi_catalog.json", "bi_analyst_findings.json", "data_quality_report.json"],
        "kpi_expected": expected,
        "not_visualised": skipped,
        "structural_checks": {"issues": len(all_issues), "errors": len([issue for issue in all_issues if issue.severity == "error"]), "pages": len(report_pages), "measures": len(measures), "model_tables": 6, "relationships": 3},
        "desktop_validation": result["desktop_validation"],
        "artifact_manifest": manifest,
        "final_status": status,
        "known_limitations": [
            "Python structural checks do not prove Power BI Desktop refresh, DAX execution, or visual rendering.",
            "The documented input contract has no currency field or conversion policy.",
            "KPIs defined only by a catalog 'dax' field have no independent Python check.",
        ],
    }
    _write_json(output / "validation_summary.json", validation_summary)
    _write_json(output / "artifact_manifest.json", manifest)
    return result


def source_kpis(fact_csv: Path) -> dict[str, float]:
    """Recompute the KPIs from the CSV Power BI will load, with the csv module only."""
    import csv

    imports = exports = 0.0
    count = 0
    with fact_csv.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            count += 1
            if row["Direction"] == "Import":
                imports += float(row["Value"])
            elif row["Direction"] == "Export":
                exports += float(row["Value"])
    return {"total_imports": imports, "total_exports": exports, "trade_balance": exports - imports, "transaction_count": float(count)}


def test_generation(bundle: Bundle, result: dict[str, Any], output: Path) -> dict[str, Any]:
    data, _, issues = validate_frame(bundle.data, trade_mode=getattr(bundle, "trade_mode", True))
    from_source = source_kpis(output / "dashboard_generator.source" / "FactTrade.csv")
    checks = {
        "input_schema_valid": not any(issue.severity == "error" for issue in issues),
        "kpi_values_match_loaded_source_csv": all(abs(from_source[key] - value) < 1e-9 for key, value in result["expected_kpi_values"].items()),
        "required_pbip_artifacts": not any(issue["code"] == "MISSING_ARTIFACT" for issue in result["validation_issues"]),
        "semantic_model_consistent": not any(issue["code"] in {"MODEL_TABLE_MISMATCH", "MODEL_MEASURE_MISSING", "RELATIONSHIP_DEFINITION_INVALID", "MODEL_FORMAT_AMBIGUOUS", "SOURCE_PATH_HARDCODED"} for issue in result["validation_issues"]),
        "visual_bindings_present": not any(issue["code"] in {"VISUAL_DEFINITION_INVALID", "VISUAL_FIELD_INVALID", "VISUAL_MEASURE_REFERENCE_INVALID", "VISUAL_COLUMN_REFERENCE_INVALID", "PAGES_METADATA_MISSING", "REPORT_THEME_MISSING"} for issue in result["validation_issues"]),
        "lineage_written": (output / "visual_lineage.json").exists(),
        "external_power_bi_desktop_test": False,
    }
    report = {"checks": checks, "passed": all(value for key, value in checks.items() if key != "external_power_bi_desktop_test"), "external_test_required": True}
    _write_json(output / "automated_test_results.json", report)
    return report


def main(
    input_root: Path = Path("input"),
    output_root: Path = Path("generated"),
    demo_mode: bool = False,
    config: Any | None = None,
    user_context: dict[str, Any] | str | None = None,
) -> dict[str, Any]:
    try:
        from src.agents.dashboard.config import GeneratorConfig, OperatingMode
        from src.agents.dashboard.dashboard_planner import DashboardPlanner, create_deterministic_plan
        from src.agents.dashboard.dashboard_critic import DashboardCritic
        from src.agents.dashboard.knowledge_agent import KnowledgeAgent

        cfg = config or GeneratorConfig.from_env()

        # Load optional user_context.json or user_request.txt from input_root if present
        ctx = user_context
        if ctx is None:
            if (input_root / "user_context.json").exists():
                try:
                    ctx = json.loads((input_root / "user_context.json").read_text(encoding="utf-8"))
                except Exception:
                    ctx = None
            elif (input_root / "user_request.txt").exists():
                try:
                    ctx = (input_root / "user_request.txt").read_text(encoding="utf-8").strip()
                except Exception:
                    ctx = None

        bundle, issues = load_bundle(input_root, demo_mode)

        plan = None
        critic_report = None
        if cfg.mode != OperatingMode.DETERMINISTIC:
            retrieved_chunks = []
            if cfg.rag_enabled and cfg.mode == OperatingMode.AI_ASSISTED:
                agent = KnowledgeAgent(cfg.knowledge_dir)
                if isinstance(ctx, dict):
                    query = ctx.get("objective", "Trade overview and key performance indicators")
                elif isinstance(ctx, str) and ctx.strip():
                    query = ctx.strip()
                else:
                    query = "Trade overview and key performance indicators"
                retrieved_chunks = agent.retrieve(query, top_k=cfg.max_retrieved_chunks)

            planner = DashboardPlanner(cfg)
            candidate_plan = planner.plan(bundle, ctx, retrieved_chunks)
            critic = DashboardCritic()
            critic_report = critic.evaluate(candidate_plan, bundle)

            if critic_report.passed_deterministic:
                plan = candidate_plan
            else:
                # Controlled fallback to deterministic plan
                plan = create_deterministic_plan(bundle, ctx)
                plan.planning_mode = "deterministic_fallback"
                plan.warnings.append(f"Candidate plan failed critic validation: {'; '.join(critic_report.errors)}")

        generation = generate(
            bundle,
            output_root,
            demo_mode,
            input_issues=issues,
            plan=plan,
            critic_report=critic_report,
            export_html=cfg.export_html,
        )
        tests = test_generation(bundle, generation, output_root)
        print(json.dumps({"result": generation, "tests": tests}, indent=2))
        return generation
    except (FileNotFoundError, ValueError, OSError) as exc:
        issue_data = [issue.as_dict() for issue in exc.issues] if isinstance(exc, InputValidationError) else [{"code": "GENERATION_FAILED", "message": str(exc)}]
        failure = {"agent_name": "dashboard_generator", "status": "VALIDATION_FAILED", "project_format": "PBIP", "project_path": None, "report_pages": [], "semantic_model": {}, "visual_lineage": [], "warnings": [], "errors": issue_data}
        output_root.mkdir(parents=True, exist_ok=True)
        _write_json(output_root / "dashboard_generation_report.json", failure)
        print(json.dumps(failure, indent=2))
        return failure


def run_dashboard(
    input_dir: Path,
    output_dir: Path,
    config: Any | None = None,
) -> dict[str, Any]:
    """LangGraph-compatible entry point for the Dashboard Generator.

    Returns a JSON-serialisable dict with keys:
    ``status``, ``mode``, ``selected_plan``, ``score``, ``candidates``,
    ``artifacts``, ``warnings``, ``errors``.
    Exceptions are caught and surfaced in ``errors``; never raises.
    """
    warnings_out: list[str] = []
    errors_out: list[str] = []

    try:
        from src.agents.dashboard.config import GeneratorConfig, OperatingMode
        from src.agents.dashboard.dashboard_planner import DashboardPlanner, create_deterministic_plan
        from src.agents.dashboard.dashboard_critic import DashboardCritic
        from src.agents.dashboard.knowledge_agent import KnowledgeAgent

        cfg: Any = config or GeneratorConfig()

        # â”€â”€ Load inputs â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        bundle, issues = load_bundle(Path(input_dir), demo=False)
        for issue in issues:
            msg = f"{issue.code}: {issue.message}"
            if issue.severity == "warning":
                warnings_out.append(msg)
            elif issue.severity == "error":
                errors_out.append(msg)

        # â”€â”€ Plan â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        plan = None
        critic_report = None
        score: float | None = None
        candidates_scores: list[float] = []
        mode_value = getattr(getattr(cfg, "mode", None), "value", "deterministic")

        if getattr(cfg, "mode", None) != OperatingMode.DETERMINISTIC:
            retrieved_chunks: list[Any] = []
            if getattr(cfg, "rag_enabled", True) and getattr(cfg, "mode", None) == OperatingMode.AI_ASSISTED:
                knowledge_dir = getattr(cfg, "knowledge_dir",
                                        Path(__file__).resolve().parents[3] / "resources" / "knowledge")
                agent = KnowledgeAgent(Path(knowledge_dir))
                retrieved_chunks = agent.retrieve(
                    "dashboard overview", top_k=getattr(cfg, "max_retrieved_chunks", 3))

            planner = DashboardPlanner(cfg)
            candidate_plan = planner.plan(bundle, None, retrieved_chunks)
            critic = DashboardCritic()
            critic_report = critic.evaluate(candidate_plan, bundle)

            if critic_report.passed_deterministic:
                plan = candidate_plan
            else:
                plan = create_deterministic_plan(bundle)
                plan.planning_mode = "deterministic_fallback"
                plan.warnings.append(
                    f"Candidate plan failed critic: {'; '.join(critic_report.errors)}")
                warnings_out.append("Planner fallback to deterministic: critic rejected AI plan")

            # Extract scores from evaluator metadata if present
            eval_summary = plan.metadata.get("evaluation_summary")
            if eval_summary and hasattr(eval_summary, "evaluations"):
                candidates_scores = [float(e.total_score) for e in eval_summary.evaluations]
                score = float(plan.metadata.get("score", 0.0))

        # â”€â”€ Generate â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        output_dir_path = Path(output_dir)
        generation = generate(
            bundle,
            output_dir_path,
            demo=False,
            input_issues=issues,
            plan=plan,
            critic_report=critic_report,
            export_html=getattr(cfg, "export_html", True),
        )

        for w in generation.get("warnings", []):
            if isinstance(w, str) and w not in warnings_out:
                warnings_out.append(w)
        for e in generation.get("errors", []):
            msg = e.get("message", str(e)) if isinstance(e, dict) else str(e)
            if msg not in errors_out:
                errors_out.append(msg)
        for skipped in generation.get("not_visualised", []):
            warnings_out.append(f"KPI '{skipped['kpi_id']}' skipped: {skipped['reason']}")

        # â”€â”€ Artifacts (paths as strings) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        effective_plan = plan or create_deterministic_plan(bundle)
        effective_critic = critic_report or DashboardCritic().evaluate(effective_plan, bundle)

        pbip = output_dir_path / "dashboard_generator.pbip"
        html = output_dir_path / "dashboard.html"
        artifacts: dict[str, Any] = {
            "pbip_dir": str(pbip) if pbip.exists() else None,
            "html_path": str(html) if html.exists() else None,
            "plan_json": None,
            "critic_report": None,
        }
        plan_path = output_dir_path / "dashboard_plan.json"
        try:
            _write_json(plan_path, effective_plan.model_dump())
            artifacts["plan_json"] = str(plan_path)
        except Exception as exc2:
            warnings_out.append(f"Could not write dashboard_plan.json: {exc2}")

        cr_path = output_dir_path / "critic_report.json"
        try:
            _write_json(cr_path, effective_critic.model_dump())
            artifacts["critic_report"] = str(cr_path)
        except Exception as exc2:
            warnings_out.append(f"Could not write critic_report.json: {exc2}")

        return {
            "status": generation.get("status", "UNKNOWN"),
            "mode": mode_value,
            "selected_plan": plan.planning_mode if plan else "deterministic",
            "score": score,
            "candidates": candidates_scores,
            "artifacts": artifacts,
            "warnings": warnings_out,
            "errors": errors_out,
        }

    except Exception as exc:
        logger.exception("run_dashboard failed: %s", exc)
        errors_out.append(str(exc))
        return {
            "status": "ERROR",
            "mode": "unknown",
            "selected_plan": None,
            "score": None,
            "candidates": [],
            "artifacts": {
                "pbip_dir": None, "html_path": None,
                "plan_json": None, "critic_report": None,
            },
            "warnings": warnings_out,
            "errors": errors_out,
        }


if __name__ == "__main__":
    main()

