"""Tests de l'agent de nettoyage : python -m pytest tests  (ou python tests/test_cleaning.py)."""
import json
import sys
import tempfile
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from agent import run  # noqa: E402
import tools  # noqa: E402

SAMPLES = HERE / "samples"


def _run(tmp):
    return run([SAMPLES / "clients_sale.csv", SAMPLES / "ventes_sale.xlsx"], Path(tmp) / "clean", Path(tmp) / "hist")


def test_nettoyage_et_trace():
    with tempfile.TemporaryDirectory() as tmp:
        s = _run(tmp)
        clients = pd.read_csv(Path(tmp) / "clean" / "clients_sale.csv")
        cmd = pd.read_csv(Path(tmp) / "clean" / "ventes_sale_commandes.csv")
        prod = pd.read_csv(Path(tmp) / "clean" / "ventes_sale_produits.csv")
        assert len(clients) == 91                                   # 2 doublons + 1 ligne vide supprimés
        assert set(clients.country.dropna()) >= {"USA", "Germany", "France"}
        assert not clients.country.isin(["usa", "United States", "Deutschland", "FRANCE"]).any()
        assert (clients.companyName.dropna() == clients.companyName.dropna().str.strip()).all()
        assert "Christina Berglünd" in set(clients.contactName)    # accents réparés
        assert cmd.freight.dtype == float and cmd.freight.iloc[0] == 32.38
        assert pd.to_datetime(cmd.orderDate, format="%Y-%m-%d").notna().all()
        assert prod.discontinued.dtype == bool
        used = {x["tool"] for x in s["steps"]}
        assert {"drop_duplicates", "normalize_country", "cast_numeric", "parse_dates", "fix_encoding", "flag_negative", "cap_outliers",
                "split_packaging", "normalize_keys", "normalize_percent", "clean_column_names", "fix_case"} <= used
        # données prêtes pour l'ETL
        chang = prod.set_index("productName").loc["Chang"]                    # « 24 - 12 oz bottles »
        assert (chang.nb_unites, chang.taille_unite, chang.unite_mesure, chang.contenant, chang.unite_base) == (24, 12, "oz", "bottle", "ml")
        assert chang.quantite_totale == 8517.17
        assert prod.nb_unites.notna().all() and "quantityPerUnit" in prod.columns   # colonne d'origine conservée
        lignes = pd.read_csv(Path(tmp) / "clean" / "ventes_sale_lignes.csv")
        assert "prix_unitaire" in lignes.columns and lignes.discount.between(0, 1).all()
        assert (cmd.customerID == cmd.customerID.str.upper()).all()
        assert not clients.contactName.dropna().str.fullmatch(r"[A-Z ]{5,}").any()
        run_dir = Path(tmp) / "hist" / "runs" / s["run_id"]
        for f in ["trace.json", "trace.md", "comparaison.html", "view.json", "before/clients_sale.csv", "after/clients_sale.csv"]:
            assert (run_dir / f).exists(), f
        assert json.loads((run_dir / "trace.json").read_text())["steps_applied"] == s["steps_applied"]
        # valeurs extrêmes : rien n'est modifié sans accord (outliers='keep' par défaut)
        assert prod.unitPrice.max() == 9999
        assert any(x["tool"] == "cap_outliers" and x["status"] == "kept" for x in s["steps"])


def test_vue_simple():
    with tempfile.TemporaryDirectory() as tmp:
        v = _run(tmp)["view"]
        names = [t["name"] for t in v["tables"]]
        assert "clients_sale" in names and v["totals"]["tables"] == 4
        clients = next(t for t in v["tables"] if t["name"] == "clients_sale")
        texts = " | ".join(a["text"] for a in clients["actions"])
        assert "22 noms de pays harmonisés" in texts and "2 lignes en double supprimées" in texts
        assert {r["reason"] for r in clients["removed"]["rows"]} == {"doublon", "ligne vide"}
        cell = next(c for r in clients["grid"]["rows"] for c in r["cells"] if c["changed"] and c["before"] == "Deutschland")
        assert cell["after"] == "Germany"
        prod = next(t for t in v["tables"] if t["name"] == "ventes_sale_produits")
        assert prod["derived"][0]["source"] == "quantityPerUnit" and prod["derived"][0]["count"] == 77
        assert "77 conditionnements décomposés" in " | ".join(a["text"] for a in prod["actions"])
        lignes = next(t for t in v["tables"] if t["name"] == "ventes_sale_lignes")
        assert lignes["renamed"] == [{"from": "Prix unitaire (€)", "to": "prix_unitaire"}]
        assert "prix_unitaire" in lignes["grid"]["columns"]
        checks = {c["label"]: c for c in lignes["checks"]}
        assert checks["Clé unique"]["detail"].startswith("orderID + productID")
        assert checks["Lien productID vers ventes_sale_produits"]["ok"] is True
        assert v["totals"]["etl_ready"] == 4


def test_valeurs_extremes_avec_accord():
    with tempfile.TemporaryDirectory() as tmp:
        from agent import CleaningSession
        s = CleaningSession([SAMPLES / "ventes_sale.xlsx"], Path(tmp) / "clean", Path(tmp) / "hist")
        q = next(q for q in s.questions if q["table"] == "ventes_sale_produits" and q["column"] == "unitPrice")
        assert 9999 in q["examples"] and q["recommended"] == "empty"
        assert q["rows"][0]["label"] == "Sir Rodney's Scones" and q["rows"][0]["error"]
        assert q["high"] == 263.5                      # échelle log : le vrai prix de luxe (Côte de Blaye) reste normal
        other = next(q for q in s.questions if q["column"] == "quantity")
        assert other["recommended"] == "keep"
        out = s.finish({q["id"]: "empty"})
        prod = pd.read_csv(Path(tmp) / "clean" / "ventes_sale_produits.csv")
        assert prod.unitPrice.max() == 263.5 and prod.unitPrice.isna().sum() == 1
        step = next(x for x in out["steps"] if x["tool"] == "cap_outliers" and x["column"] == "unitPrice" and x["table"] == "ventes_sale_produits")
        assert step["status"] == "applied" and "mis à vide avec l'accord de l'utilisateur" in step["reason"]
        prod_view = next(t for t in out["view"]["tables"] if t["name"] == "ventes_sale_produits")
        assert any(a["text"] == "1 valeur aberrante mise à vide" for a in prod_view["actions"])
        s = CleaningSession([SAMPLES / "ventes_sale.xlsx"], Path(tmp) / "clean2", Path(tmp) / "hist")
        q = next(q for q in s.questions if q["column"] == "unitPrice")
        s.finish({q["id"]: "cap"})
        assert pd.read_csv(Path(tmp) / "clean2" / "ventes_sale_produits.csv").unitPrice.max() == 263.5


def _clean(df, **kw):
    from agent import CleaningAgent
    from tracer import TraceAgent
    with tempfile.TemporaryDirectory() as tmp:
        return CleaningAgent(TraceAgent(Path(tmp)), **kw).clean_table("t", df.astype(str))


def test_outils_etl():
    df = pd.DataFrame({
        "Code postal": ["05021", "75001", "13008", "69002"],
        "Prix unitaire (€)": ["10,5", "3", "7,25", "12"],
        "Prix unitaire (€).1": ["1", "2", "3", "4"],
        "isActive": ["1", "0", "1", "1"],
        "remise": ["0.1", "15", "0", "5 %"],
        "telephone": ["(1) 555-1234", "01.23.45.67.89", "+33 1 23 45 67 89", "030-0074321"],
        "customerID": ["ALFKI", "anatr", "ANTON", "AROUT"],
        "nom": ["Jean Dupont", "MARIE CURIE", "Paul\u200b Martin", "Anne\nLeroy"],
        "conditionnement": ["6 x 1 l bottles", "12 - 250 g jars", "1k pkg.", "boîte"],
    })
    out = _clean(df)
    assert list(out.columns[1:5]) == ["code_postal", "prix_unitaire", "prix_unitaire_2", "isActive"]
    assert out.code_postal.tolist() == ["05021", "75001", "13008", "69002"]      # code : zéro initial conservé
    assert out.prix_unitaire.tolist() == [10.5, 3, 7.25, 12]
    assert out.isActive.dtype == "boolean" and out.isActive.tolist() == [True, False, True, True]
    assert out.remise.tolist() == [0.1, 0.15, 0, 0.05]
    assert out.telephone.tolist() == ["1 555 1234", "01 23 45 67 89", "+33 1 23 45 67 89", "030 0074321"]
    assert out.customerID.tolist() == ["ALFKI", "ANATR", "ANTON", "AROUT"]
    assert out.nom.tolist() == ["Jean Dupont", "Marie Curie", "Paul Martin", "Anne Leroy"]
    assert out.quantite_totale.tolist()[:3] == [6000, 3000, 1000] and out.unite_base.tolist()[:3] == ["ml", "g", "g"]
    assert pd.isna(out.nb_unites.iloc[3])                                          # non reconnu : laissé tel quel
    assert out.conditionnement.iloc[3] == "boîte"


def test_conditionnement_pas_sur_une_adresse():
    df = pd.DataFrame({"address": ["12 rue de la Paix", "3 avenue Foch", "8 place Bellecour", "1 quai Voltaire"]})
    assert tools.detect_packaging(df, "address") is None


def test_historique_append_only():
    with tempfile.TemporaryDirectory() as tmp:
        a, b = _run(tmp), _run(tmp)
        hist = Path(tmp) / "hist"
        assert a["run_id"] != b["run_id"]
        lines = (hist / "cleaning_history.jsonl").read_text().splitlines()
        assert len(lines) == len(a["steps"]) + len(b["steps"])
        assert len((hist / "runs_index.csv").read_text().splitlines()) == 3


if __name__ == "__main__":
    test_vue_simple()
    test_valeurs_extremes_avec_accord()
    test_nettoyage_et_trace()
    test_historique_append_only()
    test_outils_etl()
    test_conditionnement_pas_sur_une_adresse()
    print("OK")
