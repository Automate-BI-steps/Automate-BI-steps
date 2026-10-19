"""Agent 4 — Semantic & KPI (DSO4) : l'agent qui définit et calcule les KPI.

Remplace l'étape « mesures DAX » de Power BI. Il fait 6 étapes, dans cet ordre :

  1 charger_donnees     récupère la table de faits (fact_trade)
  2 comprendre_colonnes associe chaque colonne à un rôle (date, montant, pays...)
  3 calculer_kpi        calcule les KPI possibles avec pandas
  4 documenter_kpi      écrit le KPI catalog et les mesures DAX
  5 controler           vérifie la cohérence des chiffres
  6 exporter            écrit les fichiers de sortie et le journal d'audit

Dans le graphe de l'équipe :  main.add_node("semantic_kpi", build_graph())
"""
import json
import operator
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, TypedDict

import numpy as np
import pandas as pd
from langgraph.graph import END, StateGraph

from data import load_fact_table
from kpis import CATALOG, standardise
from mapping import map_columns


class State(TypedDict, total=False):
    """Mémoire partagée entre les étapes (et entre les agents de l'équipe)."""
    data_path: str                          # entrée : dossier des CSV
    fact_trade: pd.DataFrame                # entrée : table de faits (agent ETL)
    semantic_mapping: dict                  # rôle -> colonne
    kpi_results: dict                       # sortie : valeurs des KPI
    kpi_catalog: list                       # sortie : définition de chaque KPI
    dax_measures: dict                      # sortie : mesures DAX
    kpi_checks: list                        # sortie : contrôles de cohérence
    needs_human_validation: bool            # True si un contrôle a échoué
    validation_reasons: list                # pourquoi une validation humaine est conseillée
    mapping_sources: dict                   # rôle -> « LLM » ou « mots-clés »
    lineage: dict                           # colonne de fact_trade -> table source
    outputs: dict                           # chemins des fichiers écrits
    audit_log: Annotated[list, operator.add]    # journal : chaque étape AJOUTE une ligne
    errors: Annotated[list, operator.add]


def log(etape: str, message: str, **extra) -> dict:
    """Une ligne du journal d'audit (lue par l'agent XAI)."""
    return {"etape": etape, "message": message,
            "heure": datetime.now().isoformat(timespec="seconds"), **extra}


def en_json(o):
    """Convertit les types numpy en types Python ; NaN devient null."""
    if isinstance(o, dict):
        return {str(k): en_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [en_json(v) for v in o]
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return None if np.isnan(o) else float(o)
    return o


# ------------------------------------------------------------------ les 6 étapes
def charger_donnees(state: State) -> dict:
    if state.get("fact_trade") is not None:                      # déjà fournie par l'agent ETL
        return {"audit_log": [log("charger_donnees", "Table de faits reçue de l'agent ETL")]}
    try:
        df, info = load_fact_table(state["data_path"])
    except Exception as e:
        return {"errors": [f"Chargement impossible : {e}"]}
    return {"fact_trade": df, "lineage": info.get("origine", {}), "audit_log": [log(
        "charger_donnees", f"{len(df)} lignes, {df.shape[1]} colonnes ({info['methode']})", **info)]}


def comprendre_colonnes(state: State) -> dict:
    mapping, methode, sources = map_columns(state["fact_trade"], use_llm=os.getenv("USE_LLM", "0") == "1")
    sortie = {"semantic_mapping": mapping, "mapping_sources": sources,
              "audit_log": [log("comprendre_colonnes", f"Colonnes -> rôles ({methode})",
                                mapping=mapping, sources=sources)]}
    if "value" not in mapping:
        sortie["errors"] = ["Aucune colonne de montant trouvée : impossible de calculer les KPI"]
    return sortie


def calculer_kpi(state: State) -> dict:
    mapping = state["semantic_mapping"]
    d = standardise(state["fact_trade"], mapping)
    resultats, ignores, erreurs = {}, {}, []
    for k in CATALOG:
        manquants = [r for r in k.requires if r not in mapping]
        if manquants:                                            # il manque des colonnes : on saute
            ignores[k.id] = f"rôles manquants : {manquants}"
            continue
        try:
            resultats[k.id] = {"nom": k.nom, "valeur": k.compute(d), "unite": k.unite,
                               "definition": k.definition, "formule": k.formule,
                               "bo": k.bo, "pourquoi": k.pourquoi,
                               "colonnes_sources": [mapping[r] for r in k.requires],
                               "lignes_utilisees": int(d["value"].notna().sum())}
        except Exception as e:                                   # un KPI qui plante n'arrête pas les autres
            erreurs.append(f"KPI {k.id} en échec : {e}")
    return {"kpi_results": resultats, "errors": erreurs,
            "audit_log": [log("calculer_kpi", f"{len(resultats)}/{len(CATALOG)} KPI calculés",
                              calcules=list(resultats), ignores=ignores)]}


def documenter_kpi(state: State) -> dict:
    mapping, table = state["semantic_mapping"], os.getenv("FACT_TABLE", "Fact_Trade")
    colonnes_dax = {role: f"{table}[{col}]" for role, col in mapping.items()}   # ex. Fact_Trade[lineTotal]
    origine = state.get("lineage") or {}                                          # colonne -> table source
    catalogue, dax = [], {}
    for k in CATALOG:
        if k.id in state["kpi_results"]:
            dax[k.nom] = k.dax.format(**colonnes_dax)
            catalogue.append({"id": k.id, "nom": k.nom, "definition": k.definition, "formule": k.formule,
                              "bo": k.bo, "pourquoi": k.pourquoi, "dax": dax[k.nom], "unite": k.unite,
                              "colonnes_sources": state["kpi_results"][k.id]["colonnes_sources"],
                              "origine": {c: origine.get(c, "fichier source")
                                          for c in state["kpi_results"][k.id]["colonnes_sources"]}})
    return {"kpi_catalog": catalogue, "dax_measures": dax,
            "audit_log": [log("documenter_kpi", f"{len(catalogue)} KPI documentés (formule, colonnes, DAX)")]}


def controler(state: State) -> dict:
    mapping, res = state["semantic_mapping"], state["kpi_results"]
    d = standardise(state["fact_trade"], mapping)
    checks = []

    def ajouter(nom, detail, ok):
        checks.append({"controle": nom, "detail": detail, "statut": "ok" if ok else "avertissement"})

    try:
        negatifs = int((d["value"] < 0).sum())
        ajouter("montants négatifs", str(negatifs), negatifs == 0)
        vides = d["value"].isna().mean() * 100
        ajouter("montants vides ou non numériques", f"{vides:.1f}%", vides < 5)
        if "total_revenue" in res:
            ecart = abs(d["value"].sum() - res["total_revenue"]["valeur"])
            ajouter("CA = somme des lignes", f"écart {ecart:.2f}", ecart < 0.01)
        if "top_countries" in res:
            total = sum(p["part_pct"] for p in res["top_countries"]["valeur"])
            ajouter("parts des pays <= 100 %", f"{total:.1f}%", total <= 100.01)
        if "date" in d and "shipped_date" in d:
            avant = int(((d["shipped_date"] - d["date"]).dt.days < 0).sum())
            ajouter("expédition avant la commande", str(avant), avant == 0)
        if "flow" in d:
            autres = (d["flow"] == "autre").mean() * 100
            ajouter("flux non reconnus", f"{autres:.1f}%", autres < 5)
        if all(k in res for k in ("total_exports", "total_imports", "trade_balance")):
            ecart = abs(res["total_exports"]["valeur"] - res["total_imports"]["valeur"] - res["trade_balance"]["valeur"])
            ajouter("solde = exports - imports", f"écart {ecart:.2f}", ecart < 0.01)
    except Exception as e:
        ajouter("contrôles", f"erreur : {e}", False)

    raisons = [f"{c['controle']} : {c['detail']}" for c in checks if c["statut"] != "ok"]
    a_verifier = bool(raisons)                       # c'est l'orchestrateur qui déclenche la validation
    return {"kpi_checks": checks, "needs_human_validation": a_verifier, "validation_reasons": raisons,
            "audit_log": [log("controler", "Validation humaine conseillée" if a_verifier
                              else "Tous les contrôles sont OK", controles=checks, raisons=raisons)]}


def exporter(state: State) -> dict:
    dossier = Path(os.getenv("OUTPUT_DIR", "outputs"))
    dossier.mkdir(parents=True, exist_ok=True)
    fichiers = {"kpi_catalog": dossier / "kpi_catalog.json", "kpi_results": dossier / "kpi_results.json",
                "dax_measures": dossier / "dax_measures.dax", "audit_log": dossier / "agent4_audit_log.json"}
    ecrire = lambda chemin, obj: chemin.write_text(json.dumps(en_json(obj), ensure_ascii=False, indent=2), "utf-8")
    ecrire(fichiers["kpi_catalog"], state["kpi_catalog"])
    ecrire(fichiers["kpi_results"], state["kpi_results"])
    fichiers["dax_measures"].write_text(
        "\n\n".join(f"{nom} =\n{expr}" for nom, expr in state["dax_measures"].items()), "utf-8")
    ecrire(fichiers["audit_log"], state.get("audit_log", []))
    return {"outputs": {k: str(v) for k, v in fichiers.items()},
            "audit_log": [log("exporter", f"Fichiers écrits dans {dossier}")]}


# ------------------------------------------------------------------ le graphe
def build_graph():
    """Relie les étapes. S'il n'y a plus rien à faire (pas de données, pas de KPI), on s'arrête."""
    g = StateGraph(State)
    for nom, fonction in [("charger_donnees", charger_donnees), ("comprendre_colonnes", comprendre_colonnes),
                          ("calculer_kpi", calculer_kpi), ("documenter_kpi", documenter_kpi),
                          ("controler", controler), ("exporter", exporter)]:
        g.add_node(nom, fonction)
    g.set_entry_point("charger_donnees")
    g.add_conditional_edges("charger_donnees", lambda s: "suite" if s.get("fact_trade") is not None else "fin",
                            {"suite": "comprendre_colonnes", "fin": END})
    g.add_conditional_edges("comprendre_colonnes", lambda s: "suite" if "value" in s["semantic_mapping"] else "fin",
                            {"suite": "calculer_kpi", "fin": END})
    g.add_conditional_edges("calculer_kpi", lambda s: "suite" if s.get("kpi_results") else "fin",
                            {"suite": "documenter_kpi", "fin": END})
    g.add_edge("documenter_kpi", "controler")
    g.add_edge("controler", "exporter")
    g.add_edge("exporter", END)
    return g.compile()


def run(data_path: str) -> dict:
    """Lance l'agent seul sur un dossier de CSV et retourne l'état final."""
    return build_graph().invoke({"data_path": data_path})
