from __future__ import annotations

import pandas as pd
import pytest

from src.agents.dashboard.dashboard_generator import InputValidationError, validate_frame
from tests.conftest import frame


def test_valid_input_passes():
    df = frame([["2024-01-01", "DE", "Machinery", "Import", 1000.0]])
    normalized, mapping, issues = validate_frame(df)
    assert not normalized.empty
    assert len(normalized) == 1
    assert mapping["country"] == "country"
    assert not any(i.severity == "error" for i in issues)


def test_missing_required_column_raises():
    df = pd.DataFrame([{"date": "2024-01-01", "country": "DE", "product": "Machinery", "direction": "Import"}])
    with pytest.raises(InputValidationError) as exc:
        validate_frame(df)
    assert "MISSING_REQUIRED_COLUMN" in [i.code for i in exc.value.issues]


def test_empty_dataframe_raises():
    df = pd.DataFrame(columns=["date", "country", "product", "direction", "value"])
    with pytest.raises(InputValidationError) as exc:
        validate_frame(df)
    assert "EMPTY_DATASET" in [i.code for i in exc.value.issues]


def test_invalid_date_raises():
    df = frame([["not-a-date", "DE", "Machinery", "Import", 1000.0]])
    with pytest.raises(InputValidationError) as exc:
        validate_frame(df)
    assert "INVALID_DATE" in [i.code for i in exc.value.issues]


def test_invalid_numeric_value_raises():
    df = frame([["2024-01-01", "DE", "Machinery", "Import", "one-thousand"]])
    with pytest.raises(InputValidationError) as exc:
        validate_frame(df)
    assert "INVALID_NUMERIC_VALUE" in [i.code for i in exc.value.issues]


def test_invalid_direction_domain_raises():
    df = frame([["2024-01-01", "DE", "Machinery", "Transit", 1000.0]])
    with pytest.raises(InputValidationError) as exc:
        validate_frame(df)
    assert "INVALID_DIRECTION" in [i.code for i in exc.value.issues]


def test_extra_columns_accepted_with_warning():
    df = frame([["2024-01-01", "DE", "Machinery", "Import", 1000.0]])
    df["extra_metadata"] = "custom_field"
    normalized, _, issues = validate_frame(df)
    assert "extra_metadata" in normalized.columns
    assert any(i.code == "UNEXPECTED_COLUMNS" and i.severity == "warning" for i in issues)


def test_non_positive_value_accepted_with_warning():
    df = frame([["2024-01-01", "DE", "Machinery", "Import", 0.0]])
    _, _, issues = validate_frame(df)
    assert any(i.code == "NON_POSITIVE_VALUE" and i.severity == "warning" for i in issues)


def test_duplicate_rows_accepted_with_warning():
    df = frame([
        ["2024-01-01", "DE", "Machinery", "Import", 100.0],
        ["2024-01-01", "DE", "Machinery", "Import", 100.0],
    ])
    _, _, issues = validate_frame(df)
    assert any(i.code == "DUPLICATE_TRANSACTION" and i.severity == "warning" for i in issues)


def test_multiple_currencies_rejected():
    df = frame([
        ["2024-01-01", "DE", "Machinery", "Import", 100.0],
        ["2024-01-02", "US", "Machinery", "Export", 200.0],
    ])
    df["currency"] = ["EUR", "USD"]
    with pytest.raises(InputValidationError) as exc:
        validate_frame(df)
    assert "MULTIPLE_CURRENCIES" in [i.code for i in exc.value.issues]

