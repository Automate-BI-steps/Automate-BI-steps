"""Outils de nettoyage de l'agent Quality & Cleaning : rendre chaque table prête pour un ETL.

Chaque outil a deux fonctions :
  - detect(df, col) -> Decision | None : observe la table et dit si l'outil doit s'appliquer, et pourquoi ;
  - apply(df, col, **params) -> df : applique la correction.
L'agent (agent.py) parcourt les outils dans l'ordre de TOOLS, décide colonne par colonne et
fait tracer chaque décision par l'agent de traçage (tracer.py).

Familles d'outils :
  - structure : noms de colonnes compatibles ETL, lignes et colonnes vides, doublons ;
  - texte     : espaces et caractères invisibles, accents mal encodés, majuscules, variantes d'écriture, pays ;
  - types     : nombres (sans casser les codes à zéro initial), dates, booléens (oui/non, 0/1) ;
  - métier    : clés de jointure, taux en %, montants au centime, téléphones,
                conditionnements décomposés en colonnes (ex. « 24 - 12 oz bottles ») ;
  - contrôle  : valeurs extrêmes (corrigées seulement avec l'accord de l'utilisateur) et signalements.
Un outil de type "flag" ne modifie jamais les données : il signale seulement un problème dans la trace.
Un outil marqué ask=True (valeurs extrêmes) n'est appliqué qu'après accord de l'utilisateur.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

ROW_ID = "__row_id"  # identifiant de ligne interne, conservé pour la comparaison avant / après

MISSING_TOKENS = {"", "na", "n/a", "n.a.", "nan", "null", "none", "nil", "-", "--", "?", "#n/a", "#na", "missing", "vide", "inconnu"}
TRUE_TOKENS = {"true", "vrai", "yes", "oui", "y", "o", "t"}
FALSE_TOKENS = {"false", "faux", "no", "non", "n", "f"}
COUNTRY_ALIASES = {
    "usa": "USA", "us": "USA", "u.s.a.": "USA", "u.s.": "USA", "united states": "USA", "etats-unis": "USA", "états-unis": "USA",
    "uk": "UK", "u.k.": "UK", "united kingdom": "UK", "great britain": "UK", "royaume-uni": "UK", "england": "UK",
    "deutschland": "Germany", "allemagne": "Germany", "germany": "Germany",
    "france": "France", "españa": "Spain", "espagne": "Spain", "spain": "Spain",
    "brasil": "Brazil", "brésil": "Brazil", "brazil": "Brazil", "italia": "Italy", "italie": "Italy", "italy": "Italy",
    "mexique": "Mexico", "méxico": "Mexico", "mexico": "Mexico", "belgique": "Belgium", "belgium": "Belgium",
    "suisse": "Switzerland", "schweiz": "Switzerland", "switzerland": "Switzerland", "autriche": "Austria",
    "österreich": "Austria", "austria": "Austria", "canada": "Canada", "tunisie": "Tunisia", "tunisia": "Tunisia",
}
KEY_PATTERN = re.compile(r"(^id$|id$|_id$|key$|_key$|code$)", re.IGNORECASE)
QUANTITY_PATTERN = re.compile(r"(qty|quantit|price|prix|amount|montant|freight|fret|cost|cout|coût)", re.IGNORECASE)
CODE_NAME = re.compile(r"(?i:postal|zip|code|phone|fax|mobile|gsm|siret|siren|iban|isbn|sku|^t[eé]l(_|$|[eé]phone)|^ean(_|$))")
BOOL_NAME = re.compile(r"^(is|has|est)_|^(is|has)[A-Z]|(?i:discontinu|actif|active|enabled|deleted|supprim|archiv|flag|bool)")
MONEY_NAME = re.compile(r"(price|prix|amount|montant|freight|fret|cost|co[uû]t|total|revenue|tarif|salaire|salary)", re.IGNORECASE)
PERCENT_NAME = re.compile(r"(discount|remise|rabais|taux|rate|pct|percent|pourcent|%)", re.IGNORECASE)
PHONE_NAME = re.compile(r"(?i:phone|fax|mobile|gsm|portable|^t[eé]l(_|$|[eé]phone))")
LABEL_NAME = re.compile(r"(name|nom|libell|title|titre|designation|description|produit|product)", re.IGNORECASE)
PACKAGING_NAME = re.compile(r"(quantit.*unit|qty|pack|condition|emballage|format|contenance|unit|taille|size)", re.IGNORECASE)
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass
class Decision:
    reason: str
    params: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- aides
def data_cols(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c != ROW_ID]


def is_text(s: pd.Series) -> bool:
    return pd.api.types.is_string_dtype(s) or s.dtype == object


def is_number(s: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)


def is_key(name: str) -> bool:
    return bool(KEY_PATTERN.search(str(name)))


def non_null_text(s: pd.Series) -> pd.Series:
    return s.dropna().astype(str)


def pct(n: int, total: int) -> str:
    return f"{(100 * n / total):.0f} %" if total else "0 %"


def plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def fnum(v: float) -> str:
    """12.5 -> '12,5' ; 9999.0 -> '9 999' (affichage français)."""
    s = f"{v:,.2f}".rstrip("0").rstrip(".")
    return s.replace(",", " ").replace(".", ",")


def to_number(value: str):
    """'1 234,50 €' -> 1234.5 ; renvoie None si la valeur n'est pas un nombre."""
    v = re.sub(r"[\s €$£%]", "", str(value))
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?", v):      # 1.234,50
        v = v.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?", v):    # 1,234.50
        v = v.replace(",", "")
    elif re.fullmatch(r"-?\d+,\d+", v):                       # 32,38
        v = v.replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


DATE_FORMATS = ["%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d", "%d/%m/%y", "%m/%d/%Y", "%d %b %Y", "%d %B %Y"]


def to_date(value: str, dayfirst: bool = True):
    v = str(value).strip()
    formats = DATE_FORMATS if dayfirst else [f for f in DATE_FORMATS if f != "%d/%m/%Y"] + ["%d/%m/%Y"]
    for fmt in formats:
        try:
            return pd.to_datetime(v, format=fmt)
        except (ValueError, TypeError):
            continue
    return None


def label_columns(df: pd.DataFrame) -> list[str]:
    """Colonnes qui permettent à l'utilisateur de reconnaître une ligne (nom du produit, sinon les clés)."""
    cols = data_cols(df)
    names = [c for c in cols if LABEL_NAME.search(c) and is_text(df[c]) and not is_key(c)]
    return names[:1] or [c for c in cols if is_key(c)][:2]


# --------------------------------------------------------------------------- outils de structure
def _clean_name(name: str, position: int) -> str:
    n = str(name).strip()
    if not n or re.fullmatch(r"Unnamed: \d+", n):
        return f"colonne_{position + 1}"
    n = re.sub(r"\.\d+$", "", n)  # doublon d'en-tête renommé par pandas (« prix.1 ») : le suffixe _2 est ajouté ensuite
    if IDENTIFIER.fullmatch(n):
        return n
    folded = unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode()
    s = re.sub(r"[^0-9A-Za-z]+", "_", folded).strip("_").lower() or f"colonne_{position + 1}"
    return f"c_{s}" if s[0].isdigit() else s


def detect_column_names(df, col=None):
    used, mapping = set(), {}
    for i, c in enumerate(data_cols(df)):
        new, k = _clean_name(c, i), 2
        base = new
        while new in used:
            new, k = f"{base}_{k}", k + 1
        used.add(new)
        if new != c:
            mapping[c] = new
    if not mapping:
        return None
    ex = next(iter(mapping.items()))
    return Decision(f"{plural(len(mapping), 'nom de colonne rendu compatible', 'noms de colonnes rendus compatibles')} ETL "
                    f"(sans espaces, accents, symboles ni doublon), ex. « {ex[0]} » -> « {ex[1]} »",
                    {"mapping": mapping, "count": len(mapping)})


def apply_column_names(df, col=None, mapping=None, **_):
    return df.rename(columns=mapping or {})


def detect_empty_rows(df, col=None):
    cols = data_cols(df)
    n = int(df[cols].isna().all(axis=1).sum())
    return Decision(f"{n} ligne(s) entièrement vide(s)") if n else None


def apply_empty_rows(df, col=None):
    return df[~df[data_cols(df)].isna().all(axis=1)]


def detect_empty_columns(df, col=None):
    empty = [c for c in data_cols(df) if df[c].isna().all()]
    return Decision(f"colonne(s) entièrement vide(s) : {empty}", {"columns": empty}) if empty else None


def apply_empty_columns(df, col=None, columns=()):
    return df.drop(columns=list(columns))


def detect_duplicates(df, col=None):
    n = int(df.duplicated(subset=data_cols(df)).sum())
    return Decision(f"{n} ligne(s) en double exact ({pct(n, len(df))})") if n else None


def apply_duplicates(df, col=None):
    return df.drop_duplicates(subset=data_cols(df), keep="first")


def detect_duplicate_keys(df, col=None):
    keys = [c for c in data_cols(df) if is_key(c)]
    if len(keys) != 1:
        return None  # clé composite ou absente : pas de contrôle automatique
    k = keys[0]
    n = int(df[k].dropna().duplicated().sum())
    return Decision(f"{n} valeur(s) de clé '{k}' en double avec des contenus différents : à arbitrer", {"key": k}) if n else None


# --------------------------------------------------------------------------- outils de texte
def detect_missing_tokens(df, col):
    if not is_text(df[col]):
        return None
    s = df[col].dropna().astype(str).str.strip().str.lower()
    hits = s[s.isin(MISSING_TOKENS)]
    if hits.empty:
        return None
    tokens = sorted(set(hits))
    return Decision(f"{len(hits)} marqueur(s) de valeur manquante {tokens} remplacés par une vraie valeur vide", {"tokens": tokens})


def apply_missing_tokens(df, col, tokens=()):
    s = df[col]
    mask = s.astype(str).str.strip().str.lower().isin(set(tokens)) & s.notna()
    df[col] = s.mask(mask)
    return df


INVISIBLE = re.compile("[​‌‍⁠﻿­]")


def _clean_spaces(v: str) -> str:
    """Espaces en trop, tabulations, retours à la ligne, espaces insécables et caractères invisibles."""
    return re.sub(r"\s+", " ", INVISIBLE.sub("", v)).strip()


def detect_whitespace(df, col):
    if not is_text(df[col]):
        return None
    s = non_null_text(df[col])
    n = int((s != s.map(_clean_spaces)).sum())
    return Decision(f"{n} valeur(s) avec espaces en trop, retours à la ligne ou caractères invisibles") if n else None


def apply_whitespace(df, col):
    df[col] = df[col].map(lambda v: (_clean_spaces(v) or None) if isinstance(v, str) else v)
    return df


MOJIBAKE = re.compile("[ÃÂ][\u0080-¿]|â€")


def _fix_mojibake(v):
    try:
        return v.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return v


def detect_mojibake(df, col):
    if not is_text(df[col]):
        return None
    s = non_null_text(df[col])
    n = int(s.str.contains(MOJIBAKE).sum())
    return Decision(f"{n} valeur(s) avec accents mal encodés (ex. 'Ã©' au lieu de 'é')") if n else None


def apply_mojibake(df, col):
    df[col] = df[col].map(lambda v: _fix_mojibake(v) if isinstance(v, str) and MOJIBAKE.search(v) else v)
    return df


def _shouting(v: str) -> bool:
    letters = re.sub(r"[^A-Za-zÀ-ÿ]", "", v)
    return len(letters) >= 4 and letters.isupper()


def detect_case(df, col):
    """Textes écrits TOUT EN MAJUSCULES au milieu de textes normaux : remis en casse normale."""
    if not is_text(df[col]) or is_key(col) or CODE_NAME.search(col):
        return None
    s = non_null_text(df[col])
    lettered = s[s.str.contains(r"[A-Za-zÀ-ÿ]{2}")]
    if len(lettered) < 3:
        return None
    loud = lettered[lettered.map(_shouting)]
    if loud.empty or len(loud) / len(lettered) > 0.4:
        return None  # colonne volontairement en majuscules (codes, sigles)
    return Decision(f"{len(loud)} texte(s) écrits en majuscules (ex. « {loud.iloc[0]} ») remis en casse normale",
                    {"count": int(len(loud))})


def apply_case(df, col, **_):
    df[col] = df[col].map(lambda v: v.title() if isinstance(v, str) and _shouting(v) else v)
    return df


def detect_country(df, col):
    if not is_text(df[col]) or not re.search(r"country|pays", str(col), re.IGNORECASE):
        return None
    s = non_null_text(df[col])
    mapped = s.map(lambda v: COUNTRY_ALIASES.get(v.strip().lower(), v))
    n = int((mapped != s).sum())
    return Decision(f"{n} nom(s) de pays standardisé(s) (ex. 'United States' -> 'USA')") if n else None


def apply_country(df, col):
    df[col] = df[col].map(lambda v: COUNTRY_ALIASES.get(v.strip().lower(), v) if isinstance(v, str) else v)
    return df


def _fold(v: str) -> str:
    return unicodedata.normalize("NFKD", v).encode("ascii", "ignore").decode().lower().strip()


def detect_category_variants(df, col):
    if not is_text(df[col]) or is_key(col):
        return None
    s = non_null_text(df[col])
    if s.empty or s.nunique() > max(50, 0.5 * len(s)):
        return None  # texte libre, pas une catégorie
    groups = s.groupby(s.map(_fold)).agg(lambda x: x.value_counts().index.tolist())
    variants = {k: v for k, v in groups.items() if len(v) > 1}
    if not variants:
        return None
    mapping = {var: forms[0] for forms in variants.values() for var in forms[1:]}
    n = int(s.isin(mapping).sum())
    example = next(iter(variants.values()))
    return Decision(f"{n} valeur(s) écrites de plusieurs façons (casse ou accents), ex. {example} : forme la plus fréquente retenue", {"mapping": mapping})


def apply_category_variants(df, col, mapping=None):
    df[col] = df[col].map(lambda v: mapping.get(v, v) if isinstance(v, str) else v)
    return df


# --------------------------------------------------------------------------- outils de type
def _looks_like_code(col: str, s: pd.Series) -> bool:
    """Codes postaux, téléphones, SIRET, identifiants à zéro initial : à garder en texte."""
    return bool(CODE_NAME.search(col)) or bool(s.str.match(r"^\s*0\d").any())


def _is_01_flag(col: str, s: pd.Series) -> bool:
    return bool(BOOL_NAME.search(col)) and s.str.strip().isin({"0", "1"}).all()


def detect_numeric(df, col, threshold: float = 0.9):
    if not is_text(df[col]):
        return None
    s = non_null_text(df[col])
    if s.empty or _looks_like_code(col, s) or _is_01_flag(col, s):
        return None
    parsed = s.map(to_number)
    ok = parsed.notna().sum()
    if ok / len(s) < threshold:
        return None
    reformatted = int((s.str.contains(r"[,\s€$£%]", regex=True) & parsed.notna()).sum())
    bad = int(len(s) - ok)
    reason = f"{pct(ok, len(s))} des valeurs sont numériques : conversion en nombre"
    if reformatted:
        reason += f" ({reformatted} au format texte, ex. virgule décimale ou symbole monétaire)"
    if bad:
        reason += f" ; {bad} valeur(s) non numériques mises à vide"
    return Decision(reason)


def apply_numeric(df, col):
    nums = df[col].map(lambda v: to_number(v) if pd.notna(v) else None).astype("Float64")
    whole = nums.dropna()
    df[col] = nums.astype("Int64") if len(whole) and (whole % 1 == 0).all() else nums
    return df


def detect_dates(df, col, threshold: float = 0.9, dayfirst: bool = True):
    if not is_text(df[col]):
        return None
    s = non_null_text(df[col])
    if s.empty or s.str.fullmatch(r"-?\d+([.,]\d+)?").mean() > 0.5:
        return None  # colonne numérique, pas des dates
    parsed = s.map(lambda v: to_date(v, dayfirst))
    ok = int(parsed.notna().sum())
    if ok / len(s) < threshold:
        return None
    iso = int(s.str.fullmatch(r"\d{4}-\d{2}-\d{2}").sum())
    reason = f"{pct(ok, len(s))} des valeurs sont des dates : conversion au format AAAA-MM-JJ"
    if ok - iso:
        reason += f" ({ok - iso} dans un autre format, lecture jour/mois)"
    if len(s) - ok:
        reason += f" ; {len(s) - ok} valeur(s) illisibles mises à vide"
    return Decision(reason, {"dayfirst": dayfirst})


def apply_dates(df, col, dayfirst: bool = True):
    df[col] = pd.to_datetime(df[col].map(lambda v: to_date(v, dayfirst) if pd.notna(v) else None), errors="coerce")
    return df


def detect_boolean(df, col):
    if not is_text(df[col]):
        return None
    s = non_null_text(df[col]).str.strip().str.lower()
    if s.empty:
        return None
    if _is_01_flag(col, s):
        return Decision("colonne oui/non codée 0 / 1 : conversion en booléen (1 = vrai)", {"ones": True})
    if not s.isin(TRUE_TOKENS | FALSE_TOKENS).all() or s.nunique() < 2:
        return None
    return Decision(f"valeurs oui/non hétérogènes {sorted(s.unique())} : conversion en booléen")


def apply_boolean(df, col, ones: bool = False):
    true = TRUE_TOKENS | ({"1"} if ones else set())
    df[col] = df[col].map(lambda v: None if pd.isna(v) else str(v).strip().lower() in true).astype("boolean")
    return df


# --------------------------------------------------------------------------- outils métier
def detect_keys(df, col):
    """Clés de jointure texte (ex. customerID) : même casse partout, sinon l'ETL ne relie pas les tables."""
    if not is_text(df[col]) or not is_key(col):
        return None
    s = non_null_text(df[col])
    lettered = s[s.str.contains(r"[A-Za-z]")]
    if lettered.empty:
        return None
    upper = lettered.str.upper() == lettered
    if upper.mean() <= 0.5 or upper.all():  # la majorité est en majuscules : on aligne le reste
        return None
    n = int((~upper).sum())
    return Decision(f"{n} clé(s) de jointure en minuscules (ex. « {lettered[~upper].iloc[0]} ») mises en majuscules comme les autres",
                    {"count": n})


def apply_keys(df, col, **_):
    df[col] = df[col].map(lambda v: v.upper() if isinstance(v, str) else v)
    return df


def detect_percent(df, col):
    s = df[col]
    if not is_number(s) or not PERCENT_NAME.search(col):
        return None
    x = s.dropna().astype(float)
    if x.empty or (x < 0).any() or x.max() > 100 or not (x > 1).any():
        return None
    all_pct = not ((x > 0) & (x < 1)).any()  # aucune fraction : toute la colonne est en %, sinon seules les valeurs > 1
    n = int(len(x) if all_pct else (x > 1).sum())
    return Decision(f"{n} taux écrits en pourcentage (ex. {fnum(x[x > 1].iloc[0])} au lieu de {fnum(x[x > 1].iloc[0] / 100)}) : "
                    "tous exprimés entre 0 et 1", {"all_values": bool(all_pct), "count": n})


def apply_percent(df, col, all_values: bool = False, **_):
    s = df[col].astype("Float64")
    df[col] = s / 100 if all_values else s.where(~(s > 1), s / 100)
    return df


def detect_money(df, col):
    s = df[col]
    if not is_number(s) or pd.api.types.is_integer_dtype(s) or not MONEY_NAME.search(col):
        return None
    x = s.dropna().astype(float)
    n = int(((x * 100 - (x * 100).round()).abs() > 1e-6).sum())
    return Decision(f"{n} montant(s) avec plus de 2 décimales arrondis au centime") if n else None


def apply_money(df, col):
    df[col] = df[col].round(2)
    return df


def _phone(v: str) -> str:
    groups = re.findall(r"\d+", v)
    return ("+" if v.strip().startswith("+") else "") + " ".join(groups) if groups else v


def detect_phone(df, col):
    if not is_text(df[col]) or not PHONE_NAME.search(col):
        return None
    s = non_null_text(df[col])
    n = int((s != s.map(_phone)).sum())
    return Decision(f"{n} numéro(s) au format harmonisé : chiffres groupés par des espaces, sans parenthèses, points ni tirets",
                    {"count": n}) if n else None


def apply_phone(df, col, **_):
    df[col] = df[col].map(lambda v: _phone(v) if isinstance(v, str) else v)
    return df


# ---- conditionnements : « 24 - 12 oz bottles » -> 24 | 12 | oz | bottle | 8 516,5 | ml
UNITS = {"mg": ("g", 0.001), "g": ("g", 1), "gr": ("g", 1), "kg": ("g", 1000), "lb": ("g", 453.592), "lbs": ("g", 453.592),
         "oz": ("g", 28.3495), "ml": ("ml", 1), "cc": ("ml", 1), "cl": ("ml", 10), "dl": ("ml", 100), "l": ("ml", 1000),
         "litre": ("ml", 1000), "litres": ("ml", 1000), "liter": ("ml", 1000), "liters": ("ml", 1000)}
FL_OZ_ML = 29.5735  # once liquide : pour les bouteilles et canettes
CONTAINER_ALIASES = {"pkg": "package", "pkgs": "package", "pk": "package", "pack": "package", "packs": "package",
                     "packages": "package", "boxes": "box", "glasses": "glass", "gift boxes": "gift box", "sausgs": "sausage",
                     "sausages": "sausage", "pcs": "piece", "pc": "piece", "pieces": "piece", "bottles": "bottle",
                     "bouteilles": "bouteille", "boites": "boîte", "boîtes": "boîte", "sachets": "sachet", "paquets": "paquet"}
KNOWN_CONTAINERS = {"bottle", "jar", "package", "box", "bag", "tin", "can", "glass", "piece", "pie", "bar", "round",
                    "gift box", "sausage", "carton", "case", "crate", "tube", "pot", "roll", "sheet", "unit", "bouteille",
                    "boîte", "sac", "sachet", "paquet", "pièce", "unité", "flacon", "canette", "barquette", "tablette"}
_NUM = r"(\d+(?:[.,]\d+)?)"
_UNIT = r"(kg|mg|gr|g|lbs|lb|oz|ml|cl|dl|litres|litre|liters|liter|l|cc)\b"
_CONT = r"([a-zàâçéèêëîïôûùüÿ][a-zàâçéèêëîïôûùüÿ .]*?)"
PACK_PATTERNS = [
    ("n_size", re.compile(rf"^(\d+)\s*[-x*]\s*{_NUM}\s*{_UNIT}\.?\s*{_CONT}?\.?$")),        # 24 - 12 oz bottles
    ("n_x_m", re.compile(rf"^(\d+)\s*{_CONT}\.?\s*x\s*(\d+)\s*{_CONT}\.?$")),               # 10 boxes x 20 bags
    ("size", re.compile(rf"^{_NUM}\s*{_UNIT}\.?\s*(?:per\s+|par\s+|/\s*)?{_CONT}?\.?$")),    # 1 kg pkg. / 750 cc per bottle
    ("n", re.compile(rf"^(\d+)\s+{_CONT}\.?$")),                                             # 36 boxes
]
PACK_COLUMNS = ["nb_unites", "taille_unite", "unite_mesure", "contenant", "quantite_totale", "unite_base"]


def _container(word):
    if not word:
        return None
    w = re.sub(r"\s+", " ", word.strip(" .").lower())
    if w in CONTAINER_ALIASES:
        return CONTAINER_ALIASES[w]
    return w[:-1] if len(w) > 3 and w.endswith("s") and w[:-1] in KNOWN_CONTAINERS else w


def parse_packaging(value) -> dict | None:
    """Décompose un conditionnement en nombre d'unités, taille, unité, contenant et quantité totale (g ou ml)."""
    if not isinstance(value, str):
        return None
    v = re.sub(r"\s+", " ", value.strip().lower().replace("×", "x"))
    v = re.sub(r"(\d)\s*k\b\.?", r"\1 kg", v)  # « 1k pkg. » -> « 1 kg pkg. »
    for kind, pat in PACK_PATTERNS:
        m = pat.match(v)
        if not m:
            continue
        g = m.groups()
        if kind == "n_size":
            n, size, unit, cont = int(g[0]), float(g[1].replace(",", ".")), g[2], _container(g[3])
        elif kind == "n_x_m":
            n, size, unit, cont = int(g[0]) * int(g[2]), None, None, _container(g[3])
        elif kind == "size":
            n, size, unit, cont = 1, float(g[0].replace(",", ".")), g[1], _container(g[2])
        else:
            n, size, unit, cont = int(g[0]), None, None, _container(g[1])
        unit = "ml" if unit == "cc" else unit
        base, total = None, None
        if unit:
            base, factor = UNITS[unit]
            if unit == "oz" and cont in {"bottle", "can", "bouteille", "canette"}:
                base, factor = "ml", FL_OZ_ML
            total = round(n * size * factor, 2)
        return {"nb_unites": n, "taille_unite": size, "unite_mesure": unit, "contenant": cont,
                "quantite_totale": total, "unite_base": base, "meaningful": bool(unit) or cont in KNOWN_CONTAINERS}
    return None


def _pack_names(df, col):
    taken = set(df.columns)
    return {c: (c if c not in taken else f"{col}_{c}") for c in PACK_COLUMNS}


def detect_packaging(df, col):
    """Texte composite du type « 24 - 12 oz bottles » : inexploitable tel quel par un ETL, on le décompose."""
    if not is_text(df[col]) or is_key(col):
        return None
    s = non_null_text(df[col])
    if len(s) < 3:
        return None
    parsed = s.map(parse_packaging)
    ok = parsed.notna()
    meaningful = parsed[ok].map(lambda p: p["meaningful"]).sum()
    hinted = bool(PACKAGING_NAME.search(col))  # nom de colonne évocateur : seuils plus souples
    if ok.mean() < (0.6 if hinted else 0.8) or meaningful / len(s) < (0.3 if hinted else 0.6):
        return None
    unparsed = sorted(set(s[~ok]))[:5]
    names = list(_pack_names(df, col).values())
    reason = (f"{plural(int(ok.sum()), 'conditionnement décomposé', 'conditionnements décomposés')} sur {len(s)} en colonnes "
              f"exploitables : {', '.join(names)} (quantité totale convertie en g ou en ml)")
    if unparsed:
        reason += f" ; non reconnus, laissés tels quels : {unparsed}"
    return Decision(reason, {"columns": names, "count": int(ok.sum()), "unparsed": unparsed})


def apply_packaging(df, col, **_):
    names = _pack_names(df, col)
    parsed = df[col].map(parse_packaging)
    get = lambda k: parsed.map(lambda p: p[k] if p else None)  # noqa: E731
    new = {names["nb_unites"]: get("nb_unites").astype("Int64"),
           names["taille_unite"]: get("taille_unite").astype("Float64"),
           names["unite_mesure"]: get("unite_mesure").astype("string"),
           names["contenant"]: get("contenant").astype("string"),
           names["quantite_totale"]: get("quantite_totale").astype("Float64"),
           names["unite_base"]: get("unite_base").astype("string")}
    pos = list(df.columns).index(col) + 1
    for i, (name, values) in enumerate(new.items()):
        if name in df.columns:
            df[name] = values
        else:
            df.insert(pos + i, name, values)
    return df


# --------------------------------------------------------------------------- contrôles
def detect_negative(df, col):
    if not is_number(df[col]) or not QUANTITY_PATTERN.search(str(col)):
        return None
    n = int((df[col] < 0).sum())
    return Decision(f"{n} valeur(s) négative(s) dans une colonne qui devrait être positive : à vérifier") if n else None


SENTINEL = re.compile(r"-?9{3,}(\.0+)?|-1|-99")


def _diagnose(v: float, median: float) -> tuple[str, bool]:
    """Diagnostic en mots simples pour une valeur extrême : (texte, erreur probable ?)."""
    if SENTINEL.fullmatch(f"{v:g}"):
        return f"valeur de remplissage ({fnum(v)}) : sûrement une erreur de saisie", True
    if median > 0 and v > 0:
        ratio = v / median
        if ratio >= 50:
            return f"{fnum(round(ratio))} fois la valeur habituelle : erreur de saisie probable", True
        if ratio <= 1 / 50:  # une très petite valeur (ex. frais de port de 0,02) est souvent réelle
            return f"{fnum(round(1 / ratio))} fois plus petite que d'habitude : rare mais possible", False
        return (f"{fnum(round(ratio, 1))} fois la valeur habituelle : rare mais possible" if ratio >= 1
                else "beaucoup plus petite que d'habitude : rare mais possible"), False
    return "très éloignée des autres valeurs", False


def detect_outliers(df, col):
    s = df[col]
    if not is_number(s) or is_key(col) or s.dropna().nunique() < 10:
        return None
    x = s.dropna().astype(float)
    log = bool((x > 0).all() and x.skew() > 1)  # montants, prix, quantités : échelle logarithmique
    y = np.log(x) if log else x
    q1, q3 = np.percentile(y, [25, 75])
    lo, hi = q1 - 3 * (q3 - q1), q3 + 3 * (q3 - q1)
    out = (y < lo) | (y > hi)
    if not out.any():
        return None
    normal, median = x[~out], float(x.median())
    low, high = round(float(normal.min()), 2), round(float(normal.max()), 2)
    n_low, n_high = int((x < low).sum()), int((x > high).sum())
    labels = label_columns(df)
    rows = []
    for idx in x[out].sort_values(ascending=False).index[:8]:
        v = float(x[idx])
        text, error = _diagnose(v, median)
        label = " · ".join(f"{df.at[idx, c]}" if not is_key(c) else f"{c} {df.at[idx, c]}" for c in labels)
        rows.append({"row": int(df.at[idx, ROW_ID]) if ROW_ID in df.columns else int(idx), "label": label,
                     "value": round(v, 2), "diagnosis": text, "error": error})
    n_err = sum(r["error"] for r in rows)
    recommended = "empty" if n_err else "keep"
    parts = []
    if n_high:
        parts.append(f"{n_high} au-dessus de {fnum(high)}")
    if n_low:
        parts.append(f"{n_low} en dessous de {fnum(low)}")
    n = n_low + n_high
    reason = (f"{n} valeur{'s' if n > 1 else ''} extrême{'s' if n > 1 else ''} : {' et '.join(parts)} "
              f"(valeur habituelle : {fnum(median)})")
    if n_err:
        reason += f" ; {plural(n_err, 'erreur de saisie probable', 'erreurs de saisie probables')}"
    return Decision(reason, {"low": low, "high": high, "n_low": n_low, "n_high": n_high, "median": round(median, 2),
                             "log_scale": log, "rows": rows, "recommended": recommended,
                             "examples": [r["value"] for r in rows[:6]]})


def apply_cap_outliers(df, col, low=None, high=None, mode="cap", **_):
    """Valeurs extrêmes (seulement après accord de l'utilisateur) : 'empty' les vide, 'cap' les ramène aux bornes."""
    s = df[col]
    if mode == "empty":
        df[col] = s.mask((s < low) | (s > high))
        return df
    if pd.api.types.is_integer_dtype(s):
        low, high = int(np.floor(low)), int(np.ceil(high))
    df[col] = s.clip(lower=low, upper=high)
    return df


def detect_missing(df, col):
    n = int(df[col].isna().sum())
    return Decision(f"{n} valeur(s) manquante(s) ({pct(n, len(df))}) : laissées vides") if n else None


def detect_impute(df, col):
    s = df[col]
    n = int(s.isna().sum())
    if not n or is_key(col) or pd.api.types.is_datetime64_any_dtype(s) or n / len(s) > 0.3:
        return None
    if is_number(s):
        return Decision(f"{n} valeur(s) manquante(s) remplacées par la médiane", {"value": float(s.median()), "method": "médiane"})
    mode = s.mode()
    return Decision(f"{n} valeur(s) manquante(s) remplacées par la valeur la plus fréquente", {"value": mode.iloc[0], "method": "mode"}) if len(mode) else None


def apply_impute(df, col, value=None, method=None):
    df[col] = df[col].fillna(value)
    return df


# --------------------------------------------------------------------------- registre
@dataclass
class Tool:
    name: str
    label: str
    scope: str        # "table" ou "column"
    kind: str         # "fix" (modifie) ou "flag" (signale seulement)
    detect: callable
    apply: callable | None = None
    optional: bool = False  # activé seulement sur demande (ex. imputation)
    ask: bool = False       # appliqué seulement après accord de l'utilisateur (ex. valeurs extrêmes)


TOOLS = [
    # structure
    Tool("clean_column_names", "Rendre les noms de colonnes compatibles ETL", "table", "fix", detect_column_names, apply_column_names),
    Tool("standardize_missing", "Uniformiser les valeurs manquantes", "column", "fix", detect_missing_tokens, apply_missing_tokens),
    Tool("drop_empty_rows", "Supprimer les lignes vides", "table", "fix", detect_empty_rows, apply_empty_rows),
    Tool("drop_empty_columns", "Supprimer les colonnes vides", "table", "fix", detect_empty_columns, apply_empty_columns),
    # texte
    Tool("trim_whitespace", "Supprimer les espaces et caractères invisibles", "column", "fix", detect_whitespace, apply_whitespace),
    Tool("fix_encoding", "Corriger l'encodage des accents", "column", "fix", detect_mojibake, apply_mojibake),
    Tool("normalize_keys", "Harmoniser les clés de jointure", "column", "fix", detect_keys, apply_keys),
    # types
    Tool("cast_numeric", "Convertir en nombre", "column", "fix", detect_numeric, apply_numeric),
    Tool("parse_dates", "Convertir en date", "column", "fix", detect_dates, apply_dates),
    Tool("cast_boolean", "Convertir en booléen", "column", "fix", detect_boolean, apply_boolean),
    # métier
    Tool("normalize_percent", "Exprimer les taux entre 0 et 1", "column", "fix", detect_percent, apply_percent),
    Tool("round_money", "Arrondir les montants au centime", "column", "fix", detect_money, apply_money),
    Tool("normalize_phone", "Harmoniser les numéros de téléphone", "column", "fix", detect_phone, apply_phone),
    Tool("normalize_country", "Standardiser les pays", "column", "fix", detect_country, apply_country),
    Tool("merge_category_variants", "Unifier les variantes d'écriture", "column", "fix", detect_category_variants, apply_category_variants),
    Tool("fix_case", "Corriger les textes en majuscules", "column", "fix", detect_case, apply_case),
    Tool("split_packaging", "Décomposer les conditionnements en colonnes", "column", "fix", detect_packaging, apply_packaging),
    Tool("drop_duplicates", "Supprimer les doublons exacts", "table", "fix", detect_duplicates, apply_duplicates),
    Tool("impute_missing", "Imputer les valeurs manquantes", "column", "fix", detect_impute, apply_impute, optional=True),
    # contrôle
    Tool("flag_duplicate_keys", "Signaler les clés en double", "table", "flag", detect_duplicate_keys),
    Tool("flag_negative", "Signaler les valeurs négatives", "column", "flag", detect_negative),
    Tool("cap_outliers", "Traiter les valeurs extrêmes", "column", "fix", detect_outliers, apply_cap_outliers, ask=True),
    Tool("flag_missing", "Signaler les valeurs manquantes", "column", "flag", detect_missing),
]
