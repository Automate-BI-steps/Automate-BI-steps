"""Rapport Power BI par rôle (role_report.py) : vrai Agent 4 (sous-processus) sur des tables de type Northwind,
puis prep.py -> run_dashboard. Vérifie les pages par lecteur, l'ossature commune (filtres synchronisés, bandeau,
navigation), les champs (tous dans model.bim), le respect des schémas PBIR et la page 1280×720 sans chevauchement.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agents.dashboard.dashboard_generator import run_dashboard
from src.agents.dashboard.prep import build_input
from src.agents.semantic_kpi.node import run_semantic_kpi
from tests.test_star_schema import PREFIX, SCHEMAS, _clean_tables

ROLE_PAGES = ["ExecutiveOverview", "AdministrationOverview", "FinanceOverview", "LogisticsOverview", "DataQualityTraceability"]


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict:
    tmp = tmp_path_factory.mktemp("role")
    clean = tmp / "clean"
    clean.mkdir()
    for name, frame in _clean_tables().items():
        frame.to_csv(clean / f"{name}.csv", index=False)
    kpi = run_semantic_kpi({"cleaning": {"clean_data_dir": str(clean)}, "input": {"work_dir": str(tmp)}})["kpi"]
    assert kpi["status"] == "done", kpi
    state = {"run": {"run_dir": str(tmp)}, "kpi": kpi}
    prep = build_input(state, tmp / "dashboard_input")
    result = run_dashboard(Path(prep["input_dir"]), tmp / "dashboard")
    out = tmp / "dashboard"
    visuals = {}
    for path in out.glob("dashboard_generator.Report/definition/pages/*/visuals/*/visual.json"):
        visuals.setdefault(path.parts[-4], {})[path.parent.name] = json.loads(path.read_text(encoding="utf-8"))
    return {"kpi": kpi, "result": result, "out": out, "visuals": visuals,
            "plan": json.loads((out / "dashboard_plan.json").read_text(encoding="utf-8")),
            "bim": json.loads((out / "dashboard_generator.SemanticModel/model.bim").read_text(encoding="utf-8"))["model"]}


def test_une_page_par_lecteur_et_le_critique_accepte(run):
    result, plan = run["result"], run["plan"]
    assert result["status"] == "PARTIAL_SUCCESS" and result["errors"] == []
    assert result["artifacts"]["critic_status"].startswith("ACCEPTED")
    assert plan["metadata"]["compiler"] == "role_report"
    assert [p["name"] for p in plan["pages"]] == ROLE_PAGES
    pages = json.loads((run["out"] / "dashboard_generator.Report/definition/pages/pages.json").read_text(encoding="utf-8"))
    assert pages["pageOrder"] == ROLE_PAGES
    for page in plan["pages"][:4]:
        cards = [v for v in page["visuals"] if v["visual_type"] == "card"]
        assert 2 <= len(cards) <= 6 and len(page["visuals"]) - len(cards) >= 2, page["name"]
    types = {v["visual_type"] for p in plan["pages"] for v in p["visuals"]}
    assert {"card", "clusteredBarChart", "areaChart", "treemap", "donutChart", "gauge", "lineChart", "tableEx"} <= types


def test_les_nouveaux_kpi_sont_dans_les_cartes(run):
    cards = {v["kpi_reference"] for p in run["plan"]["pages"] for v in p["visuals"] if v["visual_type"] == "card"}
    assert {"total_revenue", "customer_count", "order_count", "best_employee", "gross_revenue", "discount_rate",
            "shipped_orders", "unshipped_orders", "late_orders"} <= cards
    measures = {m["name"]: m for m in next(t for t in run["bim"]["tables"] if t["name"] == "Fact_Trade")["measures"]}
    assert "formatString" not in measures["Best Employee"]                                       # mesure texte
    assert measures["Active Customers"]["expression"].startswith("CALCULATE(DISTINCTCOUNT(DimCustomer[CompanyName]), Fact_Trade)")


def test_ossature_commune_filtres_bandeau_navigation(run):
    for name in ROLE_PAGES:
        visuals = run["visuals"][name]
        header = visuals[f"{name}_header"]["visual"]
        runs = [r["value"] for p in header["objects"]["general"][0]["properties"]["paragraphs"] for r in p["textRuns"]]
        assert runs[0] == "Dashboard "
        buttons = [v["visual"] for v in visuals.values() if v["visual"]["visualType"] == "actionButton"]
        targets = [b["visualContainerObjects"]["visualLink"][0]["properties"]["navigationSection"]["expr"]["Literal"]["Value"].strip("'")
                   for b in buttons]
        assert sorted(targets) == sorted(ROLE_PAGES)                                             # un bouton par page
        slicers = [v["visual"] for v in visuals.values() if v["visual"]["visualType"] == "slicer"]
        if name == "DataQualityTraceability":
            assert not slicers
        else:
            groups = {s["syncGroup"]["groupName"] for s in slicers}
            assert {"filtre_year", "filtre_period", "filtre_country", "filtre_category", "filtre_customer", "filtre_shipper"} == groups


def test_chaque_champ_existe_et_les_fichiers_respectent_les_schemas(run):
    jsonschema = pytest.importorskip("jsonschema")
    referencing = pytest.importorskip("referencing")
    from referencing.jsonschema import DRAFT7

    columns = {t["name"]: {c["name"] for c in t["columns"]} for t in run["bim"]["tables"]}
    measures = {t["name"]: {m["name"] for m in t.get("measures", [])} for t in run["bim"]["tables"]}
    for page in run["visuals"].values():
        for v in page.values():
            query = v["visual"].get("query", {})
            fields = [p["field"] for s in query.get("queryState", {}).values() for p in s["projections"]]
            fields += [s["field"] for s in query.get("sortDefinition", {}).get("sort", [])]
            for f in fields:
                f = f.get("Aggregation", {}).get("Expression", f)
                kind = "Measure" if "Measure" in f else "Column"
                table, name = f[kind]["Expression"]["SourceRef"]["Entity"], f[kind]["Property"]
                assert name in (measures if kind == "Measure" else columns)[table], (v["name"], table, name)
    registry = referencing.Registry().with_resources(
        (PREFIX + p.relative_to(SCHEMAS).as_posix(), referencing.Resource.from_contents(json.loads(p.read_text(encoding="utf-8")), default_specification=DRAFT7))
        for p in SCHEMAS.rglob("*.json"))
    errors = []
    for path in (run["out"] / "dashboard_generator.Report").glob("definition/**/*.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("$schema", "").startswith(PREFIX):
            schema = registry.get_or_retrieve(doc["$schema"]).value.contents
            errors += [f"{path.name}: {e.message}" for e in jsonschema.Draft7Validator(schema, registry=registry).iter_errors(doc)]
    assert errors == []
    report = json.loads((run["out"] / "dashboard_generator.Report/definition/report.json").read_text(encoding="utf-8"))
    assert report["themeCollection"]["customTheme"]["name"] == "BIFlowRoleTheme.json"


def test_les_visuels_tiennent_dans_la_page_sans_se_chevaucher(run):
    for name, page in run["visuals"].items():
        boxes = [(v["name"], v["position"]) for v in page.values() if not v["name"].endswith("_side")]   # le fond du panneau est dessous
        for vid, p in boxes:
            assert p["x"] >= 0 and p["y"] >= 0 and p["x"] + p["width"] <= 1280 and p["y"] + p["height"] <= 720, (name, vid)
        for i, (a, p) in enumerate(boxes):
            for b, q in boxes[i + 1:]:
                apart = (p["x"] + p["width"] <= q["x"] or q["x"] + q["width"] <= p["x"] or
                         p["y"] + p["height"] <= q["y"] or q["y"] + q["height"] <= p["y"])
                assert apart, (name, a, b)
