"""Détection des relations clé étrangère -> clé primaire entre tables."""

import re

import pandas as pd

from .column_profiler import is_id_like, name_tokens
from .readers import null_mask

# Mots qui signalent une référence vers une autre ligne, même sans "id".
REFERENCE_TOKENS = {"reports", "parent", "manager", "ref", "owner", "supervisor", "boss"}
MIN_CONTAINMENT = 0.8
_NUM_RE = re.compile(r"^[+-]?\d+(\.0+)?$")


def _normalize(series: pd.Series) -> pd.Series:
    """Rend comparables '5', '5.0' et ' 5 '."""
    s = series.str.strip()
    return s.where(~s.str.match(_NUM_RE.pattern), s.str.replace(r"\.0+$", "", regex=True))


def primary_key(table: dict) -> str | None:
    """Clé de référence de la table, utilisée comme cible des clés étrangères.

    Un identifiant en première colonne est retenu même s'il contient des doublons
    (ils sont signalés par une issue `duplicate_keys`), sauf s'il fait partie
    d'une clé composée (ex. order_details : orderID + productID) ; sinon, la
    première clé simple candidate de type identifiant.
    """
    first = table["columns"][0]["column_name"] if table["columns"] else None
    if first and is_id_like(first):
        in_composite = any(len(c) > 1 and first in c for c in table["primary_key_candidates"])
        return None if in_composite else first
    for cand in table["primary_key_candidates"]:
        if len(cand) == 1 and is_id_like(cand[0]):
            return cand[0]
    return None


def _is_reference_like(column: str) -> bool:
    return is_id_like(column) or bool(set(name_tokens(column)) & REFERENCE_TOKENS)


def detect_relationships(tables: list[dict], frames: dict[str, pd.DataFrame]) -> list[dict]:
    pks = {}
    for t in tables:
        pk = primary_key(t)
        if pk:
            values = _normalize(frames[t["table_name"]][pk])
            pks[t["table_name"]] = (pk, set(values))

    relationships = []
    for table in tables:
        name = table["table_name"]
        df = frames[name]
        own_pk = primary_key(table)
        for col in table["columns"]:
            column = col["column_name"]
            if column == own_pk:
                continue
            nulls = null_mask(df[column])
            fk_values = _normalize(df[column][~nulls])
            if fk_values.empty:
                continue
            distinct = set(fk_values)

            candidates = []
            for target, (pk, pk_values) in pks.items():
                containment = len(distinct & pk_values) / len(distinct)
                name_match = column.lower() == pk.lower() and target != name
                if name_match or (_is_reference_like(column) and containment >= MIN_CONTAINMENT):
                    candidates.append((name_match, containment, target == name, target, pk))
            if not candidates:
                continue
            # Priorité : correspondance de nom, puis recouvrement, puis auto-référence.
            name_match, containment, _, target, pk = max(candidates)
            if not name_match and len(distinct) < 2:
                continue  # une seule valeur : recouvrement non significatif

            pk_values = pks[target][1]
            orphan_mask = ~fk_values.isin(pk_values)
            if name_match and containment >= MIN_CONTAINMENT:
                method, confidence = "both", 0.95
            elif name_match:
                method, confidence = "name_match", 0.7
            else:
                method, confidence = "value_overlap", 0.6

            relationships.append({
                "relationship_id": f"REL-{len(relationships) + 1:03d}",
                "from_table": name,
                "from_column": column,
                "to_table": target,
                "to_column": pk,
                "cardinality": "one_to_one" if fk_values.is_unique else "many_to_one",
                "match_pct": round(100 * (1 - orphan_mask.mean()), 2),
                "orphan_count": int(orphan_mask.sum()),
                "orphan_samples": sorted(set(fk_values[orphan_mask]))[:5],
                "null_count": int(nulls.sum()),
                "detection_method": method,
                "confidence": confidence,
            })
            col["semantic_role"] = "foreign_key"
    return relationships
