"""Rapport Power BI « par rôle » (modèle de référence de bedis), construit sur le schéma en étoile.

Quand prep.py a reconstruit l'étoile (star_schema.py), le rapport n'est plus organisé par axe technique
mais par lecteur, comme la maquette de référence :

  - Direction commerciale (page d'accueil) : CA, clients, commandes, panier moyen ; CA par ville,
    évolution mensuelle, catégories et produits ;
  - Administration : commandes, CA, meilleur employé, unités ; CA par mois et par employé,
    commandes expédiées / non expédiées, commandes par pays ;
  - Direction financière : CA brut, remises, taux de remise, poids du fret ; brut contre net par mois,
    remises par catégorie, clients, tableau des produits ;
  - Direction logistique : expédiées, taux à l'heure, non expédiées, en retard, délai ; délai et fret
    par transporteur, retards par employé, jauge expédiées / commandes ;
  - Constats et qualité : constats de l'Agent 4 et contrôles de qualité.

Chaque page a la même ossature (``compose``) : un panneau de filtres à gauche (segments Année, Période,
Pays, Catégorie, Client, Transporteur, synchronisés sur toutes les pages), un bandeau « Dashboard <rôle> »
avec des boutons de navigation entre les pages, une rangée de cartes KPI puis les graphiques.

Rien n'est inventé : un visuel n'est créé que si ses mesures existent dans le catalogue de l'Agent 4 et
ses colonnes dans le modèle. Northwind n'a pas de coût d'achat : le bénéfice net et le coût des
marchandises vendues (cartes de la maquette) ne sont PAS calculables et ne sont donc pas affichés.
"""
from __future__ import annotations

from typing import Any

COMPILER = "role_report"
THEME_NAME = "BIFlowRoleTheme.json"

# Couleurs de la maquette : fond bleu nuit, cartes gris-bleu, chiffres rouge foncé, accents or.
NAVY, NAVY_DARK, PANEL, INK, RED, GOLD, WHITE, MUTED = (
    "#13213D", "#0B1529", "#E6EBF3", "#13213D", "#9E1B32", "#D4A017", "#FFFFFF", "#5B6B86")
PALETTE = ("#1F4E8C", "#9E1B32", "#D4A017", "#3E8EC4", "#7A8FB3", "#E07A5F", "#4F9D69", "#F2C14E", "#6C5B9E", "#9CC3D5")

PAGE_W, PAGE_H = 1280.0, 720.0
SIDE_W, PAD, GAP = 200.0, 16.0, 12.0
HEADER_Y, HEADER_H = 10.0, 56.0
CARDS_Y, CARD_H = 76.0, 96.0
CONTENT_X = SIDE_W + PAD
CONTENT_W = PAGE_W - CONTENT_X - PAD

# (page, titre du bandeau, libellé du bouton, description)
PAGES = (
    ("ExecutiveOverview", "Directeur commercial", "Commercial", "Chiffre d'affaires, clients et produits."),
    ("AdministrationOverview", "Administrateur", "Admin", "Activité des commandes et des équipes."),
    ("FinanceOverview", "Directeur financier", "Finance", "CA brut, remises et poids du fret."),
    ("LogisticsOverview", "Directeur logistique", "Logistique", "Expéditions, délais et transporteurs."),
    ("DataQualityTraceability", "Qualité des données", "Qualité", "Constats de l'amont et contrôles de qualité."),
)


# --------------------------------------------------------------------------- plan
class _Ctx:
    """Mesures (par id de KPI) et colonnes (par rôle de l'Agent 4) réellement disponibles."""

    def __init__(self, bundle: Any) -> None:
        from src.agents.dashboard.dashboard_generator import dax
        from src.agents.dashboard.generic_report import model_name

        dm = bundle.data_model or {}
        self.fact = dm.get("fact_table", "FactTrade")
        measures = dax(bundle.kpis, fact_table=self.fact)
        self.by_id = {k.id: k.name for k in bundle.kpis if k.name in measures}
        self.ids = {name: kid for kid, name in self.by_id.items()}
        columns = {str(c) for c in bundle.data.columns}
        self.role = {r: model_name(c) for r, c in (dm.get("roles") or {}).items() if isinstance(c, str) and c in columns}
        star = bundle.star or {}
        self.locations = star.get("locations") or {}
        self.dim_columns = {d["name"]: {c[0] for c in d.get("columns", [])} for d in star.get("dimensions", [])}

    def m(self, kpi_id: str, label: str | None = None) -> dict[str, str] | None:
        name = self.by_id.get(kpi_id)
        if not name:
            return None
        spec = {"kind": "Measure", "table": self.fact, "name": name}
        if label:
            spec["label"] = label
        return spec

    def col(self, role: str, label: str | None = None) -> dict[str, str] | None:
        name = self.role.get(role)
        if not name:
            return None
        spec = {"kind": "Column", "table": self.fact, "name": name}
        if label:
            spec["label"] = label
        return spec

    def sibling(self, role: str, hints: tuple[str, ...], label: str) -> dict[str, str] | None:
        """Autre colonne de la dimension qui porte un rôle (ex. la ville du client : DimCustomer[City])."""
        name = self.role.get(role)
        table = (self.locations.get(name) or [None])[0] if name else None
        for column in sorted(self.dim_columns.get(table, ())):
            if any(h in column.lower() for h in hints):
                return {"kind": "Column", "table": table, "name": column, "label": label}
        return None


def _card(ctx: _Ctx, kpi_id: str, label: str):
    from src.agents.dashboard.dashboard_planner import VisualPlan

    spec = ctx.m(kpi_id, label)
    if not spec:
        return None
    return VisualPlan(id=f"{kpi_id}_card", title=label, visual_type="card", kpi_reference=kpi_id, roles={"Values": [spec]},
                      selection_reason="Indicateur clé du rôle, en tête de page.", priority="high", evidence_references=[f"kpi:{kpi_id}"])


def _chart(vid: str, title: str, vtype: str, roles: dict[str, list[Any]], why: str, sort_by: dict[str, str] | None = None,
           kpi: str | None = None, labels: bool = True):
    from src.agents.dashboard.dashboard_planner import VisualPlan

    if any(not specs or any(s is None for s in specs) for specs in roles.values()):
        return None  # une mesure ou une colonne manque : le visuel n'est pas créé
    sort = [{"kind": sort_by["kind"], "table": sort_by["table"], "name": sort_by["name"], "direction": "Descending"}] if sort_by else None
    refs = [f"{s['table']}.{s['name']}" for specs in roles.values() for s in specs]
    return VisualPlan(id=vid, title=title, visual_type=vtype, kpi_reference=kpi, roles=roles, sort=sort,
                      display={"data_labels": labels}, selection_reason=why, priority="high", evidence_references=refs)


def can_build(bundle: Any) -> bool:
    """Le rapport par rôle demande l'étoile, une date, le CA, le nombre de commandes, et assez de KPI et de rôles
    pour que chacune des 4 pages de rôle ait au moins 2 cartes et 2 graphiques ; sinon le rapport générique est gardé."""
    if getattr(bundle, "trade_mode", True) or not getattr(bundle, "star", None):
        return False
    ctx = _Ctx(bundle)
    if not ctx.role.get("date") or not {"total_revenue", "order_count"} <= set(ctx.by_id):
        return False
    pages = {p.name: p for p in build_role_plan(bundle).pages}
    for name, *_ in PAGES[:4]:
        visuals = pages[name].visuals if name in pages else []
        cards = sum(1 for v in visuals if v.visual_type == "card")
        if cards < 2 or len(visuals) - cards < 2:
            return False
    return True


def build_role_plan(bundle: Any):
    from src.agents.dashboard.dashboard_planner import DashboardPlan, PagePlan, VisualPlan
    from src.agents.dashboard.generic_report import DATE_TABLE

    ctx = _Ctx(bundle)
    month = {"kind": "Column", "table": DATE_TABLE, "name": "Month", "label": "Mois"}
    ca = ctx.m("total_revenue", "Chiffre d'affaires")
    pages: list[PagePlan] = []

    def page(index: int, visuals: list[Any]) -> None:
        name, title, _, description = PAGES[index]
        visuals = [v for v in visuals if v is not None]
        taken = {v.id for p in pages for v in p.visuals}
        for v in visuals:  # une même carte (ex. CA) sur plusieurs pages : identifiant unique dans le plan
            if v.id in taken:
                v.id = f"{v.id}_{name[:5].lower()}"
            taken.add(v.id)
        if visuals:
            pages.append(PagePlan(name=name, display_name=title, description=description, visuals=visuals))

    city = ctx.sibling("customer", ("city", "ville"), "Ville")
    geo = city or ctx.col("country", "Pays")
    page(0, [
        _card(ctx, "total_revenue", "Chiffre d'affaires"), _card(ctx, "customer_count", "Clients actifs"),
        _card(ctx, "order_count", "Commandes"), _card(ctx, "avg_order_value", "Panier moyen"),
        _chart("revenue_by_city", f"CA par {'ville' if city else 'pays'}", "clusteredBarChart", {"Category": [geo], "Y": [ca]},
               "Où se fait le chiffre d'affaires : barres triées.", sort_by=ca, kpi="total_revenue"),
        _chart("revenue_trend", "Évolution du CA par mois", "areaChart", {"Category": [month], "Y": [ca]},
               "Tendance mensuelle du chiffre d'affaires.", kpi="total_revenue", labels=False),
        _chart("revenue_category_product", "CA par catégorie et produit", "treemap",
               {"Group": [ctx.col("category", "Catégorie")], "Details": [ctx.col("product", "Produit")], "Values": [ca]},
               "Hiérarchie catégorie puis produit en surfaces.", kpi="total_revenue"),
        _chart("category_share", "Part des catégories", "donutChart", {"Category": [ctx.col("category", "Catégorie")], "Y": [ca]},
               "Répartition du CA entre catégories.", kpi="top_categories" if "top_categories" in ctx.by_id else None),
    ])
    page(1, [
        _card(ctx, "order_count", "Commandes"), _card(ctx, "total_revenue", "Chiffre d'affaires"),
        _card(ctx, "best_employee", "Meilleur employé"), _card(ctx, "units_sold", "Unités vendues"),
        _chart("revenue_month_columns", "CA par mois", "clusteredColumnChart", {"Category": [month], "Y": [ca]},
               "Volume mensuel en colonnes.", kpi="total_revenue", labels=False),
        _chart("revenue_by_employee", "CA par employé", "clusteredBarChart", {"Category": [ctx.col("employee", "Employé")], "Y": [ca]},
               "Contribution de chaque employé.", sort_by=ca, kpi="top_employees" if "top_employees" in ctx.by_id else None),
        _chart("shipped_split", "Commandes expédiées / non expédiées", "donutChart",
               {"Y": [ctx.m("shipped_orders", "Expédiées"), ctx.m("unshipped_orders", "Non expédiées")]},
               "Deux mesures en parts d'un même total.", kpi="shipped_orders"),
        _chart("orders_by_country", "Commandes par pays", "clusteredBarChart",
               {"Category": [ctx.col("country", "Pays")], "Y": [ctx.m("order_count", "Commandes")]},
               "Nombre de commandes par pays du client.", sort_by=ctx.m("order_count"), kpi="order_count"),
    ])
    gross = ctx.m("gross_revenue", "CA brut")
    page(2, [
        _card(ctx, "gross_revenue", "CA brut"), _card(ctx, "discount_amount", "Remises accordées"),
        _card(ctx, "discount_rate", "Taux de remise"), _card(ctx, "freight_ratio", "Fret / CA"),
        _chart("gross_vs_net", "CA brut et CA net par mois", "lineChart", {"Category": [month], "Y": [gross, ca]},
               "L'écart entre les deux courbes est la remise.", kpi="gross_revenue", labels=False),
        _chart("discount_by_category", "Remises par catégorie", "clusteredColumnChart",
               {"Category": [ctx.col("category", "Catégorie")], "Y": [ctx.m("discount_amount", "Remises")]},
               "Où partent les remises.", sort_by=ctx.m("discount_amount"), kpi="discount_amount"),
        _chart("revenue_by_customer", "CA par client", "clusteredBarChart", {"Category": [ctx.col("customer", "Client")], "Y": [ca]},
               "Clients classés par chiffre d'affaires.", sort_by=ca, kpi="top_customers" if "top_customers" in ctx.by_id else None),
        _chart("product_finance", "Produits : volumes, remises et CA", "tableEx",
               {"Values": [ctx.col("product", "Produit"), ctx.m("units_sold", "Unités"), ctx.m("gross_revenue", "CA brut"),
                           ctx.m("discount_rate", "Remise %"), ctx.m("total_revenue", "CA net")]},
               "Détail par produit pour l'audit.", sort_by=ca, labels=False),
    ])
    shipped = ctx.m("shipped_orders", "Expédiées")
    page(3, [
        _card(ctx, "shipped_orders", "Commandes expédiées"), _card(ctx, "on_time_rate", "Taux à l'heure"),
        _card(ctx, "unshipped_orders", "Non expédiées"), _card(ctx, "late_orders", "En retard"),
        _card(ctx, "avg_lead_time", "Délai moyen d'expédition"),
        _chart("lead_time_by_shipper", "Délai moyen par transporteur", "clusteredColumnChart",
               {"Category": [ctx.col("shipper", "Transporteur")], "Y": [ctx.m("avg_lead_time", "Délai (jours)")]},
               "Comparaison des transporteurs sur le délai.", kpi="avg_lead_time"),
        _chart("freight_by_shipper", "Fret par transporteur", "donutChart",
               {"Category": [ctx.col("shipper", "Transporteur")], "Y": [ctx.m("total_freight", "Fret")]},
               "Part de chaque transporteur dans le fret payé.", kpi="total_freight"),
        _chart("delivery_by_employee", "Expédiées et en retard par employé", "clusteredBarChart",
               {"Category": [ctx.col("employee", "Employé")], "Y": [shipped, ctx.m("late_orders", "En retard")]},
               "Qui expédie, et avec combien de retards.", sort_by=shipped, kpi="late_orders"),
        _chart("shipped_gauge", "Commandes expédiées sur le total", "gauge",
               {"Y": [shipped], "MaxValue": [ctx.m("order_count", "Commandes")]},
               "Avancement des expéditions : expédiées / commandes.", kpi="shipped_orders", labels=False),
    ])
    finding_refs = [f["id"] for f in bundle.findings.get("findings", []) if isinstance(f, dict) and "id" in f]
    pages.append(PagePlan(name=PAGES[4][0], display_name=PAGES[4][1], description=PAGES[4][3], visuals=[
        VisualPlan(id="insights", title="Constats validés", visual_type="tableEx",
                   roles={"Values": [{"kind": "Column", "table": "BusinessInsights", "name": "Kind", "label": "Type"},
                                     {"kind": "Column", "table": "BusinessInsights", "name": "Text", "label": "Constat"}]},
                   selection_reason="Constats et recommandations de l'amont.", priority="high", evidence_references=finding_refs or ["findings"]),
        VisualPlan(id="quality", title="Indicateurs de qualité", visual_type="tableEx",
                   roles={"Values": [{"kind": "Column", "table": "DataQuality", "name": "Check", "label": "Contrôle"},
                                     {"kind": "Column", "table": "DataQuality", "name": "Result", "label": "Résultat"}]},
                   selection_reason="Contrôles de qualité et traçabilité.", priority="high", evidence_references=["quality_report"]),
    ]))
    refs = sorted({v.kpi_reference for p in pages for v in p.visuals if v.kpi_reference})
    missing = [label for kid, label in (("net_profit", "bénéfice net"), ("cogs", "coût des marchandises vendues")) if kid not in ctx.by_id]
    return DashboardPlan(
        dashboard_title="BI Flow : tableaux de bord par rôle",
        business_objective="Un tableau de bord par lecteur (commercial, administration, finance, logistique) sur le schéma en étoile.",
        target_audience="Direction commerciale, administration, direction financière, direction logistique",
        pages=pages, kpi_references=refs, insight_references=finding_refs, planning_mode="deterministic",
        warnings=[f"Non affiché faute de coût d'achat dans les données : {', '.join(missing)}."] if missing else [],
        metadata={"compiler": COMPILER, "fact_table": ctx.fact, "roles": ctx.role},
    )


# --------------------------------------------------------------------------- ossature des pages (PBIR)
def _lit(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (int, float)):
        text = f"{value}D"
    else:
        text = f"'{value}'"
    return {"expr": {"Literal": {"Value": text}}}


def _color(hex_color: str) -> dict[str, Any]:
    return {"solid": {"color": _lit(hex_color)}}


def _container(background: str | None = None, transparency: int = 0) -> dict[str, Any]:
    objects: dict[str, Any] = {"border": [{"properties": {"show": _lit(False)}}],
                               "dropShadow": [{"properties": {"show": _lit(False)}}]}
    objects["background"] = ([{"properties": {"show": _lit(True), "color": _color(background), "transparency": _lit(transparency)}}]
                             if background else [{"properties": {"show": _lit(False)}}])
    return objects


def _textbox(vid: str, runs: list[list[tuple[str, dict[str, str]]]], background: str | None = None) -> dict[str, Any]:
    paragraphs = [{"textRuns": [{"value": text, "textStyle": style} for text, style in line]} for line in runs]
    return {"id": vid, "config": {"visualType": "textbox", "objects": {"general": [{"properties": {"paragraphs": paragraphs}}]},
                                  "visualContainerObjects": _container(background), "drillFilterOtherVisuals": True}}


def _button(vid: str, label: str, target: str, current: bool) -> dict[str, Any]:
    """Bouton de navigation vers une autre page (visualLink PageNavigation) ; la page courante est en or."""
    fill, font = (GOLD, NAVY_DARK) if current else (PANEL, NAVY)
    return {"id": vid, "config": {
        "visualType": "actionButton",
        "objects": {
            "icon": [{"properties": {"shapeType": _lit("blank")}, "selector": {"id": "default"}}],
            "text": [{"properties": {"show": _lit(True)}},
                     {"properties": {"text": _lit(label), "fontColor": _color(font), "fontSize": _lit(10), "bold": _lit(True)},
                      "selector": {"id": "default"}}],
            "fill": [{"properties": {"show": _lit(True), "fillColor": _color(fill), "transparency": _lit(0)}, "selector": {"id": "default"}}],
            "outline": [{"properties": {"show": _lit(False)}}],
        },
        "visualContainerObjects": {
            **_container(),
            "visualLink": [{"properties": {"show": _lit(True), "type": _lit("PageNavigation"), "navigationSection": _lit(target)}}],
        },
        "drillFilterOtherVisuals": True,
    }}


def _slicers(page_name: str, plan_meta: dict[str, Any], fact: str, locations: dict[str, Any]) -> list[tuple[dict[str, Any], float]]:
    """Segments du panneau de gauche (même groupe de synchronisation sur toutes les pages) et leur hauteur."""
    from src.agents.dashboard.dashboard_generator import visual
    from src.agents.dashboard.generic_report import DATE_TABLE
    from src.agents.dashboard.star_schema import physical_field

    roles = plan_meta.get("roles") or {}
    specs: list[tuple[str, str, tuple[str, str], str, float]] = [
        ("year", "Année", (DATE_TABLE, "Year"), "Basic", 112.0),
        ("period", "Période", (DATE_TABLE, "Date"), "Between", 84.0),
    ]
    for role, label, height in (("country", "Pays", 62.0), ("category", "Catégorie", 62.0), ("customer", "Client", 62.0),
                                ("shipper", "Transporteur", 100.0)):
        if roles.get(role):
            mode = "Basic" if role == "shipper" else "Dropdown"
            specs.append((role, label, physical_field(fact, roles[role], fact, locations), mode, height))
    out = []
    for key, label, (table, column), mode, height in specs:
        slicer = visual(f"{page_name}_f_{key}", "slicer", {"Values": [("Column", table, column, label)]}, label,
                        display={"slicer_mode": mode})
        slicer["config"]["syncGroup"] = {"groupName": f"filtre_{key}", "fieldChanges": True, "filterChanges": True}
        out.append((slicer, height))
    return out


def _grid(n: int, top: float, bottom: float) -> list[tuple[float, float, float, float]]:
    """Graphiques deux par ligne ; un nombre impair donne toute la largeur au premier."""
    rows = ([[0]] if n % 2 else []) + [[i, i + 1] for i in range(n % 2, n, 2)]
    height = (bottom - top - (len(rows) - 1) * GAP) / max(1, len(rows))
    boxes: dict[int, tuple[float, float, float, float]] = {}
    for r, row in enumerate(rows):
        w = (CONTENT_W - (len(row) - 1) * GAP) / len(row)
        for k, i in enumerate(row):
            boxes[i] = (CONTENT_X + k * (w + GAP), top + r * (height + GAP), w, height)
    return [boxes[i] for i in range(n)]


def compose(page: dict[str, Any], report_pages: list[dict[str, Any]], bundle: Any) -> list[tuple[dict[str, Any], tuple[float, ...]]]:
    """(visuel, (x, y, largeur, hauteur)) de chaque élément de la page : panneau de filtres, bandeau,
    boutons de navigation, cartes KPI puis graphiques, sans chevauchement dans la page 1280×720."""
    fact = (bundle.data_model or {}).get("fact_table", "FactTrade")
    locations = (getattr(bundle, "star", None) or {}).get("locations") or {}
    meta = page.get("plan_metadata") or {}
    name = page["name"]
    items: list[tuple[dict[str, Any], tuple[float, ...]]] = []

    # Panneau de gauche : fond bleu nuit, marque, segments synchronisés.
    items.append((_textbox(f"{name}_side", [[("BI Flow", {"fontWeight": "bold", "fontSize": "16pt", "color": WHITE})],
                                             [("Filtres", {"fontSize": "10pt", "color": "#AFC0DA"})]], NAVY_DARK),
                  (0.0, 0.0, SIDE_W, PAGE_H)))
    y = 70.0
    data_page = name != PAGES[4][0]  # la page des constats et de la qualité n'a pas de données filtrables
    for slicer, height in (_slicers(name, meta, fact, locations) if data_page else []):
        if y + height > PAGE_H - PAD:
            break
        items.append((slicer, (12.0, y, SIDE_W - 24.0, height)))
        y += height + 8.0

    # Bandeau : titre du rôle et boutons vers les autres pages.
    labels = {p[0]: p[2] for p in PAGES}
    titles = {p[0]: p[1] for p in PAGES}
    items.append((_textbox(f"{name}_header", [
        [("Dashboard ", {"fontSize": "18pt", "color": "#AFC0DA"}),
         (titles.get(name, page.get("displayName", "")), {"fontWeight": "bold", "fontSize": "18pt", "color": WHITE})],
        [(page.get("description", ""), {"fontSize": "10pt", "color": "#AFC0DA"})]]),
        (CONTENT_X, HEADER_Y, 520.0, HEADER_H)))
    nav = [p for p in report_pages if p["name"] in labels]
    if nav:
        left = CONTENT_X + 532.0
        width = (PAGE_W - PAD - left - (len(nav) - 1) * 8.0) / len(nav)
        for k, target in enumerate(nav):
            items.append((_button(f"{name}_nav_{k}", labels[target["name"]], target["name"], target["name"] == name),
                          (left + k * (width + 8.0), HEADER_Y + 10.0, width, 38.0)))

    containers = page["visualContainers"]
    cards = [c for c in containers if c["config"].get("visualType") in {"card", "multiRowCard"}]
    charts = [c for c in containers if c not in cards]
    top = HEADER_Y + HEADER_H + GAP
    if cards:
        w = (CONTENT_W - (len(cards) - 1) * GAP) / len(cards)
        for k, card in enumerate(cards):
            items.append((card, (CONTENT_X + k * (w + GAP), CARDS_Y, w, CARD_H)))
        top = CARDS_Y + CARD_H + GAP
    for chart, box in zip(charts, _grid(len(charts), top, PAGE_H - PAD)):
        items.append((chart, box))
    return [(c, tuple(round(v, 1) for v in box)) for c, box in items]


# --------------------------------------------------------------------------- thème
def _solid(color: str) -> dict[str, Any]:
    return {"solid": {"color": color}}


def report_theme() -> dict[str, Any]:
    """Thème de la maquette : page bleu nuit, visuels en panneaux gris-bleu arrondis, chiffres rouge foncé."""
    return {
        "name": "BI Flow par rôle",
        "dataColors": list(PALETTE),
        "background": PANEL, "foreground": INK, "tableAccent": RED,
        "textClasses": {
            "callout": {"fontSize": 26, "fontFace": "Segoe UI Bold", "color": RED},
            "title": {"fontSize": 12, "fontFace": "Segoe UI Semibold", "color": INK},
            "header": {"fontSize": 11, "fontFace": "Segoe UI Semibold", "color": INK},
            "label": {"fontSize": 10, "fontFace": "Segoe UI", "color": MUTED},
        },
        "visualStyles": {
            "*": {"*": {
                "background": [{"show": True, "color": _solid(PANEL), "transparency": 0}],
                "border": [{"show": True, "color": _solid("#C9D3E3"), "radius": 12}],
                "dropShadow": [{"show": True, "color": _solid("#000000"), "transparency": 70}],
                "title": [{"show": True, "fontColor": _solid(INK), "fontSize": 12, "bold": True}],
            }},
            "page": {"*": {
                "background": [{"color": _solid(NAVY), "transparency": 0}],
                "outspace": [{"color": _solid(NAVY), "transparency": 0}],
            }},
            "card": {"*": {
                "labels": [{"color": _solid(RED), "fontSize": 26}],
                "categoryLabels": [{"show": True, "color": _solid(MUTED), "fontSize": 11}],
            }},
            "slicer": {"*": {
                "background": [{"show": True, "color": _solid(WHITE), "transparency": 0}],
                "border": [{"show": True, "color": _solid("#2B3B5E"), "radius": 8}],
                "dropShadow": [{"show": False}],
            }},
            "tableEx": {"*": {
                "columnHeaders": [{"fontColor": _solid(WHITE), "backColor": _solid(NAVY), "bold": True}],
                "values": [{"backColorPrimary": _solid(WHITE), "backColorSecondary": _solid("#F2F5FA")}],
            }},
        },
    }


def is_role_plan(plan: Any) -> bool:
    return bool(plan is not None and (getattr(plan, "metadata", None) or {}).get("compiler") == COMPILER)
