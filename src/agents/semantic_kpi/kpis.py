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
    for role in ("order_id", "country", "product", "customer", "category", "employee", "shipper"):
        if m.get(role):
            d[role] = df[m[role]]
    for role in ("quantity", "unit_price", "discount", "freight"):
        if m.get(role):
            d[role] = pd.to_numeric(df[m[role]], errors="coerce")
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


# ---- clients, volumes, remises, transport (rôles complémentaires) ----
def customer_count(d):
    return int(d["customer"].nunique())


def product_count(d):
    return int(d["product"].nunique())


def units_sold(d):
    return round(float(d["quantity"].sum()), 2)


def gross_revenue(d):
    """CA avant remise : prix unitaire x quantité."""
    return round(float((d["unit_price"] * d["quantity"]).sum()), 2)


def discount_amount(d):
    return round(gross_revenue(d) - total_revenue(d), 2)


def discount_rate(d):
    g = gross_revenue(d)
    return round(discount_amount(d) / g * 100, 2) if g else None


def total_freight(d):
    """Le fret est un montant PAR COMMANDE répété sur chaque ligne : compté une fois par commande."""
    return round(float(d.drop_duplicates("order_id")["freight"].sum()), 2)


def avg_freight(d):
    n = order_count(d)
    return round(total_freight(d) / n, 2) if n else None


def freight_ratio(d):
    ca = total_revenue(d)
    return round(total_freight(d) / ca * 100, 2) if ca else None


def _orders(d):
    return d.drop_duplicates("order_id")


def shipped_orders(d):
    return int(_orders(d)["shipped_date"].notna().sum())


def unshipped_orders(d):
    return int(_orders(d)["shipped_date"].isna().sum())


def late_orders(d):
    o = _orders(d)
    return int((o["shipped_date"] > o["required_date"]).sum())


def revenue_per_customer(d):
    n = customer_count(d)
    return round(total_revenue(d) / n, 2) if n else None


def units_per_order(d):
    n = order_count(d)
    return round(units_sold(d) / n, 2) if n else None


def best_employee(d):
    g = d.groupby("employee")["value"].sum()
    return str(g.idxmax()) if len(g) else None


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
    # ---- clients et volumes ----
    KPI("customer_count", "Active Customers", "Nombre de clients ayant commandé", ("customer",), "clients",
        "Nombre de clients différents", "DISTINCTCOUNT({customer})", customer_count),
    KPI("revenue_per_customer", "Revenue per Customer", "Chiffre d'affaires moyen par client", ("value", "customer"),
        "valeur", "Chiffre d'affaires / nombre de clients", "DIVIDE([Total Revenue], [Active Customers])", revenue_per_customer),
    KPI("product_count", "Products Sold", "Nombre de produits différents vendus", ("product",), "produits",
        "Nombre de produits différents", "DISTINCTCOUNT({product})", product_count),
    KPI("units_sold", "Units Sold", "Quantité totale vendue", ("quantity",), "unités",
        "Somme des quantités", "SUM({quantity})", units_sold),
    KPI("units_per_order", "Units per Order", "Quantité moyenne par commande", ("quantity", "order_id"), "unités",
        "Quantité totale / nombre de commandes", "DIVIDE([Units Sold], [Order Count])", units_per_order),
    KPI("top_customers", "Top Customers", "5 premiers clients et leur part du CA", ("value", "customer"),
        "valeur / %", "Somme par client, du plus grand au plus petit", PART % "customer", top5("customer")),
    KPI("top_categories", "Top Categories", "5 premières catégories et leur part du CA", ("value", "category"),
        "valeur / %", "Somme par catégorie, du plus grand au plus petit", PART % "category", top5("category")),
    KPI("top_employees", "Top Employees", "5 premiers employés et leur part du CA", ("value", "employee"),
        "valeur / %", "Somme par employé, du plus grand au plus petit", PART % "employee", top5("employee")),
    KPI("best_employee", "Best Employee", "Employé qui réalise le plus grand chiffre d'affaires", ("value", "employee"),
        "texte", "Employé dont la somme des montants est la plus grande",
        "MAXX(TOPN(1, VALUES({employee}), [Total Revenue]), {employee})", best_employee),
    # ---- finance : remises ----
    KPI("gross_revenue", "Gross Revenue", "Chiffre d'affaires avant remise", ("unit_price", "quantity", "value"), "valeur",
        "Somme de (prix unitaire x quantité)", "SUMX({table}, {unit_price} * {quantity})", gross_revenue),
    KPI("discount_amount", "Discount Amount", "Montant total des remises accordées", ("unit_price", "quantity", "value"),
        "valeur", "CA avant remise - CA", "[Gross Revenue] - [Total Revenue]", discount_amount),
    KPI("discount_rate", "Discount Rate", "Part du CA brut cédée en remises", ("unit_price", "quantity", "value"), "%",
        "Remises / CA avant remise x 100", "DIVIDE([Discount Amount], [Gross Revenue]) * 100", discount_rate),
    # ---- logistique : transport et livraisons ----
    KPI("total_freight", "Total Freight", "Frais de port totaux (comptés une fois par commande)", ("freight", "order_id"),
        "valeur", "Somme du fret de chaque commande", "SUMX(VALUES({order_id}), CALCULATE(MAX({freight})))", total_freight),
    KPI("avg_freight", "Average Freight per Order", "Frais de port moyens par commande", ("freight", "order_id"), "valeur",
        "Fret total / nombre de commandes", "DIVIDE([Total Freight], [Order Count])", avg_freight),
    KPI("freight_ratio", "Freight to Revenue", "Poids du transport dans le chiffre d'affaires", ("freight", "order_id", "value"),
        "%", "Fret total / CA x 100", "DIVIDE([Total Freight], [Total Revenue]) * 100", freight_ratio),
    KPI("shipped_orders", "Shipped Orders", "Commandes expédiées", ("shipped_date", "order_id"), "commandes",
        "Commandes avec une date d'expédition",
        "COUNTROWS(FILTER(VALUES({order_id}), NOT ISBLANK(CALCULATE(MAX({shipped_date})))))", shipped_orders),
    KPI("unshipped_orders", "Unshipped Orders", "Commandes pas encore expédiées", ("shipped_date", "order_id"), "commandes",
        "Commandes sans date d'expédition",
        "COUNTROWS(FILTER(VALUES({order_id}), ISBLANK(CALCULATE(MAX({shipped_date})))))", unshipped_orders),
    KPI("late_orders", "Late Orders", "Commandes expédiées après la date exigée", ("shipped_date", "required_date", "order_id"),
        "commandes", "Commandes dont la date d'expédition dépasse la date exigée",
        "COUNTROWS(FILTER(VALUES({order_id}), NOT ISBLANK(CALCULATE(MAX({shipped_date}))) && "
        "CALCULATE(MAX({shipped_date})) > CALCULATE(MAX({required_date}))))", late_orders),
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
    "customer_count": "Direction commerciale : taille de la base clients active (BO3).",
    "revenue_per_customer": "Direction commerciale : valeur moyenne d'un client (BO3).",
    "product_count": "Direction commerciale : largeur de l'offre réellement vendue (BO3).",
    "units_sold": "Volume physique vendu, indépendant des prix (BO3).",
    "units_per_order": "Taille moyenne d'une commande en unités (BO3).",
    "top_customers": "Direction commerciale : dépendance aux plus gros clients (BO3).",
    "top_categories": "Direction commerciale : catégories qui portent le chiffre d'affaires (BO3).",
    "top_employees": "Administration : contribution de chaque vendeur (BO3).",
    "best_employee": "Administration : vendeur qui réalise le plus de chiffre d'affaires (BO3).",
    "gross_revenue": "Direction financière : chiffre d'affaires avant remises (BO3).",
    "discount_amount": "Direction financière : coût des remises accordées (BO3).",
    "discount_rate": "Direction financière : politique de remise rapportée au chiffre d'affaires (BO3).",
    "total_freight": "Direction logistique : coût total du transport (BO3).",
    "avg_freight": "Direction logistique : coût moyen d'expédition d'une commande (BO3).",
    "freight_ratio": "Direction logistique et financière : poids du transport dans le chiffre d'affaires (BO3).",
    "shipped_orders": "Direction logistique : commandes livrées (expédiées) (BO3).",
    "unshipped_orders": "Direction logistique : commandes en attente d'expédition (BO3).",
    "late_orders": "Direction logistique : commandes expédiées en retard (BO3).",
}
for _k in CATALOG:
    _k.pourquoi = POURQUOI[_k.id]
