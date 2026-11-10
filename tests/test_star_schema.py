"""Schéma en étoile du modèle Power BI (Agent 5), reconstruit à partir des tables nettoyées.

Chaîne complète : tables nettoyées -> table à plat de l'Agent 4 (sa propre jointure) -> prep.build_input
-> run_dashboard. Vérifie qu'aucune colonne des tables nettoyées ne disparaît, que chaque dimension a
une clé unique et non vide reliée à la table de faits, que les formules et les visuels visent la table
qui porte la colonne, et que le rapport reste valide (schémas PBIR officiels, mise en page).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.agents.dashboard.dashboard_generator import run_dashboard
from src.agents.dashboard.prep import build_input
from src.agents.dashboard.star_schema import build_star, physical_dax, read_tables
from src.agents.semantic_kpi.data import construire_fact_trade

FACT = "Fact_Trade"
SCHEMAS = Path(__file__).resolve().parents[1] / "resources" / "pbir_schemas"
PREFIX = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/"


def _clean_tables() -> dict[str, pd.DataFrame]:
    """7 tables nettoyées au format Northwind (mêmes colonnes que la sortie de l'Agent 2)."""
    categories = pd.DataFrame({"categoryID": [1, 2, 3], "categoryName": ["Beverages", "Produce", "Seafood"],
                               "description": ["Drinks", "Fruits", "Fish"]})
    products = pd.DataFrame({"productID": range(1, 7), "productName": ["Chai", "Tofu", "Ikura", "Chang", "Konbu", "Apple"],
                             "quantityPerUnit": ["10 boxes", "40 pkgs", "12 jars", "24 bottles", "2 kg", "1 box"],
                             "unitPrice": [18.0, 23.25, 31.0, 19.0, 6.0, 5.5], "discontinued": [False, False, True, False, False, False],
                             "categoryID": [1, 2, 3, 1, 3, 2]})
    customers = pd.DataFrame({"customerID": ["ALFKI", "BONAP", "QUICK", "RATTC"],
                              "companyName": ["Alfreds", "Bon app'", "QUICK-Stop", "Rattlesnake"],
                              "contactName": ["Maria", "Laurence", "Horst", "Paula"], "contactTitle": ["Owner", "Owner", "Manager", "Manager"],
                              "city": ["Berlin", "Marseille", "Cunewalde", "Albuquerque"], "country": ["Germany", "France", "Germany", "USA"]})
    employees = pd.DataFrame({"employeeID": [1, 2, 3], "employeeName": ["Nancy", "Andrew", "Janet"],
                              "title": ["Rep", "VP", "Rep"], "city": ["Seattle", "Tacoma", "London"],
                              "country": ["USA", "USA", "UK"], "reportsTo": [2, None, 2]})
    shippers = pd.DataFrame({"shipperID": [1, 2, 3], "companyName": ["Speedy Express", "United Package", "Federal Shipping"]})
    orders, details = [], []
    for i in range(30):
        day = pd.Timestamp("2013-07-01") + pd.Timedelta(days=23 * i)
        orders.append({"orderID": 10000 + i, "customerID": customers["customerID"][i % 4], "employeeID": 1 + i % 3,
                       "orderDate": day.strftime("%Y-%m-%d"), "requiredDate": (day + pd.Timedelta(days=14)).strftime("%Y-%m-%d"),
                       "shippedDate": "" if i % 7 == 0 else (day + pd.Timedelta(days=2 + i % 15)).strftime("%Y-%m-%d"),
                       "shipperID": 1 + i % 3, "freight": 10.0 + i})
        for j in range(1 + i % 3):
            pid = 1 + (i + j) % 6
            details.append({"orderID": 10000 + i, "productID": pid, "unitPrice": float(products["unitPrice"][pid - 1]),
                            "quantity": 3 + (i + 2 * j) % 11, "discount": 0.05 if j == 0 else 0.0})
    orders = pd.DataFrame(orders)
    orders["shippedDate"] = orders["shippedDate"].replace("", None)
    return {"categories": categories, "products": products, "customers": customers, "employees": employees,
            "shippers": shippers, "orders": orders, "order_details": pd.DataFrame(details)}


def _catalog() -> list[dict]:
    """Catalogue de l'Agent 4 : formules écrites sur la table à plat (noms de colonnes d'origine)."""
    f = FACT
    kpis = [
        ("total_revenue", "Total Revenue", f"SUM({f}[lineTotal])", ["lineTotal"]),
        ("order_count", "Order Count", f"DISTINCTCOUNT({f}[orderID])", ["orderID"]),
        ("avg_order_value", "Average Order Value", "DIVIDE([Total Revenue], [Order Count])", []),
        ("top_countries", "Top Countries", f"DIVIDE(SUM({f}[lineTotal]), CALCULATE(SUM({f}[lineTotal]), ALL({f}[country])))", ["country"]),
        ("top_products", "Top Products", f"DIVIDE(SUM({f}[lineTotal]), CALCULATE(SUM({f}[lineTotal]), ALL({f}[productName])))", ["productName"]),
        ("avg_lead_time", "Average Shipping Lead Time",
         f"AVERAGEX(VALUES({f}[orderID]), DATEDIFF(CALCULATE(MIN({f}[orderDate])), CALCULATE(MAX({f}[shippedDate])), DAY))", []),
    ]
    return [{"id": i, "nom": n, "definition": n, "formule": n, "dax": d, "colonnes_sources": c, "origine": "test"} for i, n, d, c in kpis]


@pytest.fixture
def run(tmp_path: Path) -> dict:
    clean = tmp_path / "clean"
    clean.mkdir()
    tables = _clean_tables()
    for name, frame in tables.items():
        frame.to_csv(clean / f"{name}.csv", index=False)
    kpi_dir = tmp_path / "semantic_kpi"
    kpi_dir.mkdir()
    flat = construire_fact_trade(read_tables(clean))         # la jointure de l'Agent 4, telle quelle
    flat.to_csv(kpi_dir / "fact_trade.csv", index=False, date_format="%Y-%m-%d")
    (kpi_dir / "kpi_catalog.json").write_text(json.dumps(_catalog()), encoding="utf-8")
    state = {"run": {"run_dir": str(tmp_path)}, "kpi": {
        "status": "done", "data_path": str(clean), "fact_table_path": str(kpi_dir / "fact_trade.csv"),
        "outputs": {"kpi_catalog": str(kpi_dir / "kpi_catalog.json")}, "kpi_results": {},
        "semantic_mapping": {"order_id": "orderID", "date": "orderDate", "shipped_date": "shippedDate", "required_date": "requiredDate",
                             "country": "country", "product": "productName", "value": "lineTotal"},
        "kpi_analysis": [{"kpi_id": "total_revenue", "constat": "Constat de test."}], "kpi_checks": []}}
    prep = build_input(state, tmp_path / "dashboard_input")
    result = run_dashboard(Path(prep["input_dir"]), tmp_path / "dashboard")
    out = tmp_path / "dashboard"
    bim = json.loads((out / "dashboard_generator.SemanticModel/model.bim").read_text(encoding="utf-8"))["model"]
    return {"tables": tables, "prep": prep, "result": result, "out": out, "bim": bim,
            "model": {t["name"]: t for t in bim["tables"]}}


def test_aucune_colonne_des_tables_nettoyees_ne_disparait(run):
    model = run["model"]
    columns = {c["name"].lower() for t in model.values() for c in t["columns"]}
    for name, frame in run["tables"].items():
        for column in frame.columns:
            assert column.lower() in columns, (name, column)
    assert {c["name"] for c in model["DimCustomer"]["columns"]} == {"CustomerID", "CompanyName", "ContactName", "ContactTitle", "City", "Country"}
    # Flocon aplati : la catégorie (nom et description) est dans la dimension produit.
    assert {"CategoryID", "CategoryName", "Description", "Discontinued"} <= {c["name"] for c in model["DimProduct"]["columns"]}
    assert "LineTotal" in {c["name"] for c in model[FACT]["columns"]}  # colonne calculée par l'Agent 4


def test_le_modele_est_un_schema_en_etoile(run):
    model, rels = run["model"], run["bim"]["relationships"]
    assert run["result"]["status"] == "PARTIAL_SUCCESS" and run["result"]["errors"] == []
    report = json.loads((run["out"] / "dashboard_generation_report.json").read_text(encoding="utf-8"))
    assert report["semantic_model"]["schema"] == "star" and report["errors"] == []
    assert set(model) == {FACT, "DimDate", "DimProduct", "DimCustomer", "DimEmployee", "DimShipper", "BusinessInsights", "DataQuality"}
    assert all(r["fromTable"] == FACT for r in rels)            # toutes les relations partent de la table de faits
    assert {r["toTable"] for r in rels} == {"DimDate", "DimProduct", "DimCustomer", "DimEmployee", "DimShipper"}
    source = run["out"] / "dashboard_generator.source"
    fact = pd.read_csv(source / "FactTrade.csv")
    for r in rels:
        if r["toTable"] == "DimDate":
            continue
        dim = pd.read_csv(source / f"{r['toTable']}.csv")
        key = dim[r["toColumn"]]
        assert key.notna().all() and not key.astype(str).str.lower().duplicated().any(), r   # clé unique et non vide
        assert fact[r["fromColumn"]].dropna().isin(key).all(), r                              # intégrité référentielle
        fk = next(c for c in model[FACT]["columns"] if c["name"] == r["fromColumn"])
        assert fk.get("isHidden") and fk.get("summarizeBy") == "none"
    flat = pd.read_csv(run["prep"]["input_dir"] + f"/{FACT}.csv")
    assert len(fact) == len(flat) and fact["LineTotal"].sum() == pytest.approx(flat["lineTotal"].sum())


def test_les_formules_et_les_visuels_visent_la_table_qui_porte_la_colonne(run):
    model = run["model"]
    measures = {m["name"]: m["expression"] for m in model[FACT]["measures"]}
    assert "ALL(DimCustomer[Country])" in measures["Top Countries"]
    assert "ALL(DimProduct[ProductName])" in measures["Top Products"]
    assert measures["Total Revenue"] == f"SUM({FACT}[LineTotal])"
    columns = {t: {c["name"] for c in v["columns"]} for t, v in model.items()}
    names = {t: {m["name"] for m in v.get("measures", [])} for t, v in model.items()}
    entities = set()
    for path in run["out"].glob("dashboard_generator.Report/definition/pages/*/visuals/*/visual.json"):
        query = json.loads(path.read_text(encoding="utf-8"))["visual"].get("query", {})
        for state in query.get("queryState", {}).values():
            for p in state["projections"]:
                f = p["field"].get("Aggregation", {}).get("Expression", p["field"])
                kind = "Measure" if "Measure" in f else "Column"
                table, name = f[kind]["Expression"]["SourceRef"]["Entity"], f[kind]["Property"]
                assert name in (names if kind == "Measure" else columns)[table], (path.parent.name, table, name)
                entities.add(table)
                if (table, name) == ("DimShipper", "CompanyName"):
                    assert p.get("displayName") == "ShipperName"   # même colonne, nom lisible
    assert {"DimCustomer", "DimProduct", "DimDate"} <= entities


def test_rapport_valide_et_mis_en_page(run):
    jsonschema = pytest.importorskip("jsonschema")
    referencing = pytest.importorskip("referencing")
    from referencing.jsonschema import DRAFT7

    out = run["out"] / "dashboard_generator.Report"
    registry = referencing.Registry().with_resources(
        (PREFIX + p.relative_to(SCHEMAS).as_posix(), referencing.Resource.from_contents(json.loads(p.read_text(encoding="utf-8")), default_specification=DRAFT7))
        for p in SCHEMAS.rglob("*.json"))
    errors = []
    for path in out.glob("definition/**/*.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("$schema", "").startswith(PREFIX):
            schema = registry.get_or_retrieve(doc["$schema"]).value.contents
            errors += [f"{path.name}: {e.message}" for e in jsonschema.Draft7Validator(schema, registry=registry).iter_errors(doc)]
    assert errors == []
    report = json.loads((out / "definition/report.json").read_text(encoding="utf-8"))
    custom = report["themeCollection"]["customTheme"]["name"]
    assert (out / "StaticResources/RegisteredResources" / custom).is_file()
    for page in (out / "definition/pages").iterdir():
        if not page.is_dir():
            continue
        visuals = {p.parent.name: json.loads(p.read_text(encoding="utf-8")) for p in page.glob("visuals/*/visual.json")}
        header = visuals[f"{page.name}_header"]
        assert header["visual"]["visualType"] == "textbox" and header["position"]["y"] < 20
        slicers = [v for v in visuals.values() if v["visual"]["visualType"] == "slicer"]
        if page.name != "DataQualityTraceability":
            assert len(slicers) == 1 and slicers[0]["visual"]["syncGroup"]["groupName"] == "Annee"


def test_repli_a_plat_si_les_lignes_ne_sont_pas_alignees():
    tables = _clean_tables()
    flat = construire_fact_trade(tables).iloc[:-1]            # une ligne de moins : on ne devine pas
    star, why = build_star(tables, flat)
    assert star is None and "lignes" in why


def test_physical_dax_ne_touche_que_les_colonnes_deplacees():
    locations = {"Country": ("DimCustomer", "Country"), "LineTotal": (FACT, "LineTotal")}
    dax = f"DIVIDE(SUM({FACT}[LineTotal]), CALCULATE(SUM({FACT}[LineTotal]), ALL({FACT}[Country]))) + [Order Count]"
    assert physical_dax(dax, FACT, locations) == (
        f"DIVIDE(SUM({FACT}[LineTotal]), CALCULATE(SUM({FACT}[LineTotal]), ALL(DimCustomer[Country]))) + [Order Count]")
