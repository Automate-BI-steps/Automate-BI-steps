"""Rapport Power BI générique (jeu de données hors contrat import/export, ex. Northwind).

Vérifie que le PBIP, dashboard_plan.json et le critique décrivent le même rapport, que chaque
champ utilisé existe dans model.bim, que les fichiers PBIR respectent les schémas officiels de
Microsoft (copie hors ligne dans resources/pbir_schemas) et que le modèle se chargera dans Power BI
(aucune valeur vide côté « un » d'une relation).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.agents.dashboard import generic_report as gr
from src.agents.dashboard.dashboard_critic import DashboardCritic
from src.agents.dashboard.dashboard_generator import load_bundle, run_dashboard

SCHEMAS = Path(__file__).resolve().parents[1] / "resources" / "pbir_schemas"
PREFIX = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/"
FACT = "Fact_Trade"

# Formules DAX telles que l'Agent 4 les publie (réécrites sur les noms du modèle par prep.py).
KPIS = [
    ("total_revenue", "Total Revenue", f"SUM({FACT}[LineTotal])"),
    ("order_count", "Order Count", f"DISTINCTCOUNT({FACT}[OrderID])"),
    ("avg_order_value", "Average Order Value", "DIVIDE([Total Revenue], [Order Count])"),
    ("top_countries", "Top Countries", f"DIVIDE(SUM({FACT}[LineTotal]), CALCULATE(SUM({FACT}[LineTotal]), ALL({FACT}[Country])))"),
    ("top_products", "Top Products", f"DIVIDE(SUM({FACT}[LineTotal]), CALCULATE(SUM({FACT}[LineTotal]), ALL({FACT}[ProductName])))"),
    ("avg_lead_time", "Average Shipping Lead Time",
     f"AVERAGEX(VALUES({FACT}[OrderID]), DATEDIFF(CALCULATE(MIN({FACT}[OrderDate])), CALCULATE(MAX({FACT}[ShippedDate])), DAY))"),
    ("on_time_rate", "On-Time Shipping Rate",
     f"VAR expediees = FILTER(VALUES({FACT}[OrderID]), NOT ISBLANK(CALCULATE(MAX({FACT}[ShippedDate])))) "
     f"RETURN DIVIDE(COUNTROWS(FILTER(expediees, CALCULATE(MAX({FACT}[ShippedDate])) <= CALCULATE(MAX({FACT}[RequiredDate])))), "
     f"COUNTROWS(expediees)) * 100"),
]


def _orders() -> pd.DataFrame:
    rows = []
    countries = ["France", "Germany", "USA", "Brazil"]
    products = [("Chai", "Beverages"), ("Tofu", "Produce"), ("Ikura", "Seafood"), ("Pavlova", "Confections"), ("Konbu", "Seafood")]
    for i in range(40):
        order = 10000 + i
        day = pd.Timestamp("2013-07-01") + pd.Timedelta(days=17 * i)
        shipped = "" if i % 9 == 0 else (day + pd.Timedelta(days=3 + i % 12)).strftime("%Y-%m-%d")  # commandes non expédiées
        for j in range(1 + i % 3):
            product, category = products[(i + j) % len(products)]
            qty = 5 + (i * 7 + j) % 20
            price = 10.0 + (i + j) % 6
            rows.append({"orderID": order, "orderDate": day.strftime("%Y-%m-%d"),
                         "requiredDate": (day + pd.Timedelta(days=14)).strftime("%Y-%m-%d"), "shippedDate": shipped,
                         "companyName": f"Client {i % 25}", "country": countries[i % 4], "employeeName": f"Employé {i % 3}",
                         "shipperName": ["Speedy", "United", "Federal"][i % 3], "productName": product, "categoryName": category,
                         "unitPrice": price, "quantity": qty, "discount": 0.0 if j else 0.05, "lineTotal": round(price * qty, 2),
                         "freight": 12.5})
    return pd.DataFrame(rows)


@pytest.fixture
def northwind_dir(tmp_path: Path) -> Path:
    root = tmp_path / "dashboard_input"
    root.mkdir()
    frame = _orders()
    frame.to_csv(root / f"{FACT}.csv", index=False)
    dims = [c for c in frame.columns if not pd.api.types.is_numeric_dtype(frame[c]) or "date" in c.lower()]
    (root / "data_model.json").write_text(json.dumps({
        "fact_table": FACT, "columns": {c: c for c in dims},
        "roles": {"date": "orderDate", "country": "country", "product": "productName", "value": "lineTotal", "order_id": "orderID"},
    }), encoding="utf-8")
    (root / "kpi_catalog.json").write_text(json.dumps({"kpis": [
        {"id": i, "name": n, "definition": n, "expression": n, "dax": d, "source_columns": []} for i, n, d in KPIS
    ] + [{"id": "yoy_growth", "name": "YoY Growth", "definition": "", "expression": "", "supported": False}]}), encoding="utf-8")
    (root / "bi_analyst_findings.json").write_text(json.dumps({"findings": [{"id": "f-1", "text": "Constat de test."}]}), encoding="utf-8")
    (root / "data_quality_report.json").write_text(json.dumps({"status": "ok", "checks": {"score": 97}}), encoding="utf-8")
    return root


@pytest.fixture
def generated(northwind_dir: Path, tmp_path: Path) -> tuple[dict, Path]:
    out = tmp_path / "dashboard"
    return run_dashboard(northwind_dir, out), out


def _visuals(out: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(out.glob("dashboard_generator.Report/definition/pages/*/visuals/*/visual.json"))]


def test_le_plan_generique_est_diversifie_et_accepte_par_le_critique(northwind_dir: Path):
    bundle, _ = load_bundle(northwind_dir, demo=False)
    plan = gr.build_generic_plan(bundle)
    assert len(plan.pages) <= 6 and all(len(p.visuals) <= 6 for p in plan.pages)
    assert [p.name for p in plan.pages][0] == "ExecutiveOverview" and plan.pages[-1].name == "DataQualityTraceability"
    types = {v.visual_type for p in plan.pages for v in p.visuals}
    assert {"card", "lineChart", "clusteredBarChart", "clusteredColumnChart", "donutChart", "treemap", "tableEx", "slicer",
            "lineClusteredColumnComboChart"} <= types
    # Les mesures de part (ALL(...)) ne sont pas des cartes : une carte afficherait toujours 100 %.
    cards = [v.title for v in plan.pages[0].visuals if v.visual_type == "card"]
    assert "Top Countries" not in cards and "Top Products" not in cards and "Total Revenue" in cards
    report = DashboardCritic().evaluate(plan, bundle)
    assert report.passed_deterministic, report.errors


def test_le_projet_pbip_est_coherent_avec_le_plan(generated):
    result, out = generated
    assert result["status"] == "PARTIAL_SUCCESS" and result["errors"] == []
    plan = json.loads((out / "dashboard_plan.json").read_text(encoding="utf-8"))
    assert result["artifacts"]["critic_status"].startswith("ACCEPTED")
    pages = json.loads((out / "dashboard_generator.Report/definition/pages/pages.json").read_text(encoding="utf-8"))
    assert pages["pageOrder"] == [p["name"] for p in plan["pages"]]
    built = {p.parent.name for p in out.glob("dashboard_generator.Report/definition/pages/*/visuals/*/visual.json")}
    planned = {v["id"] for p in plan["pages"] for v in p["visuals"]}
    # En plus du plan : le bandeau de chaque page (titre, description) et le segment Année synchronisé.
    assert planned <= built and all(v.endswith(("_header", "_year")) for v in built - planned)
    assert not (out / "dashboard.html").exists() and not (out / "critic_report.json").exists()   # sorties inutiles supprimées


def test_chaque_champ_des_visuels_existe_dans_model_bim(generated):
    _, out = generated
    bim = json.loads((out / "dashboard_generator.SemanticModel/model.bim").read_text(encoding="utf-8"))["model"]
    columns = {t["name"]: {c["name"] for c in t["columns"]} for t in bim["tables"]}
    measures = {t["name"]: {m["name"] for m in t.get("measures", [])} for t in bim["tables"]}
    for v in _visuals(out):
        query = v["visual"].get("query", {})
        fields = [p["field"] for s in query.get("queryState", {}).values() for p in s["projections"]]
        fields += [s["field"] for s in query.get("sortDefinition", {}).get("sort", [])]
        for f in fields:
            f = f.get("Aggregation", {}).get("Expression", f)
            kind = "Measure" if "Measure" in f else "Column"
            table, name = f[kind]["Expression"]["SourceRef"]["Entity"], f[kind]["Property"]
            assert name in (measures if kind == "Measure" else columns).get(table, set()), (v["name"], table, name)


def test_le_modele_se_chargera_dans_power_bi(generated):
    _, out = generated
    bim = json.loads((out / "dashboard_generator.SemanticModel/model.bim").read_text(encoding="utf-8"))["model"]
    tables = {t["name"]: t for t in bim["tables"]}
    # Une seule table de dates continue, reliée à la date de commande ; pas de dimension sur les dates
    # facultatives (ShippedDate vide = commande non expédiée = vide côté « un » = actualisation en échec).
    assert "DimDate" in tables and "DimShippedDate" not in tables and "DimOrderDate" not in tables
    assert {"fromColumn": "OrderDate", "toTable": "DimDate", "toColumn": "Date"}.items() <= next(
        r for r in bim["relationships"] if r["toTable"] == "DimDate").items()
    for name, table in tables.items():
        if name.startswith("Dim") and name != "DimDate":
            expression = "\n".join(table["partitions"][0]["source"]["expression"])
            assert "<> null" in expression and 'Table.Distinct(NonBlank' in expression, name
    country = tables["DimCountry"]  # « country » contient « count » : reste bien une dimension texte
    assert country["columns"][0]["dataType"] == "string"
    formats = {m["name"]: m["formatString"] for m in tables[FACT]["measures"]}
    assert formats["Top Countries"] == "0.0%" and formats["Order Count"] == "#,0"
    assert formats["Average Shipping Lead Time"].endswith('" j"') and formats["On-Time Shipping Rate"].endswith('" %"')


def test_les_fichiers_pbir_respectent_les_schemas_officiels(generated):
    jsonschema = pytest.importorskip("jsonschema")
    referencing = pytest.importorskip("referencing")
    from referencing.jsonschema import DRAFT7

    _, out = generated
    registry = referencing.Registry().with_resources(
        (PREFIX + p.relative_to(SCHEMAS).as_posix(), referencing.Resource.from_contents(json.loads(p.read_text(encoding="utf-8")), default_specification=DRAFT7))
        for p in SCHEMAS.rglob("*.json"))
    checked, errors = 0, []
    for path in out.glob("dashboard_generator.Report/definition/**/*.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        url = doc.get("$schema", "")
        if not url.startswith(PREFIX):
            continue
        schema = registry.get_or_retrieve(url).value.contents
        errors += [f"{path.name}: {e.message}" for e in jsonschema.Draft7Validator(schema, registry=registry).iter_errors(doc)]
        checked += 1
    assert checked > 20 and errors == []


def test_les_visuels_tiennent_dans_la_page_sans_se_chevaucher(generated):
    _, out = generated
    by_page: dict[str, list[dict]] = {}
    for path in out.glob("dashboard_generator.Report/definition/pages/*/visuals/*/visual.json"):
        by_page.setdefault(path.parts[-4], []).append(json.loads(path.read_text(encoding="utf-8"))["position"])
    for page, boxes in by_page.items():
        for b in boxes:
            assert b["x"] >= 0 and b["y"] >= 0 and b["x"] + b["width"] <= 1280 and b["y"] + b["height"] <= 720, page
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                assert (a["x"] + a["width"] <= b["x"] or b["x"] + b["width"] <= a["x"]
                        or a["y"] + a["height"] <= b["y"] or b["y"] + b["height"] <= a["y"]), page


def test_les_mesures_du_html_suivent_les_formules_dax(northwind_dir: Path):
    bundle, _ = load_bundle(northwind_dir, demo=False)
    frame = gr.model_frame(bundle)
    measures = {n: d for _, n, d in KPIS}
    raw = _orders()
    assert gr.evaluate(measures["Total Revenue"], frame, frame, measures) == pytest.approx(raw["lineTotal"].sum())
    assert gr.evaluate(measures["Order Count"], frame, frame, measures) == raw["orderID"].nunique()
    per = raw.groupby("orderID").agg(o=("orderDate", "min"), s=("shippedDate", "max"), r=("requiredDate", "max"))
    shipped = per[per["s"] != ""]
    lead = (pd.to_datetime(shipped["s"]) - pd.to_datetime(shipped["o"])).dt.days.mean()
    assert gr.evaluate(measures["Average Shipping Lead Time"], frame, frame, measures) == pytest.approx(lead)
    on_time = (pd.to_datetime(shipped["s"]) <= pd.to_datetime(shipped["r"])).mean() * 100
    assert gr.evaluate(measures["On-Time Shipping Rate"], frame, frame, measures) == pytest.approx(on_time)
    # Part par pays : ALL(Fact[Country]) retire le filtre du pays, donc la somme des parts fait 100 %.
    shares = [gr.evaluate(measures["Top Countries"], part, frame, measures, "Country") for _, part in frame.groupby("Country")]
    assert sum(shares) == pytest.approx(1.0)


def test_un_plan_ecrit_pour_facttrade_vise_la_vraie_table_de_faits(northwind_dir: Path):
    from src.agents.dashboard.dashboard_compiler_adapter import compile_plan_to_report_pages
    from src.agents.dashboard.dashboard_planner import DashboardPlan, PagePlan, VisualPlan

    bundle, _ = load_bundle(northwind_dir, demo=False)
    plan = DashboardPlan(pages=[PagePlan(name="P", display_name="P", visuals=[VisualPlan(
        id="v", title="v", visual_type="card", roles={"Values": [{"kind": "Measure", "table": "FactTrade", "name": "Total Revenue"}]})])])
    field = compile_plan_to_report_pages(plan, bundle)[0]["visualContainers"][0]["config"]["query"]["queryState"]["Values"]["projections"][0]["field"]
    assert field["Measure"]["Expression"]["SourceRef"]["Entity"] == FACT


@pytest.mark.parametrize("types", [["card"] * 5 + ["lineChart"], ["slicer", "clusteredColumnChart", "lineChart", "lineChart", "areaChart", "lineChart"],
                                   ["tableEx", "tableEx"], ["card"] * 8 + ["tableEx"],
                                   ["textbox", "slicer", "treemap", "clusteredColumnChart", "clusteredBarChart", "tableEx"],
                                   ["textbox", "slicer", "tableEx", "clusteredBarChart", "donutChart", "clusteredColumnChart", "clusteredBarChart"]])
def test_la_grille_reste_dans_la_page(types):
    boxes = gr.layout(types)
    for x, y, w, h in boxes:
        assert x >= 0 and y >= 0 and x + w <= gr.PAGE_W and y + h <= gr.PAGE_H
    for i, (ax, ay, aw, ah) in enumerate(boxes):
        for bx, by, bw, bh in boxes[i + 1:]:
            assert ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay
