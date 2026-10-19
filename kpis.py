"""ÉTAPES 3 et 4 — Le catalogue de KPI (« KPI catalog »).

Chaque KPI = un nom, une définition métier, une formule lisible, les rôles dont il a besoin,
une mesure DAX (pour Power BI) et un calcul pandas. Le calcul ne dépend jamais d'un LLM.
Un KPI n'est calculé que si les colonnes dont il a besoin existent dans le dataset.
"""
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd


def standardise(df: pd.DataFrame, m: dict) -> pd.DataFrame:
    """Recopie les colonnes utiles sous des noms standard (value, date, country...)."""
    d = pd.DataFrame(index=df.index)
    d["value"] = pd.to_numeric(df[m["value"]], errors="coerce")
    for role in ("order_id", "country", "product"):
        if m.get(role):
            d[role] = df[m[role]]
    for role in ("date", "shipped_date", "required_date"):
        if m.get(role):
            s = df[m[role]]
            if pd.api.types.is_numeric_dtype(s) and s.dropna().between(1900, 2100).all():
                s = s.astype("Int64").astype(str) + "-01-01"          # colonne = année seule
            d[role] = pd.to_datetime(s, errors="coerce")
    if m.get("flow"):
        f = df[m["flow"]].astype(str).str.lower().str.strip()
        d["flow"] = np.select([f.str.startswith("exp"), f.str.startswith("imp")],
                              ["export", "import"], default="autre")
    return d


# ---------------------------------------------------------------- calculs
def total_revenue(d):
    return round(float(d["value"].sum()), 2)


def order_count(d):
    return int(d["order_id"].nunique())


def avg_order_value(d):
    n = order_count(d)
    return round(total_revenue(d) / n, 2) if n else None


def yoy_growth(d):
    """Croissance du CA d'une année sur l'autre, calculée sur les MÊMES mois
    (comparer une année complète à une année partielle donnerait un résultat faux)."""
    d = d.dropna(subset=["date"])
    par_mois = d.groupby([d["date"].dt.year, d["date"].dt.month])["value"].sum()
    annees = sorted(par_mois.index.get_level_values(0).unique())
    res = {"ca_par_annee": {int(a): round(float(par_mois.loc[a].sum()), 2) for a in annees},
           "croissance_pct": {}}
    res["mois_couverts"] = {int(a): int(par_mois.loc[a].index.nunique()) for a in annees}
    for a in annees[1:]:
        if a - 1 not in annees:
            continue
        communs = par_mois.loc[a].index.intersection(par_mois.loc[a - 1].index)
        avant = par_mois.loc[a - 1][communs].sum()
        if len(communs) and avant:
            res["croissance_pct"][int(a)] = round((par_mois.loc[a][communs].sum() / avant - 1) * 100, 2)
    return res


def top5(colonne):
    def calcul(d):
        g = d.groupby(colonne)["value"].sum().sort_values(ascending=False)
        return [{"nom": str(n), "valeur": round(float(v), 2), "part_pct": round(float(v / g.sum() * 100), 2)}
                for n, v in g.head(5).items()]
    return calcul


def lead_time(d):
    """Délai moyen commande -> expédition. Une seule ligne par commande (les dates sont répétées)."""
    o = d.drop_duplicates("order_id")
    return round(float((o["shipped_date"] - o["date"]).dt.days.mean()), 2)


def on_time_rate(d):
    o = d.drop_duplicates("order_id").dropna(subset=["shipped_date", "required_date"])
    return round(float((o["shipped_date"] <= o["required_date"]).mean() * 100), 2) if len(o) else None


def _exports_imports(d):
    return d.loc[d["flow"] == "export", "value"].sum(), d.loc[d["flow"] == "import", "value"].sum()


def trade_balance(d):
    exp, imp = _exports_imports(d)
    return round(float(exp - imp), 2)


def coverage_ratio(d):
    exp, imp = _exports_imports(d)
    return round(float(exp / imp * 100), 2) if imp else None


def total_exports(d):
    return round(float(_exports_imports(d)[0]), 2)


def total_imports(d):
    return round(float(_exports_imports(d)[1]), 2)


# ---------------------------------------------------------------- catalogue
@dataclass
class KPI:
    id: str
    nom: str
    definition: str
    requires: tuple          # rôles nécessaires
    unite: str
    formule: str
    dax: str                 # {value}, {country}... sont remplacés par Table[Colonne]
    compute: Callable
    bo: str = "BO3"          # objectif métier servi
    pourquoi: str = ""       # pourquoi ce KPI existe (réponse à « Why this KPI ? »)


PART = "DIVIDE(SUM({value}), CALCULATE(SUM({value}), ALL({%s})))"
EXP, IMP = 'CALCULATE(SUM({value}), {flow} = "Export")', 'CALCULATE(SUM({value}), {flow} = "Import")'

CATALOG = [
    KPI("total_revenue", "Total Revenue", "Chiffre d'affaires total", ("value",), "valeur",
        "Somme des montants", "SUM({value})", total_revenue),
    KPI("order_count", "Order Count", "Nombre de commandes distinctes", ("order_id",), "commandes",
        "Nombre de commandes différentes", "DISTINCTCOUNT({order_id})", order_count),
    KPI("avg_order_value", "Average Order Value", "Panier moyen par commande", ("value", "order_id"),
        "valeur", "Chiffre d'affaires / nombre de commandes",
        "DIVIDE([Total Revenue], [Order Count])", avg_order_value),
    KPI("yoy_growth", "YoY Growth", "Croissance annuelle du CA (mêmes mois)", ("value", "date"), "%",
        "(CA année N - CA année N-1) / CA année N-1, sur les mêmes mois",
        "VAR cur = SUM({value}) "
        "VAR prev = CALCULATE(SUM({value}), SAMEPERIODLASTYEAR(Dim_Date[Date])) "
        "RETURN DIVIDE(cur - prev, prev)", yoy_growth),
    KPI("top_countries", "Top Countries", "5 premiers pays et leur part du CA", ("value", "country"),
        "valeur / %", "Somme par pays, du plus grand au plus petit", PART % "country", top5("country")),
    KPI("top_products", "Top Products", "5 premiers produits et leur part du CA", ("value", "product"),
        "valeur / %", "Somme par produit, du plus grand au plus petit", PART % "product", top5("product")),
    KPI("avg_lead_time", "Average Shipping Lead Time", "Délai moyen entre commande et expédition",
        ("date", "shipped_date", "order_id"), "jours", "Moyenne (date d'expédition - date de commande)",
        "AVERAGEX(VALUES({order_id}), DATEDIFF(CALCULATE(MIN({date})), CALCULATE(MAX({shipped_date})), DAY))",
        lead_time),
    KPI("on_time_rate", "On-Time Shipping Rate", "Part des commandes expédiées à temps",
        ("shipped_date", "required_date", "order_id"), "%",
        "Commandes expédiées avant la date exigée / commandes expédiées",
        "VAR expediees = FILTER(VALUES({order_id}), NOT ISBLANK(CALCULATE(MAX({shipped_date})))) "
        "RETURN DIVIDE(COUNTROWS(FILTER(expediees, CALCULATE(MAX({shipped_date})) <= "
        "CALCULATE(MAX({required_date})))), COUNTROWS(expediees)) * 100", on_time_rate),
    KPI("total_exports", "Total Exports", "Valeur totale des exportations", ("value", "flow"), "valeur",
        "Somme des montants dont le flux est export", EXP, total_exports),
    KPI("total_imports", "Total Imports", "Valeur totale des importations", ("value", "flow"), "valeur",
        "Somme des montants dont le flux est import", IMP, total_imports),
    KPI("trade_balance", "Trade Balance", "Solde commercial (exports - imports)", ("value", "flow"), "valeur",
        "Total des exports - total des imports", f"{EXP} - {IMP}", trade_balance),
    KPI("coverage_ratio", "Coverage Ratio", "Taux de couverture des imports par les exports",
        ("value", "flow"), "%", "Exports / imports x 100", f"DIVIDE({EXP}, {IMP}) * 100", coverage_ratio),
]


# Pourquoi chaque KPI existe : réponse à la question « Why this KPI ? » de l'agent XAI.
POURQUOI = {
    "total_revenue": "Mesure de base de la performance commerciale (BO3).",
    "order_count": "Mesure le volume d'activité (BO3).",
    "avg_order_value": "Montre la valeur moyenne d'une vente (BO3).",
    "yoy_growth": "KPI cité dans le rapport : montre l'évolution de la performance dans le temps (BO3).",
    "top_countries": "Besoin de Leila (KPI pays fiables) : révèle les marchés clés et la dépendance géographique (BO3).",
    "top_products": "Besoin de Leila (KPI produits fiables) : révèle les produits qui font le chiffre d'affaires (BO3).",
    "avg_lead_time": "Performance logistique (BO3) : rapidité de traitement des commandes.",
    "on_time_rate": "Performance logistique (BO3) : respect de la date de livraison exigée.",
    "total_exports": "Brique du solde commercial : rend le calcul du solde vérifiable (BO3).",
    "total_imports": "Brique du solde commercial : rend le calcul du solde vérifiable (BO3).",
    "trade_balance": "KPI cité dans le rapport : solde des échanges, exports moins imports (BO3).",
    "coverage_ratio": "KPI cité dans le rapport : part des imports financée par les exports (BO3).",
}
for _k in CATALOG:
    _k.pourquoi = POURQUOI[_k.id]
