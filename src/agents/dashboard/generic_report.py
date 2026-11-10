"""Rapport Power BI diversifié pour les jeux de données « génériques » (hors contrat import/export).

Le générateur de Hashem a deux contrats d'entrée. En mode trade (date, country, product, direction,
value), il construit ses 6 pages historiques. En mode générique (ex. Northwind), il ne produisait que
3 pages (cartes KPI, constats, qualité) alors que le plan, le critique et le HTML décrivaient les
6 pages trade avec des champs absents du modèle (FactTrade.Direction, FactTrade.Value...).

Ce module est la source unique du rapport générique :

  - ``build_generic_plan(bundle)``   : un DashboardPlan (≤ 6 pages, ≤ 6 visuels par page) construit à
    partir des colonnes RÉELLES de la table de faits, des rôles reconnus par l'Agent 4 et des mesures
    DAX transmises. Le PBIP, dashboard_plan.json, le critique et le HTML lisent tous ce même plan ;
  - ``calendar_table`` / ``DATE_TABLE`` : table de dates continue (année, trimestre, mois) reliée à la
    date principale, pour les tendances mensuelles et annuelles ;
  - ``format_string``                  : format d'affichage de chaque mesure (%, nombres, jours) ;
  - ``layout``                         : grille de placement (segments, cartes, puis graphiques) ;
  - ``evaluate``                       : calcul pandas des mêmes mesures (contrôle des formules DAX).

Rien n'est inventé : chaque visuel n'utilise qu'une colonne de la table de faits, une colonne de la
table de dates générée ou une mesure du catalogue KPI.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

DATE_TABLE = "DimDate"
# (nom, type du modèle tabulaire) ; Year et Month sont du texte pour un axe catégoriel trié (AAAA-MM).
DATE_COLUMNS = (("Date", "dateTime"), ("Year", "string"), ("Quarter", "string"), ("MonthNumber", "int64"), ("Month", "string"))
PALETTE = ("#118DFF", "#E66C37", "#D9B300", "#E044A7", "#1AAB40", "#15C6F4", "#744EC2", "#D64550", "#A0D1FF", "#F5A97F")

PAGE_W, PAGE_H, MARGIN, GAP = 1280.0, 720.0, 16.0, 12.0
SLICER_H, CARD_H = 56.0, 104.0
CARD_TYPES = {"card", "multiRowCard"}

# Rôles reconnus par nom de colonne (anglais et français), dans l'ordre de priorité des pages.
_ROLE_HINTS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("geo", ("country", "pays", "region", "région", "city", "ville", "state", "nation", "territor"), "pays"),
    ("product", ("product", "produit", "item", "article", "sku"), "produit"),
    ("category", ("categor", "catégor", "family", "famille", "segment", "gamme"), "catégorie"),
    ("customer", ("customer", "client", "compan", "account", "société"), "client"),
    ("employee", ("employee", "employe", "employé", "salesperson", "vendeur", "seller", "rep"), "employé"),
    ("shipper", ("shipper", "carrier", "transporteur", "livreur", "courier"), "transporteur"),
)
_QUANTITY_HINTS = ("quantity", "quantité", "quantite", "qty", "units", "unités")
_LEAD_HINTS = ("lead", "délai", "delai", "delay", "durée", "duree")


# --------------------------------------------------------------------------- analyse des entrées
@dataclass
class Dim:
    source: str          # nom de colonne d'origine
    name: str            # nom de colonne dans le modèle Power BI
    role: str            # geo / product / category / customer / employee / shipper / other
    label: str           # libellé français pour les titres
    distinct: int


@dataclass
class Analysis:
    fact: str
    columns: dict[str, str]                      # source -> nom dans le modèle
    date: str | None                             # colonne de date principale (nom du modèle)
    dims: list[Dim]
    value: tuple[str, str, str] | None           # (kind, table, name) de la mesure principale
    value_label: str
    count: str | None                            # mesure de comptage (DISTINCTCOUNT / COUNTROWS)
    ratios: list[str]                            # autres mesures scalaires
    shares: dict[str, str]                       # colonne du modèle -> mesure de part (ALL(...))
    quantity: str | None                         # colonne de quantité (somme)
    measures: dict[str, str]                     # nom -> DAX
    kpi_ids: dict[str, str] = field(default_factory=dict)  # nom de mesure -> id KPI

    def dim(self, role: str) -> Dim | None:
        return next((d for d in self.dims if d.role == role), None)


def model_name(column: Any) -> str:
    """Nom de colonne dans le modèle : celui du générateur (1re lettre en majuscule)."""
    text = str(column)
    return text[:1].upper() + text[1:]


def _humanize(column: str) -> str:
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", column).replace("_", " ").strip().lower()
    return re.sub(r"\s+name$", "", words) or column


def _role_of(column: str) -> tuple[str, str]:
    lower = column.lower()
    for role, hints, label in _ROLE_HINTS:
        if any(h in lower for h in hints):
            if role == "geo" and "country" not in lower and "pays" not in lower:
                return role, _humanize(column)
            return role, label
    return "other", _humanize(column)


def _is_id(column: str, distinct: int, rows: int) -> bool:
    lower = column.lower()
    return lower.endswith("id") or lower.endswith("_id") or lower in {"id", "key", "code"} or (rows > 20 and distinct > 0.9 * rows)


def _is_date(series: pd.Series, column: str) -> bool:
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    if pd.api.types.is_numeric_dtype(series) or not any(h in column.lower() for h in ("date", "time", "day", "jour")):
        return False
    parsed = pd.to_datetime(series.dropna().astype(str).head(200), errors="coerce")
    return len(parsed) > 0 and parsed.notna().mean() >= 0.9


def date_column(bundle: Any) -> str | None:
    """Colonne de date principale, nom du modèle : rôle « date » de l'Agent 4, sinon 1re colonne datée."""
    data = bundle.data
    roles = (bundle.data_model or {}).get("roles") or {}
    preferred = roles.get("date")
    if preferred in data.columns and _is_date(data[preferred], str(preferred)):
        return model_name(preferred)
    for column in data.columns:
        if _is_date(data[column], str(column)):
            return model_name(column)
    return None


_SHARE = r"ALL\s*\(\s*'?{table}'?\[([^\]]+)\]\s*\)"


def _measure_kind(dax_expr: str, fact: str) -> tuple[str, str | None]:
    text = " ".join(dax_expr.split())
    share = re.search(_SHARE.format(table=re.escape(fact)), text, re.IGNORECASE)
    if share and text.upper().startswith("DIVIDE"):
        return "share", share.group(1)
    if re.fullmatch(r"SUM\s*\(.+\)", text, re.IGNORECASE):
        return "additive", None
    if re.fullmatch(r"(DISTINCTCOUNT|COUNTROWS|COUNT)\s*\(.+\)", text, re.IGNORECASE):
        return "count", None
    return "ratio", None


def analyse(bundle: Any, measures: dict[str, str] | None = None) -> Analysis:
    from src.agents.dashboard.dashboard_generator import dax

    data: pd.DataFrame = bundle.data
    fact = (bundle.data_model or {}).get("fact_table", "FactTrade")
    measures = measures if measures is not None else dax(bundle.kpis, fact_table=fact)
    columns = {str(c): model_name(c) for c in data.columns}
    date = date_column(bundle)
    roles = {v: k for k, v in ((bundle.data_model or {}).get("roles") or {}).items() if isinstance(v, str)}
    rows = len(data)

    dims: list[Dim] = []
    for source in data.columns:
        series = data[source]
        if pd.api.types.is_numeric_dtype(series) or _is_date(series, str(source)):
            continue
        distinct = int(series.dropna().astype(str).str.strip().replace("", pd.NA).dropna().nunique())
        if distinct < 2 or distinct > 500 or _is_id(str(source), distinct, rows):
            continue
        role, label = _role_of(str(source))
        role = {"country": "geo", "product": "product"}.get(roles.get(str(source), ""), role)
        dims.append(Dim(str(source), model_name(source), role, label, distinct))
    order = {r: i for i, (r, _, _) in enumerate(_ROLE_HINTS)}
    dims.sort(key=lambda d: (order.get(d.role, len(order)), d.distinct))
    seen: set[str] = set()
    for d in dims:  # un seul axe par rôle ; les doublons de rôle passent en « autre »
        if d.role in seen and d.role != "other":
            d.role = "other"
        seen.add(d.role)

    value = count = None
    ratios: list[str] = []
    shares: dict[str, str] = {}
    for name, expression in measures.items():
        kind, column = _measure_kind(expression, fact)
        if kind == "share" and column:
            shares.setdefault(model_name(column), name)
        elif kind == "additive" and value is None:
            value = ("Measure", fact, name)
        elif kind == "count" and count is None:
            count = name
        else:
            ratios.append(name)
    value_label = value[2] if value else ""
    if value is None:  # pas de mesure additive : somme de la colonne « montant »
        source = ((bundle.data_model or {}).get("roles") or {}).get("value")
        numeric = [c for c in data.columns if pd.api.types.is_numeric_dtype(data[c]) and not _is_id(str(c), int(data[c].nunique()), rows)]
        source = source if source in numeric else (numeric[0] if numeric else None)
        if source is not None:
            value = ("Sum", fact, model_name(source))
            value_label = f"Somme de {_humanize(str(source))}"
    quantity = next((model_name(c) for c in data.columns if pd.api.types.is_numeric_dtype(data[c])
                     and any(h in str(c).lower() for h in _QUANTITY_HINTS)), None)
    kpi_ids = {k.name: k.id for k in bundle.kpis if k.name in measures}
    return Analysis(fact, columns, date, dims, value, value_label, count, ratios, shares, quantity, measures, kpi_ids)


# --------------------------------------------------------------------------- plan
def _m(a: Analysis, name: str, label: str | None = None) -> dict[str, str]:
    spec = {"kind": "Measure", "table": a.fact, "name": name}
    if label:
        spec["label"] = label
    return spec


def _value(a: Analysis) -> dict[str, str]:
    kind, table, name = a.value  # type: ignore[misc]
    return {"kind": kind, "table": table, "name": name}


def _col(table: str, name: str, label: str | None = None) -> dict[str, str]:
    spec = {"kind": "Column", "table": table, "name": name}
    if label:
        spec["label"] = label
    return spec


def _desc(spec: dict[str, str]) -> list[dict[str, str]]:
    return [{"kind": spec["kind"], "table": spec["table"], "name": spec["name"], "direction": "Descending"}]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40]


def _is_lead(name: str) -> bool:
    return any(h in name.lower() for h in _LEAD_HINTS)


def _dim_chart(a: Analysis, d: Dim, measure: dict[str, str] | None = None, title: str | None = None, vid: str | None = None):
    from src.agents.dashboard.dashboard_planner import VisualPlan

    measure = measure or _value(a)
    label = measure.get("label") or (a.value_label if measure == _value(a) else measure["name"])
    if d.distinct <= 6 and measure == _value(a):
        vtype, role, why = "donutChart", "Y", "Peu de modalités : la répartition se lit en parts."
    elif d.distinct <= 12:
        vtype, role, why = "clusteredColumnChart", "Y", "Peu de catégories : colonnes comparables d'un coup d'œil."
    else:
        vtype, role, why = "clusteredBarChart", "Y", "Beaucoup de catégories : barres horizontales triées, libellés lisibles."
    return VisualPlan(
        id=vid or f"{_slug(measure['name'])}_by_{_slug(d.name)}", title=title or f"{label} par {d.label}", visual_type=vtype,
        kpi_reference=a.kpi_ids.get(measure["name"]) if measure["kind"] == "Measure" else None,
        roles={"Category": [_col(a.fact, d.name)], role: [measure]}, sort=_desc(measure),
        display={"data_labels": True}, selection_reason=why, priority="high",
        evidence_references=[f"{a.fact}.{d.name}", measure["name"]])


def _detail_table(a: Analysis, dims: list[Dim], title: str, vid: str, extra: list[str] | None = None):
    from src.agents.dashboard.dashboard_planner import VisualPlan

    values = [_col(a.fact, d.name, d.label.capitalize()) for d in dims] + [_value(a)]
    # A share measure is ALL(fact[dim]) over ONE column: with a second column in the table it would
    # turn into a share within that second column (e.g. within the category), so it is left out.
    share = a.shares.get(dims[0].name) if len(dims) == 1 else None
    if share:
        values.append(_m(a, share, "Part du total"))
    if a.count:
        values.append(_m(a, a.count))
    for name in extra or []:
        if name in a.measures:
            values.append(_m(a, name))
    if a.quantity and dims[0].role == "product":
        values.append({"kind": "Sum", "table": a.fact, "name": a.quantity, "label": "Quantité"})
    return VisualPlan(id=vid, title=title, visual_type="tableEx", roles={"Values": values}, sort=_desc(_value(a)),
                      selection_reason="Tableau trié pour l'audit ligne à ligne.", priority="medium",
                      evidence_references=[f"{a.fact}.{d.name}" for d in dims])


def build_generic_plan(bundle: Any):
    """Plan déterministe du rapport générique : 6 pages au plus, 6 visuels au plus par page."""
    from src.agents.dashboard.dashboard_planner import DashboardPlan, PagePlan, VisualPlan

    a = analyse(bundle)
    pages: list[PagePlan] = []
    month = _col(DATE_TABLE, "Month", "Mois")
    year = _col(DATE_TABLE, "Year", "Année")

    # 1. Vue d'ensemble : les mesures scalaires en cartes + la tendance de la mesure principale.
    scalars = ([a.value[2]] if a.value and a.value[0] == "Measure" else []) + ([a.count] if a.count else []) + a.ratios
    overview = [VisualPlan(id=f"{a.kpi_ids.get(n, _slug(n))}_card", title=n, visual_type="card", kpi_reference=a.kpi_ids.get(n),
                           roles={"Values": [_m(a, n)]}, selection_reason="Indicateur clé en tête de rapport.", priority="high",
                           evidence_references=[f"kpi:{a.kpi_ids.get(n, n)}"]) for n in scalars[:5]]
    if a.value and a.date:
        overview.append(VisualPlan(id="trend_month", title=f"{a.value_label} par mois", visual_type="lineChart",
                                   roles={"Category": [month], "Y": [_value(a)]}, selection_reason="Tendance mensuelle de la mesure principale.",
                                   priority="high", evidence_references=[f"{DATE_TABLE}.Month", a.value_label]))
    elif a.value and a.dims:
        overview.append(_dim_chart(a, a.dims[0], vid="overview_breakdown"))
    if overview:
        pages.append(PagePlan(name="ExecutiveOverview", display_name="Vue d'ensemble",
                              description="Indicateurs clés et tendance mensuelle.", visuals=overview))

    # 2. Évolution : segment Année, CA par année, combiné CA / volume, ratios mois par mois.
    if a.value and a.date:
        trend = [VisualPlan(id="year_slicer", title="Année", visual_type="slicer", roles={"Values": [year]},
                            display={"slicer_mode": "Dropdown"}, selection_reason="Filtre la page par année.", priority="medium",
                            evidence_references=[f"{DATE_TABLE}.Year"]),
                 VisualPlan(id="value_by_year", title=f"{a.value_label} par année", visual_type="clusteredColumnChart",
                            roles={"Category": [year], "Y": [_value(a)]}, display={"data_labels": True},
                            selection_reason="Comparaison annuelle (années incomplètes signalées dans les constats).", priority="high",
                            evidence_references=[f"{DATE_TABLE}.Year"])]
        if a.count:
            trend.append(VisualPlan(id="value_count_month", title=f"{a.value_label} et {a.count} par mois",
                                    visual_type="lineClusteredColumnComboChart",
                                    roles={"Category": [month], "ColumnY": [_value(a)], "LineY": [_m(a, a.count)]},
                                    selection_reason="Montant et volume sur le même axe de temps.", priority="high",
                                    evidence_references=[a.value_label, a.count]))
        for name in a.ratios[:3]:
            trend.append(VisualPlan(id=f"{_slug(name)}_month", title=f"{name} par mois",
                                    visual_type="areaChart" if _is_lead(name) else "lineChart",
                                    kpi_reference=a.kpi_ids.get(name), roles={"Category": [month], "Y": [_m(a, name)]},
                                    selection_reason="Suivi mensuel d'un ratio.", priority="medium", evidence_references=[name]))
        pages.append(PagePlan(name="TrendAnalysis", display_name="Évolution", description="Années, mois et ratios dans le temps.",
                              visuals=trend[:6]))

    # 3. Géographie.
    geo = a.dim("geo")
    if a.value and geo:
        visuals = [_dim_chart(a, geo)]
        visuals.append(_detail_table(a, [geo], f"Détail par {geo.label}", f"{_slug(geo.name)}_detail", extra=a.ratios[:1]))
        if a.ratios:
            visuals.append(_dim_chart(a, geo, _m(a, a.ratios[0]), vid=f"{_slug(a.ratios[0])}_by_{_slug(geo.name)}"))
        pages.append(PagePlan(name="GeographyAnalysis", display_name=f"Par {geo.label}",
                              description=f"Contribution de chaque {geo.label}.", visuals=visuals))

    # 4. Produits (et catégories).
    product, category = a.dim("product"), a.dim("category")
    if a.value and (product or category):
        visuals = []
        if product and category:
            visuals.append(VisualPlan(id="category_product_treemap", title=f"{a.value_label} par {category.label} et {product.label}",
                                      visual_type="treemap",
                                      roles={"Group": [_col(a.fact, category.name)], "Details": [_col(a.fact, product.name)], "Values": [_value(a)]},
                                      selection_reason="Hiérarchie catégorie → produit en surfaces.", priority="high",
                                      evidence_references=[f"{a.fact}.{category.name}", f"{a.fact}.{product.name}"]))
        if category:
            visuals.append(_dim_chart(a, category, vid=f"value_by_{_slug(category.name)}"))
        if product:
            visuals.append(_dim_chart(a, product, vid=f"value_by_{_slug(product.name)}"))
            visuals.append(_detail_table(a, [product] + ([category] if category else []), f"Détail par {product.label}",
                                         f"{_slug(product.name)}_detail"))
        main = product or category
        pages.append(PagePlan(name="ProductAnalysis", display_name=f"Par {main.label}",
                              description="Ce qui se vend le plus, par catégorie et par article.", visuals=visuals[:6]))

    # 5. Autres axes (clients, employés, transporteurs...).
    others = [d for d in a.dims if d.role not in {"geo", "product", "category"}]
    if a.value and others:
        visuals = []
        lead = next((n for n in a.ratios if _is_lead(n)), None)
        for d in others[:4]:
            if d.distinct > 30:
                visuals.append(_detail_table(a, [d] + ([geo] if geo else []), f"Classement par {d.label}", f"{_slug(d.name)}_ranking"))
            else:
                visuals.append(_dim_chart(a, d, vid=f"value_by_{_slug(d.name)}"))
        small = next((d for d in others if d.role == "shipper"), None) or next((d for d in others if d.distinct <= 12), None)
        if lead and small and len(visuals) < 6:
            visuals.append(_dim_chart(a, small, _m(a, lead), vid=f"{_slug(lead)}_by_{_slug(small.name)}"))
        pages.append(PagePlan(name="OperationsAnalysis", display_name="Clients et opérations",
                              description="Les autres axes d'analyse de la table de faits.", visuals=visuals[:6]))

    # 6. Constats et qualité (constats de l'Agent 4, contrôles de qualité, diagnostics du générateur).
    finding_refs = [f["id"] for f in bundle.findings.get("findings", []) if isinstance(f, dict) and "id" in f]
    pages.append(PagePlan(name="DataQualityTraceability", display_name="Constats et qualité",
                          description="Constats validés en amont et indicateurs de qualité.", visuals=[
        VisualPlan(id="insights", title="Constats validés", visual_type="tableEx",
                   roles={"Values": [_col("BusinessInsights", "Kind", "Type"), _col("BusinessInsights", "Text", "Constat")]},
                   selection_reason="Constats et recommandations de l'amont.", priority="high", evidence_references=finding_refs or ["findings"]),
        VisualPlan(id="quality", title="Indicateurs de qualité", visual_type="tableEx",
                   roles={"Values": [_col("DataQuality", "Check", "Contrôle"), _col("DataQuality", "Result", "Résultat")]},
                   selection_reason="Contrôles de qualité et traçabilité.", priority="high", evidence_references=["quality_report"]),
    ]))
    pages = pages[:6]
    return DashboardPlan(
        dashboard_title="BI Flow : tableau de bord",
        business_objective="Indicateurs clés, tendances et contributions par axe, calculés sur la table de faits de l'Agent 4.",
        target_audience="Direction et équipes opérationnelles",
        pages=pages,
        kpi_references=[a.kpi_ids[n] for n in scalars if n in a.kpi_ids],
        insight_references=finding_refs,
        planning_mode="deterministic",
        metadata={"compiler": "generic_report", "fact_table": a.fact, "date_column": a.date,
                  "dimensions": [d.name for d in a.dims]},
    )


# --------------------------------------------------------------------------- modèle sémantique
def calendar_table(date_col: str) -> list[str]:
    """M d'une table de dates continue (années complètes) dérivée de la date principale de TradeData."""
    return [
        "let",
        f'    Dates = List.RemoveNulls(Table.Column(TradeData, "{date_col}")),',
        "    Start = Date.StartOfYear(List.Min(Dates)),",
        "    End = Date.EndOfYear(List.Max(Dates)),",
        "    Days = List.Dates(Start, Duration.Days(End - Start) + 1, #duration(1, 0, 0, 0)),",
        '    Base = Table.TransformColumnTypes(Table.FromList(Days, Splitter.SplitByNothing(), {"Date"}), {{"Date", type date}}),',
        '    Year = Table.AddColumn(Base, "Year", each Text.From(Date.Year([Date])), type text),',
        '    Quarter = Table.AddColumn(Year, "Quarter", each Text.From(Date.Year([Date])) & " T" & Text.From(Date.QuarterOfYear([Date])), type text),',
        '    MonthNumber = Table.AddColumn(Quarter, "MonthNumber", each Date.Month([Date]), Int64.Type),',
        '    Month = Table.AddColumn(MonthNumber, "Month", each Text.From(Date.Year([Date])) & "-" & Text.PadStart(Text.From(Date.Month([Date])), 2, "0"), type text)',
        "in",
        "    Month",
    ]


def dimension_expression(column: str, text: bool) -> list[str]:
    """M d'une dimension : valeurs distinctes NON vides (Power BI refuse un vide côté « un » d'une relation)."""
    distinct = f'Table.Distinct(NonBlank, {{"{column}", Comparer.OrdinalIgnoreCase}})' if text else "Table.Distinct(NonBlank)"
    return [
        "let",
        f'    Source = Table.SelectColumns(TradeData, {{"{column}"}}),',
        f'    NonBlank = Table.SelectRows(Source, each Record.Field(_, "{column}") <> null and Record.Field(_, "{column}") <> ""),',
        f"    Distinct = {distinct}",
        "in",
        "    Distinct",
    ]


def format_string(name: str, expression: str) -> str:
    text = " ".join(expression.split()).upper()
    if re.match(r"(MAXX|MINX|FIRSTNONBLANK|LASTNONBLANK)\s*\(\s*TOPN\b", text) or text.startswith("SELECTEDVALUE"):
        return ""  # mesure texte (ex. meilleur employé) : pas de format numérique
    if text.startswith("DIVIDE") and re.search(r"\bALL\s*\(", text):
        return "0.0%"
    if re.fullmatch(r"(DISTINCTCOUNT|COUNTROWS|COUNT)\s*\(.+\)", text):
        return "#,0"
    if "DATEDIFF" in text and "DAY" in text:
        return '0.0" j"'
    if re.search(r"\*\s*100\s*$", text):
        return '0.0" %"'
    if re.fullmatch(r"SUM\s*\(.+\)", text):
        return "#,0"
    return "#,0.00"


# --------------------------------------------------------------------------- mise en page
# Chaque page : un bandeau (titre + description à gauche, segment Année synchronisé à droite), une rangée
# de cartes, puis les graphiques. Un tableau prend la colonne de droite sur toute la hauteur et les
# graphiques s'empilent à gauche ; sans tableau, les graphiques vont deux par ligne.
HEADER_H, SLICER_W = 60.0, 230.0
HEADER_TYPES = {"textbox", "slicer"}
TABLE_TYPES = {"tableEx", "pivotTable"}
THEME_NAME = "BIFlowTheme.json"
INK, MUTED, LINE, CANVAS = "#1F2937", "#6B7280", "#E5E7EB", "#F3F4F6"


def _solid(color: str) -> dict[str, Any]:
    return {"solid": {"color": color}}


def report_theme() -> dict[str, Any]:
    """Thème du rapport (appliqué par-dessus le thème de base) : fond de page gris clair, visuels en cartes
    blanches arrondies, titres sobres, palette du rapport, en-têtes de tableau foncés."""
    return {
        "name": "BI Flow",
        "dataColors": list(PALETTE),
        "background": "#FFFFFF", "foreground": INK, "tableAccent": PALETTE[0],
        "textClasses": {
            "callout": {"fontSize": 24, "fontFace": "Segoe UI Semibold", "color": INK},
            "title": {"fontSize": 12, "fontFace": "Segoe UI Semibold", "color": INK},
            "header": {"fontSize": 11, "fontFace": "Segoe UI Semibold", "color": INK},
            "label": {"fontSize": 10, "fontFace": "Segoe UI", "color": "#4B5563"},
        },
        "visualStyles": {
            "*": {"*": {
                "background": [{"show": True, "color": _solid("#FFFFFF"), "transparency": 0}],
                "border": [{"show": True, "color": _solid(LINE), "radius": 10}],
                "dropShadow": [{"show": True, "color": _solid("#000000"), "transparency": 90}],
                "title": [{"show": True, "fontColor": _solid(INK), "fontSize": 12, "bold": True}],
            }},
            "page": {"*": {
                "background": [{"color": _solid(CANVAS), "transparency": 0}],
                "outspace": [{"color": _solid(CANVAS), "transparency": 0}],
            }},
            "textbox": {"*": {
                "background": [{"show": False}], "border": [{"show": False}], "dropShadow": [{"show": False}],
            }},
            "card": {"*": {
                "labels": [{"color": _solid(INK), "fontSize": 24}],
                "categoryLabels": [{"show": True, "color": _solid(MUTED), "fontSize": 10}],
                "border": [{"show": True, "color": _solid(PALETTE[0]), "radius": 10}],
            }},
            "tableEx": {"*": {
                "columnHeaders": [{"fontColor": _solid("#FFFFFF"), "backColor": _solid(INK), "bold": True}],
                "values": [{"backColorPrimary": _solid("#FFFFFF"), "backColorSecondary": _solid("#F9FAFB")}],
            }},
        },
    }


def _bool(value: bool) -> dict[str, Any]:
    return {"expr": {"Literal": {"Value": "true" if value else "false"}}}


def header_textbox(page_name: str, title: str, description: str) -> dict[str, Any]:
    """Zone de texte du bandeau : titre de la page en gras, description en gris."""
    paragraphs = [{"textRuns": [{"value": title, "textStyle": {"fontWeight": "bold", "fontSize": "18pt", "color": INK}}]}]
    if description:
        paragraphs.append({"textRuns": [{"value": description, "textStyle": {"fontSize": "10pt", "color": MUTED}}]})
    return {"id": f"{page_name}_header", "config": {
        "visualType": "textbox",
        "objects": {"general": [{"properties": {"paragraphs": paragraphs}}]},
        "visualContainerObjects": {"background": [{"properties": {"show": _bool(False)}}],
                                   "border": [{"properties": {"show": _bool(False)}}],
                                   "dropShadow": [{"properties": {"show": _bool(False)}}]},
        "drillFilterOtherVisuals": True,
    }}


def _entities(container: dict[str, Any]) -> set[str]:
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "Entity" in node:
                found.add(node["Entity"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(container.get("config", {}).get("query", {}))
    return found


def with_page_header(page: dict[str, Any], year_slicer: bool) -> list[dict[str, Any]]:
    """Visuels de la page précédés du bandeau ; le segment Année (DimDate[Year]) est synchronisé sur
    toutes les pages qui portent des données (pas sur la page des constats et de la qualité)."""
    from src.agents.dashboard.dashboard_generator import visual

    containers = [dict(c) for c in page["visualContainers"]]
    data_page = any(_entities(c) - {"BusinessInsights", "DataQuality"} for c in containers)
    has_year = False
    for c in containers:
        cfg = c["config"]
        if cfg.get("visualType") == "slicer" and DATE_TABLE in _entities(c):
            c["config"] = {**cfg, "syncGroup": {"groupName": "Annee", "fieldChanges": True, "filterChanges": True}}
            has_year = True
    header = [header_textbox(page["name"], page.get("displayName", ""), page.get("description", ""))]
    if year_slicer and data_page and not has_year:
        slicer = visual(f"{page['name']}_year", "slicer", {"Values": [("Column", DATE_TABLE, "Year", "Année")]}, "Année",
                        display={"slicer_mode": "Dropdown"})
        slicer["config"]["syncGroup"] = {"groupName": "Annee", "fieldChanges": True, "filterChanges": True}
        header.append(slicer)
    return header + containers


def layout(visual_types: list[str]) -> list[tuple[float, float, float, float]]:
    """(x, y, largeur, hauteur) de chaque visuel, dans la page 1280×720 et sans chevauchement."""
    positions: dict[int, tuple[float, float, float, float]] = {}
    inner = PAGE_W - 2 * MARGIN
    y = MARGIN
    texts = [i for i, t in enumerate(visual_types) if t == "textbox"]
    slicers = [i for i, t in enumerate(visual_types) if t == "slicer"]
    cards = [i for i, t in enumerate(visual_types) if t in CARD_TYPES]
    tables = [i for i, t in enumerate(visual_types) if t in TABLE_TYPES]
    charts = [i for i, t in enumerate(visual_types) if t not in HEADER_TYPES | CARD_TYPES | TABLE_TYPES]
    if texts or slicers:
        width = min(SLICER_W, (inner * 0.6 - max(0, len(slicers) - 1) * GAP) / max(1, len(slicers)))
        right = PAGE_W - MARGIN
        for i in reversed(slicers):
            right -= width
            positions[i] = (right, y, width, HEADER_H)
            right -= GAP
        text_w = (right - MARGIN) / max(1, len(texts)) if slicers else inner / max(1, len(texts))
        for n, i in enumerate(texts):
            positions[i] = (MARGIN + n * text_w, y, text_w - (GAP if n < len(texts) - 1 else 0), HEADER_H)
        y += HEADER_H + GAP
    for start in range(0, len(cards), 6):
        row = cards[start:start + 6]
        width = (inner - (len(row) - 1) * GAP) / len(row)
        for n, i in enumerate(row):
            positions[i] = (MARGIN + n * (width + GAP), y, width, CARD_H)
        y += CARD_H + GAP
    bottom = PAGE_H - MARGIN

    def stack(items: list[int], x: float, width: float, top: float) -> None:
        if not items:
            return
        height = max(120.0, (bottom - top - (len(items) - 1) * GAP) / len(items))
        for n, i in enumerate(items):
            positions[i] = (x, top + n * (height + GAP), width, height)

    def grid(items: list[int], x: float, width: float, top: float) -> None:
        if not items:
            return
        rows = ([items[:1]] if len(items) % 2 else []) + [items[k:k + 2] for k in range(len(items) % 2, len(items), 2)]
        height = max(120.0, (bottom - top - (len(rows) - 1) * GAP) / len(rows))
        for r, row in enumerate(rows):
            w = (width - (len(row) - 1) * GAP) / len(row)
            for n, i in enumerate(row):
                positions[i] = (x + n * (w + GAP), top + r * (height + GAP), w, height)

    if tables and charts:
        table_w = round(inner * 0.42, 1)
        chart_w = inner - table_w - GAP
        (stack if len(charts) <= 3 else grid)(charts, MARGIN, chart_w, y)
        stack(tables, MARGIN + chart_w + GAP, table_w, y)
    elif tables:
        stack(tables, MARGIN, inner, y)
    else:
        grid(charts, MARGIN, inner, y)
    return [tuple(round(v, 1) for v in positions[i]) for i in range(len(visual_types))]  # type: ignore[misc]


# --------------------------------------------------------------------------- calcul pandas (contrôle DAX)
def model_frame(bundle: Any) -> pd.DataFrame:
    """Table de faits sous les noms du modèle, dates converties, + colonnes de la table de dates."""
    frame = bundle.data.copy()
    frame.columns = [model_name(c) for c in frame.columns]
    for column in frame.columns:
        if _is_date(frame[column], column):
            frame[column] = pd.to_datetime(frame[column], errors="coerce")
    date = date_column(bundle)
    if date and date in frame.columns:
        d = frame[date]
        frame[f"{DATE_TABLE}.Year"] = d.dt.year.astype("Int64").astype(str)
        frame[f"{DATE_TABLE}.Month"] = d.dt.strftime("%Y-%m")
        frame[f"{DATE_TABLE}.Quarter"] = d.dt.year.astype("Int64").astype(str) + " T" + d.dt.quarter.astype("Int64").astype(str)
    return frame


def _split_args(text: str) -> list[str]:
    args, depth, current = [], 0, ""
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            args.append(current.strip())
            current = ""
        else:
            current += ch
    if current.strip():
        args.append(current.strip())
    return args


def _call(text: str) -> tuple[str, list[str]] | None:
    m = re.fullmatch(r"([A-Z]+)\s*\((.*)\)", text, re.IGNORECASE | re.DOTALL)
    return (m.group(1).upper(), _split_args(m.group(2))) if m else None


def _ref(text: str) -> str | None:
    m = re.fullmatch(r"'?[A-Za-z_][A-Za-z0-9_]*'?\[([^\]]+)\]", text.strip())
    return m.group(1) if m else None


def evaluate(expression: str, frame: pd.DataFrame, full: pd.DataFrame, measures: dict[str, str], group: str | None = None) -> float | None:
    """Valeur d'une mesure DAX (motifs du catalogue de l'Agent 4) sur un sous-ensemble de lignes.

    ``frame`` : lignes du contexte (une catégorie) ; ``full`` : toutes les lignes ; ``group`` : la colonne
    qui découpe ``frame`` (ALL(table[group]) la retire). Renvoie None quand le motif n'est pas connu."""
    text = " ".join(expression.split())
    measure = re.fullmatch(r"\[([^\]]+)\]", text)
    if measure:
        inner = measures.get(measure.group(1))
        return evaluate(inner, frame, full, measures, group) if inner else None
    times = re.fullmatch(r"(.+)\*\s*([0-9.]+)", text)
    if times and not text.startswith("VAR"):
        base = evaluate(times.group(1).strip(), frame, full, measures, group)
        return None if base is None else base * float(times.group(2))
    if text.upper().startswith("VAR "):
        return _on_time(text, frame)
    call = _call(text)
    if not call:
        return None
    func, args = call
    if func in {"SUM", "AVERAGE", "MIN", "MAX", "COUNT", "DISTINCTCOUNT"} and len(args) == 1 and _ref(args[0]):
        column = _ref(args[0])
        if column not in frame.columns:
            return None
        series = frame[column]
        if func == "DISTINCTCOUNT":
            return float(series.nunique(dropna=False))
        if func == "COUNT":
            return float(series.count())
        if series.dropna().empty:
            return None
        return float({"SUM": series.sum, "AVERAGE": series.mean, "MIN": series.min, "MAX": series.max}[func]())
    if func == "COUNTROWS" and len(args) == 1:
        return float(len(frame))
    if func == "DIVIDE" and len(args) >= 2:
        top = evaluate(args[0], frame, full, measures, group)
        bottom = evaluate(args[1], frame, full, measures, group)
        return None if top is None or not bottom else top / bottom
    if func == "CALCULATE" and len(args) == 2:
        removed = _call(args[1])
        if removed and removed[0] == "ALL" and _ref(removed[1][0]) == group:
            return evaluate(args[0], full, full, measures, None)
        return evaluate(args[0], frame, full, measures, group)
    if func == "AVERAGEX" and len(args) == 2:
        return _lead_time(args, frame)
    return None


def _lead_time(args: list[str], frame: pd.DataFrame) -> float | None:
    key = _ref(_call(args[0])[1][0]) if _call(args[0]) else None
    cols = re.findall(r"(MIN|MAX)\s*\(\s*'?[A-Za-z_][A-Za-z0-9_]*'?\[([^\]]+)\]\s*\)", args[1], re.IGNORECASE)
    if not key or len(cols) != 2 or "DATEDIFF" not in args[1].upper():
        return None
    (_, start), (_, end) = cols
    per = frame.groupby(key).agg(start=(start, "min"), end=(end, "max"))
    days = (pd.to_datetime(per["end"]) - pd.to_datetime(per["start"])).dt.days.dropna()
    return float(days.mean()) if len(days) else None


def _on_time(text: str, frame: pd.DataFrame) -> float | None:
    key = re.search(r"VALUES\s*\(\s*'?[A-Za-z_][A-Za-z0-9_]*'?\[([^\]]+)\]", text)
    pair = re.search(r"MAX\s*\(\s*'?\w+'?\[([^\]]+)\]\s*\)\s*\)\s*<=\s*CALCULATE\s*\(\s*MAX\s*\(\s*'?\w+'?\[([^\]]+)\]", text)
    if not key or not pair:
        return None
    shipped, required = pair.groups()
    per = frame.groupby(key.group(1)).agg(s=(shipped, "max"), r=(required, "max")).dropna(subset=["s"])
    if per.empty:
        return None
    rate = float((pd.to_datetime(per["s"]) <= pd.to_datetime(per["r"])).mean())
    return rate * 100 if re.search(r"\*\s*100\s*$", text) else rate


def measure_value(spec: dict[str, str], frame: pd.DataFrame, full: pd.DataFrame, measures: dict[str, str], group: str | None = None) -> float | None:
    if spec["kind"] == "Measure":
        expression = measures.get(spec["name"])
        return evaluate(expression, frame, full, measures, group) if expression else None
    column = spec["name"]
    return float(frame[column].sum()) if column in frame.columns else None


def fmt(value: float | None, pattern: str = "#,0.00") -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    if pattern == "0.0%":
        return f"{value * 100:,.1f} %".replace(",", " ")
    if pattern.endswith('" j"'):
        return f"{value:,.1f} j".replace(",", " ")
    if pattern.endswith('" %"'):
        return f"{value:,.1f} %".replace(",", " ")
    if pattern == "#,0":
        return f"{value:,.0f}".replace(",", " ")
    return f"{value:,.2f}".replace(",", " ")
