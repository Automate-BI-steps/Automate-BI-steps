"""Tests de justesse des KPI (« KPI accuracy » du rapport).
Le petit jeu de données est calculé à la main : on connaît les bons résultats."""
import os

import pandas as pd
import pytest

from kpis import CATALOG, standardise
from mapping import map_columns

KPI = {k.id: k for k in CATALOG}
MAPPING = {"order_id": "orderID", "date": "orderDate", "shipped_date": "shippedDate",
           "required_date": "requiredDate", "country": "country", "product": "productName",
           "value": "lineTotal"}


@pytest.fixture
def d():
    """2 commandes, 3 lignes. CA = 350. Commande 1 : 2 lignes (150). Commande 2 : 1 ligne (200)."""
    df = pd.DataFrame({
        "orderID": [1, 1, 2],
        "orderDate": ["2013-07-01", "2013-07-01", "2014-07-10"],
        "requiredDate": ["2013-07-20", "2013-07-20", "2014-07-15"],
        "shippedDate": ["2013-07-05", "2013-07-05", "2014-07-20"],
        "country": ["FR", "FR", "DE"], "productName": ["A", "B", "A"],
        "lineTotal": [100.0, 50.0, 200.0]})
    return standardise(df, MAPPING)


def calc(d, kid):
    return KPI[kid].compute(d)


def test_revenue_orders_basket(d):
    assert calc(d, "total_revenue") == 350.0
    assert calc(d, "order_count") == 2                 # 3 lignes mais 2 commandes
    assert calc(d, "avg_order_value") == 175.0


def test_lead_time_counts_each_order_once(d):
    assert calc(d, "avg_lead_time") == 7.0             # (4 jours + 10 jours) / 2 commandes


def test_on_time_rate(d):
    assert calc(d, "on_time_rate") == 50.0             # commande 1 à temps, commande 2 en retard


def test_top_country(d):
    top = calc(d, "top_countries")
    assert top[0]["nom"] == "DE" and top[0]["part_pct"] == 57.14


def test_yoy_uses_same_months(d):
    assert calc(d, "yoy_growth")["croissance_pct"] == {2014: 33.33}   # 150 -> 200 (juillet)
    assert calc(d, "yoy_growth")["mois_couverts"] == {2013: 1, 2014: 1}


def test_import_export_kpis():
    df = pd.DataFrame({"flux": ["Export", "Import", "Export"], "montant": [100.0, 200.0, 200.0]})
    x = standardise(df, {"flow": "flux", "value": "montant"})
    assert calc(x, "trade_balance") == 100.0           # 300 - 200
    assert calc(x, "coverage_ratio") == 150.0          # 300 / 200


def test_mapping_finds_roles():
    df = pd.DataFrame({"orderID": [1], "orderDate": ["2013-07-01"], "shippedDate": ["2013-07-05"],
                       "country": ["FR"], "lineTotal": [100.0]})
    m, *_ = map_columns(df)
    assert m["value"] == "lineTotal" and m["date"] == "orderDate" and m["shipped_date"] == "shippedDate"


@pytest.mark.skipif(not os.getenv("BIFLOW_DATA"), reason="BIFLOW_DATA non défini")
def test_reference_values_on_real_dataset(tmp_path, monkeypatch):
    """Valeurs recalculées à part avec du pandas brut."""
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    from agent import run
    r = run(os.environ["BIFLOW_DATA"])["kpi_results"]
    assert r["total_revenue"]["valeur"] == pytest.approx(1265793.04, abs=0.01)
    assert r["order_count"]["valeur"] == 830
    assert r["avg_lead_time"]["valeur"] == pytest.approx(8.49, abs=0.01)
    assert r["on_time_rate"]["valeur"] == pytest.approx(95.43, abs=0.01)
    assert r["top_countries"]["valeur"][0]["nom"] == "USA"
    from verify_kpis import verifier           # KPI accuracy : agent contre recalcul indépendant
    lignes = verifier(r, os.environ["BIFLOW_DATA"])
    assert lignes and all(x["statut"] == "identique" for x in lignes)
