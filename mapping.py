"""ÉTAPE 2 — Comprendre les colonnes : « quelle colonne est la date ? le montant ? le pays ? »

On associe chaque colonne du dataset à un RÔLE métier. Les KPI utilisent ensuite les rôles,
jamais les noms de colonnes : l'agent marche donc sur n'importe quel dataset.
Méthode : mots-clés (toujours disponible) ; un LLM peut aider (optionnel, USE_LLM=1).
"""
from typing import Optional

import pandas as pd

# role -> (description, mots-clés). L'ordre compte : les rôles précis passent avant "date".
ROLES = {
    "order_id":      ("Identifiant de commande",       ["orderid", "order_id", "commande"]),
    "shipped_date":  ("Date d'expédition",             ["shipped", "expedi"]),
    "required_date": ("Date de livraison exigée",      ["required", "due_date"]),
    "date":          ("Date de la commande ou année",  ["orderdate", "order_date", "date", "year", "annee", "period"]),
    "flow":          ("Import ou Export",              ["flow", "flux", "direction", "trade_type", "import_export"]),
    "country":       ("Pays",                          ["country", "pays", "partner", "partenaire"]),
    "product":       ("Produit",                       ["product", "produit", "commodity", "hs_code"]),
    "value":         ("Montant de la ligne (numérique)", ["linetotal", "revenue", "sales", "value", "valeur", "amount", "montant", "total"]),
}
FLUX_CONNUS = {"import", "export", "imports", "exports", "imp", "exp"}


def est_numerique(s: pd.Series) -> bool:
    return pd.to_numeric(s, errors="coerce").notna().mean() > 0.8


def ressemble_a_un_flux(s: pd.Series) -> bool:
    valeurs = set(s.dropna().astype(str).str.lower().str.strip().unique())
    return 0 < len(valeurs) <= 10 and valeurs <= FLUX_CONNUS


def mapping_par_mots_cles(df: pd.DataFrame) -> dict:
    mapping, deja_pris = {}, set()
    for role, (_, mots) in ROLES.items():
        meilleur, meilleur_score = None, 0
        for col in df.columns:
            if col in deja_pris or (role == "value" and not est_numerique(df[col])):
                continue
            score = sum(mot in str(col).lower() for mot in mots)
            if role == "flow" and ressemble_a_un_flux(df[col]):
                score += 3                                   # reconnu par ses valeurs
            if score > meilleur_score:
                meilleur, meilleur_score = col, score
        if meilleur is not None:
            mapping[role] = meilleur
            deja_pris.add(meilleur)
    return mapping


def mapping_par_llm(df: pd.DataFrame) -> dict:
    from langchain_ollama import ChatOllama          # importé ici : le LLM est optionnel
    from pydantic import Field, create_model
    Modele = create_model("Mapping", **{r: (Optional[str], Field(None, description=d))
                                        for r, (d, _) in ROLES.items()})
    colonnes = "\n".join(f"- {c} ex: {df[c].dropna().astype(str).head(3).tolist()}" for c in df.columns)
    llm = ChatOllama(model="llama3.1", temperature=0).with_structured_output(Modele)
    return llm.invoke("Associe chaque rôle à UNE colonne (nom exact) ou null. "
                      "N'invente aucune colonne.\n" + colonnes).model_dump()


def map_columns(df: pd.DataFrame, use_llm: bool = False):
    """Retourne (mapping, méthode, sources). Le LLM propose, le code vérifie, les mots-clés complètent.
    sources dit, pour chaque rôle, qui a trouvé la colonne : « LLM » ou « mots-clés »."""
    mapping, sources, methode = {}, {}, "mots-clés"
    if use_llm:
        try:
            for role, col in mapping_par_llm(df).items():
                valide = col in df.columns and col not in mapping.values()
                if valide and (role != "value" or est_numerique(df[col])):
                    mapping[role], sources[role] = col, "LLM"
            methode = "LLM + vérification"
        except Exception:
            methode = "mots-clés (LLM indisponible)"
    for role, col in mapping_par_mots_cles(df).items():
        if role not in mapping and col not in mapping.values():
            mapping[role], sources[role] = col, "mots-clés"
    return mapping, methode, sources
