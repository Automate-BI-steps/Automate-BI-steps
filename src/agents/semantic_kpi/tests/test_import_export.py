"""L'agent doit marcher sur un dataset import/export (cas métier du rapport), sans changer le code."""
import pandas as pd
import pytest

from agent import run


@pytest.fixture
def etat(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    pd.DataFrame({"year": [2020, 2020, 2020, 2021, 2021, 2021],
                  "flow": ["Export", "Export", "Import", "Export", "Export", "Import"],
                  "country": ["FR", "DE", "FR", "FR", "DE", "DE"],
                  "hs_code": [901, 901, 902, 901, 902, 902],
                  "value_usd": [100.0, 50.0, 90.0, 180.0, 60.0, 120.0]}).to_csv(tmp_path / "trade.csv", index=False)
    return run(str(tmp_path))


def test_columns_are_understood(etat):
    m = etat["semantic_mapping"]
    assert (m["date"], m["flow"], m["country"], m["product"], m["value"]) == \
           ("year", "flow", "country", "hs_code", "value_usd")


def test_report_kpis_are_computed(etat):
    r = etat["kpi_results"]
    assert r["total_exports"]["valeur"] == 390.0 and r["total_imports"]["valeur"] == 210.0
    assert r["trade_balance"]["valeur"] == 180.0
    assert r["coverage_ratio"]["valeur"] == 185.71
    assert r["yoy_growth"]["valeur"]["croissance_pct"] == {2021: 50.0}     # 240 -> 360


def test_kpis_without_needed_columns_are_skipped_with_reason(etat):
    assert "order_count" not in etat["kpi_results"]
    ignores = next(e["ignores"] for e in etat["audit_log"] if e["etape"] == "calculer_kpi")
    assert "order_count" in ignores


def test_catalog_explains_each_kpi(etat):
    for k in etat["kpi_catalog"]:
        assert k["bo"] == "BO3" and k["pourquoi"] and k["dax"] and k["origine"]


def test_balance_check_passes(etat):
    assert any(c["controle"] == "solde = exports - imports" and c["statut"] == "ok" for c in etat["kpi_checks"])
