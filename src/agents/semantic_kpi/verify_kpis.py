"""KPI accuracy (chapitre « Test » du rapport) : l'agent contre une référence indépendante.

Deux comparaisons possibles :
  1. un RECALCUL INDÉPENDANT en pandas brut, sans utiliser le code de l'agent (automatique) ;
  2. un RAPPORT POWER BI MANUEL : remplis la colonne valeur_power_bi de reference_powerbi.csv.

    python verify_kpis.py "C:\\chemin\\vers\\data"
"""
import json
import os
import sys
from pathlib import Path

import pandas as pd


def lire(chemin):
    """Lecture simple d'un CSV (certains fichiers ont des accents en latin-1)."""
    try:
        return pd.read_csv(chemin)
    except UnicodeDecodeError:
        return pd.read_csv(chemin, encoding="latin-1")


def recalcul_independant(dossier) -> dict:
    """Recalcule les KPI directement depuis les CSV, avec du pandas brut (pas de code de l'agent)."""
    p = Path(dossier)
    try:
        od, o, c, pr = (lire(p / f"{n}.csv") for n in ("order_details", "orders", "customers", "products"))
    except FileNotFoundError:
        return {}
    for col in ("orderDate", "requiredDate", "shippedDate"):
        o[col] = pd.to_datetime(o[col], errors="coerce")
    f = (od.merge(o, on="orderID").merge(c[["customerID", "country"]], on="customerID")
           .merge(pr[["productID", "productName"]], on="productID"))
    f["v"] = f["unitPrice"] * f["quantity"] * (1 - f["discount"])
    ref = {"total_revenue": f["v"].sum(), "order_count": o["orderID"].nunique()}
    ref["avg_order_value"] = ref["total_revenue"] / ref["order_count"]
    ref["avg_lead_time"] = (o["shippedDate"] - o["orderDate"]).dt.days.mean()
    exp = o.dropna(subset=["shippedDate"])
    ref["on_time_rate"] = (exp["shippedDate"] <= exp["requiredDate"]).mean() * 100
    pays = f.groupby("country")["v"].sum().sort_values(ascending=False)
    ref["top_countries (part du n°1, %)"] = pays.iloc[0] / pays.sum() * 100
    prod = f.groupby("productName")["v"].sum().sort_values(ascending=False)
    ref["top_products (part du n°1, %)"] = prod.iloc[0] / prod.sum() * 100
    par_mois = f.groupby([f["orderDate"].dt.year, f["orderDate"].dt.month])["v"].sum()
    annees = sorted(par_mois.index.get_level_values(0).unique())
    for a in annees[1:]:
        communs = par_mois.loc[a].index.intersection(par_mois.loc[a - 1].index)
        ref[f"yoy_growth {a} (%)"] = (par_mois.loc[a][communs].sum() / par_mois.loc[a - 1][communs].sum() - 1) * 100
    return ref


def valeur_agent(res: dict, cle: str):
    """Retrouve la valeur de l'agent qui correspond à une clé de référence."""
    if cle.startswith("top_countries"):
        return res["top_countries"]["valeur"][0]["part_pct"]
    if cle.startswith("top_products"):
        return res["top_products"]["valeur"][0]["part_pct"]
    if cle.startswith("yoy_growth"):
        return res["yoy_growth"]["valeur"]["croissance_pct"].get(int(cle.split()[1]))
    return res[cle]["valeur"]


def comparer(kpi, agent, reference, source) -> dict:
    ecart = abs(agent - reference)
    ok = ecart <= max(0.01, 1e-6 * abs(reference))
    return {"kpi": kpi, "agent": round(float(agent), 4), "reference": round(float(reference), 4),
            "source": source, "ecart": round(float(ecart), 4), "statut": "identique" if ok else "écart"}


def verifier(resultats: dict, dossier, reference_powerbi=None) -> list:
    """Retourne la liste des comparaisons agent / référence."""
    lignes = []
    for cle, ref in recalcul_independant(dossier).items():
        try:
            lignes.append(comparer(cle.split(" (")[0] if "(" in cle else cle, valeur_agent(resultats, cle),
                                   ref, "recalcul pandas brut"))
        except Exception:
            lignes.append({"kpi": cle, "agent": None, "reference": round(float(ref), 4), "source": "recalcul pandas brut",
                           "ecart": None, "statut": "écart"})
    if reference_powerbi and Path(reference_powerbi).exists():
        pb = pd.read_csv(reference_powerbi).dropna(subset=["valeur_power_bi"])
        for _, r in pb.iterrows():
            if r["kpi_id"] in resultats and isinstance(resultats[r["kpi_id"]]["valeur"], (int, float)):
                lignes.append(comparer(r["kpi_id"], resultats[r["kpi_id"]]["valeur"],
                                       float(r["valeur_power_bi"]), "Power BI manuel"))
    return lignes


if __name__ == "__main__":
    from agent import run
    chemin = sys.argv[1] if len(sys.argv) > 1 else "data"
    etat = run(chemin)
    lignes = verifier(etat.get("kpi_results", {}), chemin, "reference_powerbi.csv")
    if not lignes:
        sys.exit("Vérification indépendante disponible pour le dossier des 7 CSV de commandes.")
    for x in lignes:
        print(f"[{x['statut']:<9}] {x['kpi']:<18} agent={x['agent']}  référence={x['reference']}  ({x['source']})")
    ok = sum(x["statut"] == "identique" for x in lignes)
    print(f"\n{ok}/{len(lignes)} valeurs identiques à la référence")
    dossier = Path(os.getenv("OUTPUT_DIR", "outputs"))
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "kpi_accuracy.json").write_text(json.dumps(lignes, ensure_ascii=False, indent=2), "utf-8")
