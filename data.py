"""ÉTAPE 1 — Fabriquer la table de faits (fact_trade) à partir des fichiers CSV.

Dans le projet complet, cette table est préparée par l'agent ETL (agent 3).
Ici (mode autonome), on la fabrique nous-mêmes en joignant les 7 tables.
Une ligne de fact_trade = un produit dans une commande.
"""
import re
from pathlib import Path

import pandas as pd


# D'où vient chaque colonne de fact_trade (pour tracer un KPI jusqu'à sa source).
ORIGINE = {
    "orderID": "order_details", "unitPrice": "order_details", "quantity": "order_details",
    "discount": "order_details", "lineTotal": "calculé : unitPrice × quantity × (1 − discount)",
    "orderDate": "orders", "requiredDate": "orders", "shippedDate": "orders", "freight": "orders",
    "companyName": "customers", "country": "customers", "employeeName": "employees",
    "shipperName": "shippers", "productName": "products", "categoryName": "categories",
}


def lire_csv(chemin: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(chemin, encoding="utf-8")
    except UnicodeDecodeError:                      # anciens exports avec accents
        return pd.read_csv(chemin, encoding="latin-1")


def nom_table(chemin: Path) -> str:
    """'orders.csv' ou 'a1b2c3d4-orders.csv' -> 'orders'."""
    return re.sub(r"^[0-9a-f]{8}-", "", chemin.stem.lower())


def construire_fact_trade(t: dict) -> pd.DataFrame:
    """Jointure : on part des lignes de commande et on ajoute les informations des autres tables."""
    f = t["order_details"].merge(t["orders"], on="orderID", how="left")          # + dates, fret, client
    f = f.merge(t["products"][["productID", "productName", "categoryID"]], on="productID", how="left")
    f = f.merge(t["categories"][["categoryID", "categoryName"]], on="categoryID", how="left")
    f = f.merge(t["customers"][["customerID", "companyName", "country"]], on="customerID", how="left")
    f = f.merge(t["employees"][["employeeID", "employeeName"]], on="employeeID", how="left")
    f = f.merge(t["shippers"].rename(columns={"companyName": "shipperName"}), on="shipperID", how="left")
    for col in ("orderDate", "requiredDate", "shippedDate"):
        f[col] = pd.to_datetime(f[col], errors="coerce")
    f["lineTotal"] = f["unitPrice"] * f["quantity"] * (1 - f["discount"])        # montant de la ligne
    colonnes = ["orderID", "orderDate", "requiredDate", "shippedDate", "companyName", "country",
                "employeeName", "shipperName", "productName", "categoryName",
                "unitPrice", "quantity", "discount", "lineTotal", "freight"]
    return f[colonnes]


def load_fact_table(data_path: str):
    """Retourne (fact_trade, infos pour le journal d'audit)."""
    p = Path(data_path)
    fichiers = [p] if p.is_file() else sorted(p.glob("*.csv"))
    if not fichiers:
        raise FileNotFoundError(f"Aucun fichier CSV dans {p}")
    tables = {nom_table(f): lire_csv(f) for f in fichiers}
    if {"orders", "order_details", "products", "categories",
            "customers", "employees", "shippers"} <= tables.keys():
        info = {"methode": "jointure des 7 tables", "tables": {n: len(d) for n, d in tables.items()},
                "origine": ORIGINE}
        return construire_fact_trade(tables), info
    nom, df = max(tables.items(), key=lambda kv: len(kv[1]))   # sinon : la plus grande table
    return df, {"methode": f"table unique : {nom}"}
