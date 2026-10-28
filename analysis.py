"""ÉTAPE 5 bis — Analyse des KPI : un constat par KPI, produit par des RÈGLES explicites (sans LLM).

Chaque constat indique :
  - le KPI analysé et le constat (une phrase construite à partir des valeurs calculées),
  - la règle appliquée (pour que l'agent XAI et Sami puissent justifier la phrase),
  - un niveau : « info » ou « attention ».
Aucun chiffre n'est inventé : tout vient de kpi_results. Les seuils ci-dessous sont des valeurs
INDICATIVES, à adapter avec le métier.
"""

# Seuils indicatifs (modifiables)
SEUIL_PART_N1 = 25.0        # % : un seul pays / produit qui pèse plus que ça = dépendance
SEUIL_PART_TOP3 = 60.0      # % : les 3 premiers pèsent plus que ça = forte concentration
SEUIL_PONCTUALITE = 90.0    # % : livraisons à temps en dessous = point d'attention
SEUIL_COUVERTURE = 100.0    # % : exports / imports en dessous = déficit commercial


def _n(v, d=2):
    """Nombre au format français : 1 234,56"""
    return f"{v:,.{d}f}".replace(",", " ").replace(".", ",")


def _c(kpi_id, res, nom, constat, regle, niveau="info"):
    return {"kpi_id": kpi_id, "nom": nom, "constat": constat, "regle": regle, "niveau": niveau}


def _classement(kpi_id, res, libelle, pluriel):
    k = res[kpi_id]
    lignes = k["valeur"]
    if not lignes:
        return []
    n1, top3 = lignes[0], sum(x["part_pct"] for x in lignes[:3])
    out = [_c(kpi_id, res, k["nom"],
              f"Le {libelle} n°1 est « {n1['nom']} » avec {_n(n1['valeur'], 0)} ({_n(n1['part_pct'], 1)} % du total).",
              "Constat : premier du classement et sa part.",
              "attention" if n1["part_pct"] > SEUIL_PART_N1 else "info")]
    if n1["part_pct"] > SEUIL_PART_N1:
        out[0]["regle"] = f"Part du n°1 > {SEUIL_PART_N1:.0f} % : dépendance à un seul {libelle}."
    if len(lignes) >= 3:
        out.append(_c(kpi_id, res, k["nom"],
                      f"Les 3 premiers {pluriel} représentent {_n(top3, 1)} % du total.",
                      f"Part cumulée du top 3 > {SEUIL_PART_TOP3:.0f} % : forte concentration.",
                      "attention" if top3 > SEUIL_PART_TOP3 else "info"))
    return out


def analyser(res: dict) -> list:
    """res = state['kpi_results']. Retourne la liste des constats."""
    out = []

    if "total_revenue" in res:
        k = res["total_revenue"]
        out.append(_c("total_revenue", res, k["nom"],
                      f"Le chiffre d'affaires total est de {_n(k['valeur'], 0)}.", "Constat : valeur du KPI."))

    if "order_count" in res and "avg_order_value" in res:
        o, a = res["order_count"], res["avg_order_value"]
        out.append(_c("avg_order_value", res, a["nom"],
                      f"{_n(o['valeur'], 0)} commandes, soit un panier moyen de {_n(a['valeur'])} par commande.",
                      "Constat : panier moyen = CA / nombre de commandes."))

    if "yoy_growth" in res:
        y = res["yoy_growth"]["valeur"]
        croissance = y.get("croissance_pct", {})
        if croissance:
            cle = max(croissance, key=int)          # les clés peuvent être du texte si lues depuis un JSON
            an, g = int(cle), croissance[cle]
            out.append(_c("yoy_growth", res, res["yoy_growth"]["nom"],
                          f"Le CA {'augmente' if g >= 0 else 'baisse'} de {_n(abs(g), 1)} % en {an} par rapport à "
                          f"{an - 1}, sur les mêmes mois.",
                          "Croissance négative = point d'attention.", "attention" if g < 0 else "info"))
        else:
            out.append(_c("yoy_growth", res, res["yoy_growth"]["nom"],
                          "Pas assez d'années en commun pour calculer une croissance.",
                          "Il faut au moins deux années avec des mois communs."))
        incompletes = [a for a, m in y.get("mois_couverts", {}).items() if m < 12]
        if incompletes:
            out.append(_c("yoy_growth", res, res["yoy_growth"]["nom"],
                          f"Année(s) incomplète(s) : {', '.join(map(str, incompletes))}. Leurs totaux ne sont pas "
                          "comparables d'une année à l'autre.",
                          "Moins de 12 mois couverts dans l'année.", "attention"))

    if "top_countries" in res:
        out += _classement("top_countries", res, "pays", "pays")
    if "top_products" in res:
        out += _classement("top_products", res, "produit", "produits")

    if "avg_lead_time" in res:
        k = res["avg_lead_time"]
        out.append(_c("avg_lead_time", res, k["nom"],
                      f"Une commande est expédiée en moyenne {_n(k['valeur'], 1)} jours après sa date de commande.",
                      "Constat : moyenne (date d'expédition - date de commande), une ligne par commande."))

    if "on_time_rate" in res:
        k = res["on_time_rate"]
        v = k["valeur"]
        if v is not None:
            out.append(_c("on_time_rate", res, k["nom"],
                          f"{_n(v, 1)} % des commandes expédiées le sont avant la date exigée.",
                          f"Ponctualité < {SEUIL_PONCTUALITE:.0f} % : point d'attention.",
                          "attention" if v < SEUIL_PONCTUALITE else "info"))

    if "trade_balance" in res:
        b = res["trade_balance"]["valeur"]
        out.append(_c("trade_balance", res, res["trade_balance"]["nom"],
                      f"Le solde commercial est {'excédentaire' if b >= 0 else 'déficitaire'} "
                      f"({_n(b, 0)}) : les exports {'dépassent' if b >= 0 else 'restent inférieurs aux'} imports.",
                      "Solde = exports - imports ; négatif = déficit (point d'attention).",
                      "attention" if b < 0 else "info"))
    if "coverage_ratio" in res and res["coverage_ratio"]["valeur"] is not None:
        c = res["coverage_ratio"]["valeur"]
        out.append(_c("coverage_ratio", res, res["coverage_ratio"]["nom"],
                      f"Les exports couvrent {_n(c, 1)} % des imports.",
                      f"Couverture < {SEUIL_COUVERTURE:.0f} % : déficit commercial.",
                      "attention" if c < SEUIL_COUVERTURE else "info"))
    return out


# =====================================================================================
# Version LLM : le LLM RÉDIGE l'interprétation, à partir des constats déjà calculés par les règles.
# Garde-fou : un chiffre qui n'existe pas dans les KPI fait rejeter la phrase (jamais de chiffre inventé).
# =====================================================================================
import re

from pydantic import BaseModel, Field


class Interpretation(BaseModel):
    id: str = Field(description="identifiant du constat (c0, c1...)")
    interpretation: str = Field(description="une phrase : ce que ce constat signifie pour le métier")


class AnalyseLLM(BaseModel):
    resume: str = Field(description="synthèse en 3 phrases maximum")
    interpretations: list[Interpretation]


_NOMBRE = re.compile(r"\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")


def _nombres(texte: str) -> list:
    """Tous les nombres d'un texte (formats « 1 234,56 », « 12.5 »...)."""
    return [float(m.replace(" ", "").replace(" ", "").replace(" ", "").replace(",", "."))
            for m in _NOMBRE.findall(texte or "")]


def _valeurs(o) -> list:
    """Tous les nombres présents dans kpi_results (valeurs, parts, années...)."""
    if isinstance(o, dict):
        return [x for k, v in o.items() for x in _valeurs(k) + _valeurs(v)]
    if isinstance(o, (list, tuple)):
        return [x for v in o for x in _valeurs(v)]
    if isinstance(o, bool):
        return []
    if isinstance(o, (int, float)):
        return [float(o)]
    if isinstance(o, str):
        return _nombres(o)
    return []


def nombres_inconnus(texte: str, autorises: list) -> list:
    """Nombres du texte qui ne figurent pas dans les données (petits entiers <= 12 tolérés : « top 3 »)."""
    inconnus = []
    for x in _nombres(texte):
        if x == int(x) and 0 <= x <= 12:
            continue
        if not any(round(a, d) == x or abs(a - x) <= 0.005 * max(abs(a), 1) for a in autorises for d in (0, 1, 2)):
            inconnus.append(x)
    return inconnus


PROMPT = ("Tu es analyste BI pour des données de commerce. Voici des constats DÉJÀ calculés sur des KPI. "
          "Pour chaque constat (id c0, c1...), écris UNE phrase qui explique ce qu'il signifie pour le métier. "
          "Écris aussi un résumé de 3 phrases maximum. RÈGLES : utilise UNIQUEMENT les chiffres fournis, "
          "n'invente aucun chiffre, ne donne aucune recommandation, réponds en français.\n\nCONSTATS :\n")


def analyser_llm(res: dict, constats: list, llm=None):
    """Retourne (constats enrichis, résumé ou None, nombre de phrases rejetées).
    `llm` : un modèle LangChain (par défaut llama3.1 via Ollama) ; peut être remplacé pour les tests."""
    if llm is None:
        from langchain_ollama import ChatOllama          # importé ici : le LLM est optionnel
        llm = ChatOllama(model="llama3.1", temperature=0)
    entree = [{"id": f"c{i}", "kpi": c["nom"], "constat": c["constat"], "niveau": c["niveau"]}
              for i, c in enumerate(constats)]
    sortie = llm.with_structured_output(AnalyseLLM).invoke(
        PROMPT + "\n".join(f"- {e['id']} [{e['niveau']}] {e['kpi']} : {e['constat']}" for e in entree))

    autorises = _valeurs(res) + [x for c in constats for x in _nombres(c["constat"])]
    par_id = {i.id: i.interpretation for i in sortie.interpretations}
    rejetees, enrichis = 0, []
    for i, c in enumerate(constats):
        texte = par_id.get(f"c{i}")
        inconnus = nombres_inconnus(texte, autorises) if texte else []
        if inconnus:
            rejetees += 1
        enrichis.append({**c, "interpretation": None if inconnus or not texte else texte,
                         "verifie": bool(texte) and not inconnus,
                         "source": "LLM" if texte and not inconnus else "règles"})
    resume = sortie.resume if sortie.resume and not nombres_inconnus(sortie.resume, autorises) else None
    return enrichis, resume, rejetees