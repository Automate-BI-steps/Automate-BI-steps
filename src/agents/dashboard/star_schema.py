"""Schéma en étoile du modèle Power BI, reconstruit à partir des tables NETTOYÉES (sortie de l'Agent 2).

Pourquoi : l'Agent 4 calcule ses KPI sur une table à plat (15 colonnes) issue de la jointure des
tables nettoyées. Le générateur en faisait le modèle Power BI : toutes les autres colonnes des tables
d'origine disparaissaient (identifiants, contact et ville des clients, titre des employés, détail du
conditionnement des produits, description des catégories...) et chaque « dimension » n'avait qu'une
colonne (DimCountry, DimProductName...).

Ce module construit un vrai schéma en étoile, sans règle propre à Northwind :

  1. clé primaire de chaque table : une colonne « ...ID » unique et jamais vide ;
  2. clés étrangères : une colonne qui porte le nom de la clé primaire d'une autre table et dont les
     valeurs s'y retrouvent (≥ 95 %) ;
  3. table de faits : la plus grande table qui référence d'autres tables sans être référencée
     elle-même (ex. order_details : une ligne par produit commandé) ;
  4. en-têtes de transaction (ex. orders : dates, fret, client, employé, transporteur) : une table
     parente de la table de faits qui porte des dates et ses propres clés étrangères est fusionnée
     dans la table de faits (même grain que l'Agent 4) ;
  5. dimensions : une table par clé étrangère de la table de faits, avec TOUTES ses colonnes ; un
     flocon (ex. products -> categories) est aplati dans la dimension (DimProduct contient la catégorie) ;
  6. colonnes calculées par l'Agent 4 (ex. lineTotal) : reprises ligne à ligne, après vérification
     que la table de faits et la table à plat de l'Agent 4 ont les mêmes lignes dans le même ordre.

`locations` relie chaque colonne de la table à plat de l'Agent 4 à sa place dans l'étoile
(ex. country -> DimCustomer[Country], shipperName -> DimShipper[CompanyName]) : le plan du rapport et
les formules DAX restent écrits sur la table à plat, puis sont traduits vers l'étoile.

Si une étape échoue (pas de clé, lignes non alignées...), `build_star` renvoie None avec la raison :
le générateur garde alors le modèle à plat (aucune donnée inventée).
"""
from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

FK_COVERAGE = 0.95      # part minimale des valeurs d'une clé étrangère retrouvées dans la table parente
MATCH_RATIO = 0.99      # part minimale de lignes égales pour relier une colonne à plat à une colonne de l'étoile
_REF = re.compile(r"('?)([A-Za-z_][A-Za-z0-9_]*)\1\[([^\]]+)\]")


def model_name(column: Any) -> str:
    text = str(column)
    return text[:1].upper() + text[1:]


def singular(name: str) -> str:
    """customers -> Customer, categories -> Category, order_details -> OrderDetail."""
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", name) if p]
    if not parts:
        return "Table"
    last = parts[-1]
    if last.lower().endswith("ies") and len(last) > 4:
        last = last[:-3] + "y"
    elif last.lower().endswith("s") and not last.lower().endswith("ss"):
        last = last[:-1]
    parts[-1] = last
    return "".join(p[:1].upper() + p[1:] for p in parts)


def column_type(series: pd.Series) -> str:
    """Type du modèle tabulaire : int64, double, boolean, dateTime ou string."""
    s = series.dropna()
    if pd.api.types.is_bool_dtype(series) or (len(s) and s.astype(str).str.lower().isin({"true", "false"}).all()):
        return "boolean"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "dateTime"
    if pd.api.types.is_numeric_dtype(series):
        return "int64" if len(s) and (s == s.round()).all() and s.abs().max() < 2**53 else "double"
    name = str(series.name or "").lower()
    if any(h in name for h in ("date", "time", "jour")) and len(s):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            parsed = pd.to_datetime(s.astype(str).head(500), errors="coerce")
        if parsed.notna().mean() >= 0.9:
            return "dateTime"
    return "string"


@dataclass
class StarTable:
    name: str
    frame: pd.DataFrame                  # colonnes déjà sous leur nom de modèle
    key: str | None = None               # clé primaire (dimensions)
    sources: list[str] = field(default_factory=list)
    hidden: list[str] = field(default_factory=list)   # clés étrangères masquées dans la table de faits

    def types(self) -> list[tuple[str, str]]:
        return [(c, column_type(self.frame[c])) for c in self.frame.columns]


@dataclass
class StarSchema:
    fact: StarTable
    dims: list[StarTable]
    relationships: list[dict[str, str]]
    locations: dict[str, tuple[str, str]]          # colonne à plat (nom du modèle) -> (table, colonne)
    notes: list[str] = field(default_factory=list)

    def tables(self) -> list[StarTable]:
        return [self.fact, *self.dims]

    def spec(self, folder: str = "star") -> dict[str, Any]:
        """Description JSON (data_model.json) ; chaque table est écrite dans <folder>/<nom>.csv."""
        def table(t: StarTable) -> dict[str, Any]:
            return {"name": t.name, "file": f"{folder}/{t.name}.csv", "key": t.key, "sources": t.sources,
                    "columns": [list(c) for c in t.types()], "hidden": t.hidden, "rows": int(len(t.frame))}
        return {"method": "tables nettoyées : clés primaires et étrangères détectées, en-tête fusionné, flocons aplatis",
                "fact": table(self.fact), "dimensions": [table(d) for d in self.dims],
                "relationships": self.relationships, "locations": {k: list(v) for k, v in self.locations.items()},
                "notes": self.notes}

    def write(self, out_dir: Path, folder: str = "star") -> dict[str, Any]:
        target = out_dir / folder
        target.mkdir(parents=True, exist_ok=True)
        for t in self.tables():
            frame_for_csv(t.frame).to_csv(target / f"{t.name}.csv", index=False, encoding="utf-8",
                                          date_format="%Y-%m-%d", lineterminator="\n")
        return self.spec(folder)


def frame_for_csv(frame: pd.DataFrame) -> pd.DataFrame:
    """Entiers sans « .0 » (une clé 5.0 ne se lit pas en Int64), dates au format ISO."""
    out = frame.copy()
    for c in out.columns:
        kind = column_type(out[c])
        if kind == "int64" and pd.api.types.is_numeric_dtype(out[c]):
            out[c] = out[c].round().astype("Int64")
        elif kind == "dateTime":
            out[c] = pd.to_datetime(out[c], errors="coerce")
    return out


# --------------------------------------------------------------------------- clés
def read_tables(folder: str | Path) -> dict[str, pd.DataFrame]:
    """Les CSV nettoyés, lus comme l'Agent 4 les lit (même nom de table, même encodage)."""
    from src.agents.semantic_kpi.data import lire_csv, nom_table

    path = Path(folder)
    files = [path] if path.is_file() else sorted(path.glob("*.csv"))
    return {nom_table(f): lire_csv(f) for f in files}


def _is_key_name(column: str) -> bool:
    lower = column.lower()
    return lower.endswith("id") or lower.endswith("_key") or lower == "key"


def primary_key(name: str, frame: pd.DataFrame) -> str | None:
    stem = singular(name).lower()
    candidates = []
    for c in frame.columns:
        s = frame[c]
        if not _is_key_name(str(c)) or s.isna().any() or not s.is_unique:
            continue
        if not pd.api.types.is_numeric_dtype(s) and s.astype(str).str.strip().str.lower().duplicated().any():
            continue  # Power BI ne distingue pas la casse : « alfki » et « ALFKI » seraient deux fois la même clé
        candidates.append(str(c))
    if not candidates:
        return None
    return next((c for c in candidates if c.lower().replace("_", "").startswith(stem)), candidates[0])


def _norm(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return series.astype(float).round(6).astype(str)
    return series.astype(str).str.strip().str.lower()


def foreign_keys(tables: dict[str, pd.DataFrame], keys: dict[str, str | None]) -> dict[str, dict[str, str]]:
    """{table: {colonne: table parente}}."""
    found: dict[str, dict[str, str]] = {}
    by_key = {k.lower(): t for t, k in keys.items() if k}
    for name, frame in tables.items():
        for c in frame.columns:
            parent = by_key.get(str(c).lower())
            if parent is None or parent == name or str(c) == keys.get(name):
                continue
            values = frame[c].dropna()
            if values.empty:
                continue
            parent_values = set(_norm(tables[parent][keys[parent]]))
            if _norm(values).isin(parent_values).mean() >= FK_COVERAGE:
                found.setdefault(name, {})[str(c)] = parent
    return found


def _has_dates(frame: pd.DataFrame) -> bool:
    return any(column_type(frame[c]) == "dateTime" for c in frame.columns)


def _merge(left: pd.DataFrame, right: pd.DataFrame, key: str, prefix: str) -> pd.DataFrame:
    """Jointure gauche sur la clé ; une colonne homonyme de droite est préfixée (ex. Category_description)."""
    right = right.rename(columns={c: f"{prefix}_{c}" for c in right.columns if c != key and c in left.columns})
    merged = left.merge(right, on=key, how="left")
    if len(merged) != len(left):
        raise ValueError(f"la jointure sur {key} multiplie les lignes")
    return merged


# --------------------------------------------------------------------------- étoile
def build_star(tables: dict[str, pd.DataFrame], flat: pd.DataFrame | None = None,
               fact_name: str = "Fact_Trade") -> tuple[StarSchema | None, str | None]:
    """(schéma, raison du refus)."""
    tables = {n: f for n, f in tables.items() if len(f.columns)}
    if len(tables) < 2:
        return None, "une seule table nettoyée : pas d'étoile à construire"
    keys = {n: primary_key(n, f) for n, f in tables.items()}
    fks = foreign_keys(tables, keys)
    referenced = {p for refs in fks.values() for p in refs.values()}
    candidates = [n for n in fks if n not in referenced]
    if not candidates:
        return None, "aucune table de faits (table qui référence les autres sans être référencée)"
    fact_source = max(candidates, key=lambda n: len(tables[n]))
    fact = tables[fact_source].copy()
    sources = [fact_source]
    fact_fks = dict(fks.get(fact_source, {}))
    notes: list[str] = []

    # En-têtes de transaction : parent daté qui a ses propres clés étrangères -> fusionné dans les faits.
    merged = True
    while merged:
        merged = False
        for col, parent in list(fact_fks.items()):
            if parent in sources or not fks.get(parent) or not _has_dates(tables[parent]):
                continue
            try:
                fact = _merge(fact, tables[parent], col, singular(parent))
            except ValueError as exc:
                return None, str(exc)
            sources.append(parent)
            fact_fks.pop(col)
            notes.append(f"{parent} fusionnée dans la table de faits (en-tête de transaction : dates et clés)")
            for c, p in fks[parent].items():
                fact_fks.setdefault(c, p)
            merged = True
            break

    # Dimensions : toutes les colonnes de la table parente, flocons aplatis.
    dims: list[StarTable] = []
    relationships: list[dict[str, str]] = []
    used: set[str] = set()
    for col, parent in fact_fks.items():
        if parent in sources or parent in used:
            continue
        frame, dim_sources, todo = tables[parent].copy(), [parent], dict(fks.get(parent, {}))
        while todo:
            c, p = todo.popitem()
            if p in dim_sources or p in sources:
                continue
            try:
                frame = _merge(frame, tables[p], c, singular(p))
            except ValueError:
                continue
            dim_sources.append(p)
            notes.append(f"{p} aplatie dans la dimension de {parent} (flocon)")
            todo.update({k: v for k, v in fks.get(p, {}).items() if v not in dim_sources})
        name = f"Dim{singular(parent)}"
        frame.columns = [model_name(c) for c in frame.columns]
        dims.append(StarTable(name, frame, model_name(keys[parent]), dim_sources))
        relationships.append({"name": f"{fact_name}_{name}", "fromTable": fact_name, "fromColumn": model_name(col),
                              "toTable": name, "toColumn": model_name(keys[parent])})
        used.add(parent)
    if not dims:
        return None, "aucune dimension reliée à la table de faits"
    fact.columns = [model_name(c) for c in fact.columns]
    star = StarSchema(StarTable(fact_name, fact, None, sources, [r["fromColumn"] for r in relationships]),
                      dims, relationships, {}, notes)
    if flat is not None:
        why = attach_flat(star, flat)
        if why:
            return None, why
    return star, None


def _same(a: pd.Series, b: pd.Series) -> float:
    both = a.notna() | b.notna()
    if not both.any():
        return 1.0
    if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
        equal = (a.astype(float) - b.astype(float)).abs() <= 1e-6 * (1 + b.astype(float).abs())
    else:
        equal = _norm(a.where(a.notna(), "")) == _norm(b.where(b.notna(), ""))
        if "dateTime" in (column_type(a), column_type(b)) or pd.api.types.is_datetime64_any_dtype(a):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                da, db = pd.to_datetime(a, errors="coerce"), pd.to_datetime(b, errors="coerce")
            equal = equal | (da == db)
    return float((equal | (a.isna() & b.isna()))[both].mean())


def attach_flat(star: StarSchema, flat: pd.DataFrame) -> str | None:
    """Relie chaque colonne de la table à plat de l'Agent 4 à l'étoile ; ajoute aux faits ses colonnes calculées.
    Renvoie la raison d'un refus, ou None."""
    fact = star.fact.frame
    if len(flat) != len(fact):
        return f"la table de l'Agent 4 a {len(flat)} lignes, la table de faits {len(fact)}"
    flat = flat.reset_index(drop=True)
    fact = fact.reset_index(drop=True)
    shared = [c for c in flat.columns if model_name(c) in fact.columns]
    if not shared:
        return "aucune colonne commune entre la table de l'Agent 4 et la table de faits"
    for c in shared:
        if _same(flat[c], fact[model_name(c)]) < MATCH_RATIO:
            return f"lignes non alignées entre la table de l'Agent 4 et la table de faits (colonne {c})"
    # Colonne de chaque dimension vue depuis la ligne de faits (via la clé étrangère).
    projected: list[tuple[str, str, pd.Series]] = []
    for rel in star.relationships:
        dim = next(d for d in star.dims if d.name == rel["toTable"])
        lookup = dim.frame.drop_duplicates(dim.key).set_index(dim.key)
        keys_ = fact[rel["fromColumn"]]
        for col in dim.frame.columns:
            if col != dim.key:
                projected.append((dim.name, col, keys_.map(lookup[col])))
    for c in flat.columns:
        name = model_name(c)
        if name in fact.columns:
            star.locations[name] = (star.fact.name, name)
            continue
        match = next(((t, col) for t, col, series in projected
                      if col.lower() == name.lower() and _same(flat[c], series) >= MATCH_RATIO), None)
        match = match or next(((t, col) for t, col, series in projected if _same(flat[c], series) >= MATCH_RATIO), None)
        if match:
            star.locations[name] = match
        else:  # colonne calculée par l'Agent 4 (ex. lineTotal) : reprise telle quelle dans les faits
            fact[name] = flat[c].values
            star.locations[name] = (star.fact.name, name)
            star.notes.append(f"{c} : colonne calculée par l'Agent 4, ajoutée à la table de faits")
    star.fact.frame = fact
    return None


# --------------------------------------------------------------------------- traduction vers l'étoile
def physical_dax(expression: str, fact_table: str, locations: dict[str, Any]) -> str:
    """Fact_Trade[Country] -> DimCustomer[Country] ; les autres références restent inchangées."""
    def sub(m: re.Match) -> str:
        table, column = m.group(2), m.group(3)
        if table.lower() != fact_table.lower():
            return m.group(0)
        target = locations.get(column)
        if not target:
            return m.group(0)
        t, c = target
        return f"{t}[{c}]" if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", t) else f"'{t}'[{c}]"
    out = _REF.sub(sub, expression)
    # Compter les valeurs d'une colonne de dimension compterait toute la dimension (ex. 91 clients au lieu
    # des 89 qui ont commandé) : le comptage est restreint aux lignes de faits.
    def count(m: re.Match) -> str:
        table = m.group(2).strip("'")
        if table.lower() == fact_table.lower():
            return m.group(0)
        return f"CALCULATE({m.group(1)}({m.group(2)}[{m.group(3)}]), {fact_table})"
    return re.sub(r"(?<!CALCULATE\()\b(DISTINCTCOUNT|COUNT)\(\s*('?[A-Za-z_][A-Za-z0-9_]*'?)\[([^\]]+)\]\s*\)", count, out)


def physical_field(table: str, name: str, fact_table: str, locations: dict[str, Any]) -> tuple[str, str]:
    if table.lower() in {fact_table.lower(), "facttrade"} and name in locations:
        t, c = locations[name]
        return t, c
    return table, name
