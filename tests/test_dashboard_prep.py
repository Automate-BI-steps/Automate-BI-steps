"""Agent 5 (Dashboard, code de Hashem non modifié) : adaptateur d'entrée src/agents/dashboard/prep.py + étape step.py."""
import json
from pathlib import Path

import pandas as pd
import pytest

from src.agents.dashboard.dashboard_generator import load_bundle
from src.agents.dashboard.prep import (DashboardInputError, build_input, convert_catalog, load_upstream, rewrite_dax,
                                       trade_frame)
from src.agents.dashboard.step import run_dashboard_step

COLS = {"orderdate": "OrderDate", "country": "Country", "productname": "ProductName", "lineTotal".lower(): "LineTotal"}


def _kpi(kid, nom, dax, cols):
    return {"id": kid, "nom": nom, "definition": f"def {nom}", "formule": f"formule {nom}", "dax": dax,
            "colonnes_sources": cols, "origine": {c: "table source" for c in cols}}


def make_state(tmp_path, rows, mapping, catalog, results=None):
    """State minimal tel que l'Agent 4 le laisse : table à plat + catalogue + constats + contrôles."""
    out = tmp_path / "run" / "semantic_kpi"
    out.mkdir(parents=True)
    pd.DataFrame(rows).to_csv(out / "fact_trade.csv", index=False)
    (out / "kpi_catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    return {"run": {"run_dir": str(tmp_path / "run")},
            "cleaning": {"summary": {"status": "WARN", "steps_applied": 3, "values_changed": 5, "rows_removed": 0},
                         "verification": {"score_before": 90.0, "score_after": 95.0},
                         "issue_resolution": {"items": [{"table": "orders", "column": "freight", "issue_type": "outlier",
                                                         "still_present": True, "status_label": "Reste à traiter"}]}},
            "kpi": {"status": "done", "fact_table_path": str(out / "fact_trade.csv"), "outputs": {"kpi_catalog": str(out / "kpi_catalog.json")},
                    "semantic_mapping": mapping, "kpi_results": results or {},
                    "kpi_analysis": [{"kpi_id": "total_revenue", "nom": "Total Revenue", "constat": "Le CA est de 100.", "niveau": "info"}],
                    "kpi_checks": [{"controle": "montants négatifs", "detail": "0", "statut": "ok"}]}}


SALES_ROWS = [{"orderID": i, "orderDate": f"2024-0{1 + i % 3}-10", "country": c, "productName": p, "lineTotal": 100.0 + i}
              for i, (c, p) in enumerate([("FR", "A"), ("DE", "B"), ("FR", "B"), ("US", "A")])]
SALES_MAP = {"order_id": "orderID", "date": "orderDate", "country": "country", "product": "productName", "value": "lineTotal"}
SALES_CAT = [_kpi("total_revenue", "Total Revenue", "SUM(Fact_Trade[lineTotal])", ["lineTotal"]),
             _kpi("order_count", "Order Count", "DISTINCTCOUNT(Fact_Trade[orderID])", ["orderID"]),
             _kpi("avg_order_value", "Average Order Value", "DIVIDE([Total Revenue], [Order Count])", ["lineTotal", "orderID"]),
             _kpi("yoy_growth", "YoY Growth", "CALCULATE(SUM(Fact_Trade[lineTotal]), SAMEPERIODLASTYEAR(Dim_Date[Date]))", ["lineTotal"]),
             _kpi("growth_of_growth", "Growth of Growth", "[YoY Growth] * 2", ["lineTotal"])]
SALES_RES = {"total_revenue": {"valeur": 406.0}, "order_count": {"valeur": 4}, "avg_order_value": {"valeur": 101.5},
             "yoy_growth": {"valeur": {"x": 1}}}


# ------------------------------------------------------------------ formules DAX
def test_rewrite_dax_resout_les_references_du_modele():
    new, why = rewrite_dax('CALCULATE(SUM(Fact_Trade[lineTotal]), Fact_Trade[flow] = "Export")', "Fact_Trade", "FactTrade",
                           {"linetotal": "Value", "flow": "Direction"}, set())
    assert why is None and new == 'CALCULATE(SUM(FactTrade[Value]), FactTrade[Direction] = "Export")'


@pytest.mark.parametrize("dax, fragment", [
    ("SAMEPERIODLASTYEAR(Dim_Date[Date])", "table absente"),
    ("SUM(Fact_Trade[inconnue])", "colonne absente"),
    ("DIVIDE([Autre], 2)", "mesure [Autre] absente"),
])
def test_rewrite_dax_refuse_ce_qui_ne_se_charge_pas(dax, fragment):
    new, why = rewrite_dax(dax, "Fact_Trade", "Fact_Trade", {"linetotal": "LineTotal"}, set())
    assert new is None and fragment in why


def test_un_kpi_qui_depend_d_un_kpi_refuse_est_refuse_aussi():
    fact = pd.DataFrame(SALES_ROWS)
    columns = {str(c).lower(): str(c)[0].upper() + str(c)[1:] for c in fact.columns}
    kpis, skipped = convert_catalog(SALES_CAT, columns, False, "Fact_Trade", "Fact_Trade", {})
    assert {s["kpi_id"] for s in skipped} == {"yoy_growth", "growth_of_growth"}
    ok = {k["id"]: k for k in kpis if k["supported"]}
    assert ok["total_revenue"]["dax"] == "SUM(Fact_Trade[LineTotal])"
    assert ok["avg_order_value"]["dax"] == "DIVIDE([Total Revenue], [Order Count])"


# ------------------------------------------------------------------ choix du contrat
TRADE_ROWS = [{"orderDate": "2024-01-05", "country": "FR", "productName": "A", "flow": "Export", "lineTotal": 500.0},
              {"orderDate": "2024-01-06", "country": "DE", "productName": "B", "flow": "import", "lineTotal": 300.0},
              {"orderDate": "2024-02-07", "country": "FR", "productName": "B", "flow": "EXPORT", "lineTotal": 200.0}]
TRADE_MAP = {"date": "orderDate", "country": "country", "product": "productName", "flow": "flow", "value": "lineTotal"}


def test_trade_frame_accepte_un_contrat_import_export_et_normalise_le_flux():
    frame, rename, why = trade_frame(pd.DataFrame(TRADE_ROWS), TRADE_MAP)
    assert why is None and list(frame.columns) == ["date", "country", "product", "direction", "value"]
    assert list(frame["direction"]) == ["Export", "Import", "Export"] and rename["lineTotal"] == "value"


def test_trade_frame_refuse_sans_role_flux():
    frame, _, why = trade_frame(pd.DataFrame(SALES_ROWS), SALES_MAP)
    assert frame is None and "flow" in why


def test_trade_frame_refuse_un_flux_inconnu():
    rows = [{**r, "flow": "autre"} for r in TRADE_ROWS]
    frame, _, why = trade_frame(pd.DataFrame(rows), TRADE_MAP)
    assert frame is None and "refusée par le générateur" in why


# ------------------------------------------------------------------ dossier d'entrée lu par le générateur
def test_build_input_generique_est_lisible_par_le_generateur(tmp_path):
    state = make_state(tmp_path, SALES_ROWS, SALES_MAP, SALES_CAT, SALES_RES)
    prep = build_input(state, tmp_path / "run" / "dashboard_input")
    assert prep["input_mode"] == "generic"
    assert prep["config"]["feedback_path"].startswith(str(tmp_path / "run"))  # le journal de retours reste dans le run, pas dans le dépôt
    assert {s["kpi_id"] for s in prep["kpis_skipped"]} == {"yoy_growth", "growth_of_growth"}
    bundle, issues = load_bundle(tmp_path / "run" / "dashboard_input", demo=False)
    assert bundle.trade_mode is False and len(bundle.data) == 4
    assert [k.id for k in bundle.kpis if k.supported] == ["total_revenue", "order_count", "avg_order_value"]
    assert bundle.findings["findings"][0]["text"] == "Le CA est de 100." and bundle.findings["reporting_period"] != "unavailable"
    assert bundle.quality["checks"]["score_qualite_apres_nettoyage"] == 95.0 and bundle.quality["issues"]
    assert bundle.lineage["upstream_agent"] == "semantic_kpi"
    assert not [i for i in issues if i.severity == "error"]
    # seules les colonnes qualitatives et les dates deviennent des dimensions (ni montants ni identifiants)
    assert set(json.loads((tmp_path / "run/dashboard_input/data_model.json").read_text())["columns"]) == {"orderDate", "country", "productName"}


def test_build_input_trade_utilise_le_contrat_import_export(tmp_path):
    cat = [_kpi("total_exports", "Total Exports", 'CALCULATE(SUM(Fact_Trade[lineTotal]), Fact_Trade[flow] = "Export")', ["lineTotal", "flow"]),
           _kpi("total_imports", "Total Imports", 'CALCULATE(SUM(Fact_Trade[lineTotal]), Fact_Trade[flow] = "Import")', ["lineTotal", "flow"]),
           _kpi("trade_balance", "Trade Balance", "[Total Exports] - [Total Imports]", ["lineTotal", "flow"]),
           _kpi("total_revenue", "Total Revenue", "SUM(Fact_Trade[lineTotal])", ["lineTotal"])]
    state = make_state(tmp_path, TRADE_ROWS, TRADE_MAP, cat)
    prep = build_input(state, tmp_path / "run" / "dashboard_input")
    assert prep["input_mode"] == "trade" and not prep["kpis_skipped"]
    by_id = {k["id"]: k for k in prep["kpis"]}
    assert by_id["total_exports"]["dax"] is None  # formule intégrée au générateur
    assert by_id["total_revenue"]["dax"] == "SUM(FactTrade[Value])"
    bundle, _ = load_bundle(tmp_path / "run" / "dashboard_input", demo=False)
    assert bundle.trade_mode is True and set(bundle.data["direction"]) == {"Export", "Import"}


def test_sans_table_a_plat_de_l_agent_4_l_etape_echoue_proprement(tmp_path):
    with pytest.raises(DashboardInputError):
        load_upstream({"kpi": {"status": "error"}})
    res = run_dashboard_step({"run": {"run_dir": str(tmp_path)}, "kpi": {"status": "error"}})
    assert res["dashboard"]["status"] == "ERROR" and res["errors"][0]["agent"] == "dashboard"


# ------------------------------------------------------------------ étape complète
def test_etape_complete_generique_affiche_les_valeurs_publiees_par_l_agent_4(tmp_path):
    state = make_state(tmp_path, SALES_ROWS, SALES_MAP, SALES_CAT, SALES_RES)
    res = run_dashboard_step(state)
    d = res["dashboard"]
    assert d["status"] == "PARTIAL_SUCCESS" and not d["errors"] and d["input_mode"] == "generic"
    report = json.loads((Path(d["output_dir"]) / "dashboard_generation_report.json").read_text(encoding="utf-8"))
    assert 406 in report["expected_kpi_values"].values() and 101.5 in report["expected_kpi_values"].values()  # valeurs de l'Agent 4
    out = sorted(p.name for p in Path(d["output_dir"]).iterdir())                     # plus de HTML ni de fichiers de diagnostic
    assert out == ["dashboard_generation_report.json", "dashboard_generator.Report", "dashboard_generator.SemanticModel",
                   "dashboard_generator.pbip", "dashboard_generator.source", "dashboard_plan.json"]
    assert d["artifacts"]["project_zip"].endswith("dashboard_project.zip")
    assert any("yoy_growth" in w for w in d["prep_warnings"])
    assert res["timeline"][0]["agent"] == "dashboard" and res["timeline"][0]["event"] == "done"



def test_etape_complete_trade_calcule_les_valeurs_avec_les_formules_du_generateur(tmp_path):
    cat = [_kpi("total_exports", "Total Exports", "x", ["lineTotal", "flow"]), _kpi("total_imports", "Total Imports", "x", ["lineTotal", "flow"]),
           _kpi("trade_balance", "Trade Balance", "x", ["lineTotal", "flow"])]
    res = run_dashboard_step(make_state(tmp_path, TRADE_ROWS, TRADE_MAP, cat))
    d = res["dashboard"]
    assert d["status"] in ("OK", "PARTIAL_SUCCESS") and d["input_mode"] == "trade" and not d["errors"]
    expected = json.loads((Path(d["output_dir"]) / "dashboard_generation_report.json").read_text(encoding="utf-8"))["expected_kpi_values"]
    assert expected["total_exports"] == 700 and expected["total_imports"] == 300  # exports 500+200, imports 300


# ------------------------------------------------------------------ lien avec Power BI Desktop
def _source_folder(project):
    model = json.loads(next(project.glob("*.SemanticModel/model.bim")).read_text(encoding="utf-8"))
    return next(e for e in model["model"]["expressions"] if e["name"] == "SourceFolder")["expression"]


def test_le_projet_livre_contient_le_script_de_liaison_et_le_mode_d_emploi(tmp_path):
    import zipfile
    res = run_dashboard_step(make_state(tmp_path, SALES_ROWS, SALES_MAP, SALES_CAT, SALES_RES))
    with zipfile.ZipFile(res["dashboard"]["artifacts"]["project_zip"]) as z:
        names = set(z.namelist())
    assert {"relier_power_bi.py", "LIRE_MOI_POWER_BI.md", "dashboard_generator.pbip", "dashboard_generator.source/FactTrade.csv"} <= names


def test_relier_power_bi_remet_source_folder_sur_le_dossier_deplace(tmp_path):
    import shutil
    import subprocess
    import sys
    res = run_dashboard_step(make_state(tmp_path, SALES_ROWS, SALES_MAP, SALES_CAT, SALES_RES))
    moved = tmp_path / "ailleurs" / "projet"
    shutil.unpack_archive(res["dashboard"]["artifacts"]["project_zip"], moved)   # relier_power_bi.py n'est que dans le zip
    assert not (Path(res["dashboard"]["output_dir"]) / "relier_power_bi.py").exists()  # aucun .py dans outputs/ (rechargement uvicorn)
    assert str(tmp_path / "run") in _source_folder(moved)  # chemin d'origine, absolu
    cmd = [sys.executable, str(moved / "relier_power_bi.py")]
    out = subprocess.run(cmd, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    expr = _source_folder(moved)
    folder = str(moved.resolve() / "dashboard_generator.source") + __import__("os").sep
    assert expr.startswith('"' + folder + '" meta [IsParameterQuery=true') and str(tmp_path / "run") not in expr
    assert subprocess.run(cmd, capture_output=True, text=True).returncode == 0 and _source_folder(moved) == expr  # idempotent
    json.loads(next(moved.glob("*.SemanticModel/model.bim")).read_text(encoding="utf-8"))  # toujours un JSON valide


def test_relier_power_bi_signale_les_csv_manquants_et_les_dossiers_invalides(tmp_path):
    from src.agents.dashboard.relier_power_bi import main, relink
    res = run_dashboard_step(make_state(tmp_path, SALES_ROWS, SALES_MAP, SALES_CAT, SALES_RES))
    project = res["dashboard"]["output_dir"]
    empty = tmp_path / "vide"
    empty.mkdir()
    assert main([project, "--source", str(empty)]) == 2  # CSV absents du dossier choisi
    assert sorted(relink(project, empty)["missing"]) == ["BusinessInsights.csv", "DataQuality.csv", "FactTrade.csv"]
    with pytest.raises(FileNotFoundError):
        relink(tmp_path / "pas_un_projet")

