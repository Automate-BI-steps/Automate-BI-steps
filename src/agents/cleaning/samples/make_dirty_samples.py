"""Fabrique un jeu de test volontairement sale à partir des CSV Northwind (data/raw).

Problèmes injectés : séparateur ';', encodage Windows, espaces, marqueurs 'N/A', pays écrits
de plusieurs façons, doublons, lignes vides, dates en formats mixtes, montants '32,38 €',
accents mal encodés, booléens oui/non, quantité négative, noms en MAJUSCULES, clés de jointure en
minuscules, remises écrites en pourcentage (15 % au lieu de 0,15), en-tête de colonne avec espaces et symbole.
"""
from pathlib import Path
import random
import pandas as pd

random.seed(7)
RAW = Path(__file__).resolve().parents[2] / "data" / "raw"
OUT = Path(__file__).resolve().parent

c = pd.read_csv(RAW / "customers.csv", encoding="latin-1", dtype=str)
c.loc[::7, "companyName"] = "  " + c.loc[::7, "companyName"] + "   "
c.loc[3, "contactTitle"] = "N/A"
c.loc[10, "city"] = "null"
c.loc[c.country == "USA", "country"] = ["United States", "usa", "U.S.A.", "USA"] * 3 + ["USA"]
c.loc[c.country == "Germany", "country"] = ["Deutschland", "germany", "Germany"] * 3 + ["Germany", "GERMANY"]
c.loc[c.country == "France", "country"] = ["france", "FRANCE", "France"] * 3 + ["France", "France"]
c.loc[5, "contactName"] = "Christina BerglÃ¼nd"
c.loc[[8, 15], "contactName"] = c.loc[[8, 15], "contactName"].str.upper()
c = pd.concat([c, c.iloc[[1, 2]], pd.DataFrame([{k: "" for k in c.columns}])], ignore_index=True)
c.to_csv(OUT / "clients_sale.csv", sep=";", index=False, encoding="cp1252")

o = pd.read_csv(RAW / "orders.csv", dtype=str).head(120)
for i in range(0, 120, 4):
    y, m, d = o.loc[i, "orderDate"].split("-")
    o.loc[i, "orderDate"] = f"{d}/{m}/{y}"
o["freight"] = o["freight"].str.replace(".", ",", regex=False) + " €"
o.loc[[2, 9], "shippedDate"] = ["N/A", "-"]
o.loc[50, "freight"] = "inconnu"
o.loc[[6, 33, 71], "customerID"] = o.loc[[6, 33, 71], "customerID"].str.lower()
od = pd.read_csv(RAW / "order_details.csv", dtype=str)
od = od[od.orderID.isin(o.orderID)].reset_index(drop=True)
od.loc[4, "quantity"] = "-5"
od.loc[7, "unitPrice"] = " 14,00 "
od.loc[od.discount != "0", "discount"] = [f"{round(float(x) * 100)} %" if i % 3 == 0 else x
                                          for i, x in enumerate(od.loc[od.discount != "0", "discount"])]
od = od.rename(columns={"unitPrice": "Prix unitaire (€)"})
od = pd.concat([od, od.iloc[[0, 1]]], ignore_index=True)
p = pd.read_csv(RAW / "products.csv", encoding="latin-1", dtype=str)
p["discontinued"] = p["discontinued"].map({"0": random.choice(["non", "Non", "no"]), "1": "oui"})
p.loc[20, "unitPrice"] = "9999"
with pd.ExcelWriter(OUT / "ventes_sale.xlsx") as w:
    o.to_excel(w, sheet_name="commandes", index=False)
    od.to_excel(w, sheet_name="lignes", index=False)
    p.to_excel(w, sheet_name="produits", index=False)
print("clients_sale.csv et ventes_sale.xlsx créés dans", OUT)
