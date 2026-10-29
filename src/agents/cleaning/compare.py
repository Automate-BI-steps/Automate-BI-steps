"""Comparaison avant / après, simple à lire.

- cell_changes(before, after, col) : valeurs modifiées d'une colonne (utilisé à chaque étape par l'agent) ;
- build_view(...)                  : vue simple du run (tables à traiter, phrases courtes, lignes avant / après,
                                     nouvelles colonnes et contrôles « prêt pour l'ETL ») ;
- write_html_report(path, view)    : la même vue en page HTML autonome ;
- en ligne de commande : python compare.py avant.csv apres.xlsx [--key id] pour comparer deux fichiers quelconques.
"""
from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

import numpy as np
import pandas as pd

try:  # paquet src.agents.cleaning
    from .tools import CODE_NAME, IDENTIFIER, ROW_ID, is_key, is_text, to_number
except ImportError:  # script lancé depuis ce dossier
    from tools import CODE_NAME, IDENTIFIER, ROW_ID, is_key, is_text, to_number


def _norm(v):
    """Forme comparable d'une valeur : '' et NA sont équivalents, 18 et '18.0' aussi, dates au format ISO."""
    if v is None or (not isinstance(v, str) and pd.isna(v)) or (isinstance(v, str) and v.strip() == ""):
        return None
    if isinstance(v, np.datetime64):
        v = pd.Timestamp(v)
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d") if v == v.normalize() else v.isoformat()
    if isinstance(v, (bool, np.bool_)):
        return str(bool(v)).lower()
    if isinstance(v, (int, float, np.number)):
        return float(v)
    s = str(v)
    n = to_number(s) if s.strip().lstrip("-").replace(".", "", 1).isdigit() else None
    return n if n is not None else s


def _display(v) -> str:
    n = _norm(v)
    return "(vide)" if n is None else (f"{n:g}" if isinstance(n, float) else str(v))


def _same(b, a) -> bool:
    """Deux valeurs sont identiques si leur forme comparable ou leur affichage est le même (ex. « Non » et False)."""
    return _norm(b) == _norm(a) or _show(b) == _show(a)


def cell_changes(before: pd.DataFrame, after: pd.DataFrame, col: str, limit: int = 3):
    b = before.set_index(ROW_ID)[col]
    a = after.set_index(ROW_ID)[col].reindex(b.index)
    kept = b.index.isin(after[ROW_ID])
    changed = [(rid, bv, av) for rid, bv, av, k in zip(b.index, b.values, a.values, kept) if k and not _same(bv, av)]
    return len(changed), [{"row": int(r), "avant": _display(bv), "apres": _display(av)} for r, bv, av in changed[:limit]]


# --------------------------------------------------------------------------- vue simple pour l'utilisateur
PLAIN = {  # phrase simple par outil : (singulier, pluriel), {n} = nombre de valeurs ou de lignes
    "clean_column_names": ("{n} nom de colonne rendu compatible ETL", "{n} noms de colonnes rendus compatibles ETL"),
    "standardize_missing": ("{n} case « N/A », « null » ou « - » vidée", "{n} cases « N/A », « null » ou « - » vidées"),
    "drop_empty_rows": ("{n} ligne vide supprimée", "{n} lignes vides supprimées"),
    "drop_empty_columns": ("{n} colonne vide supprimée", "{n} colonnes vides supprimées"),
    "trim_whitespace": ("{n} texte avec des espaces en trop ou invisibles nettoyé", "{n} textes avec des espaces en trop ou invisibles nettoyés"),
    "normalize_keys": ("{n} clé de jointure mise en majuscules", "{n} clés de jointure mises en majuscules"),
    "fix_encoding": ("{n} accent mal affiché réparé", "{n} accents mal affichés réparés"),
    "cast_numeric": ("{n} nombre remis au bon format", "{n} nombres remis au bon format"),
    "parse_dates": ("{n} date remise au format AAAA-MM-JJ", "{n} dates remises au format AAAA-MM-JJ"),
    "cast_boolean": ("{n} valeur oui / non uniformisée", "{n} valeurs oui / non uniformisées"),
    "normalize_percent": ("{n} taux ramené entre 0 et 1 (ex. 15 devient 0,15)", "{n} taux ramenés entre 0 et 1 (ex. 15 devient 0,15)"),
    "round_money": ("{n} montant arrondi au centime", "{n} montants arrondis au centime"),
    "normalize_phone": ("{n} numéro de téléphone harmonisé", "{n} numéros de téléphone harmonisés"),
    "fix_case": ("{n} texte en MAJUSCULES remis en casse normale", "{n} textes en MAJUSCULES remis en casse normale"),
    "split_packaging": ("{n} conditionnement décomposé en colonnes exploitables", "{n} conditionnements décomposés en colonnes exploitables"),
    "normalize_country": ("{n} nom de pays harmonisé", "{n} noms de pays harmonisés"),
    "merge_category_variants": ("{n} valeur écrite autrement unifiée", "{n} valeurs écrites de plusieurs façons unifiées"),
    "drop_duplicates": ("{n} ligne en double supprimée", "{n} lignes en double supprimées"),
    "impute_missing": ("{n} case vide complétée", "{n} cases vides complétées"),
    "cap_outliers": ("{n} valeur extrême ramenée dans la plage habituelle", "{n} valeurs extrêmes ramenées dans la plage habituelle"),
    "cap_outliers:empty": ("{n} valeur aberrante mise à vide", "{n} valeurs aberrantes mises à vide"),
}
REMOVE_REASON = {"drop_empty_rows": "ligne vide", "drop_duplicates": "doublon"}


def _show(v) -> str:
    """Valeur affichable : espaces invisibles rendus visibles (·), vide explicite, nombres sans '.0'."""
    if isinstance(v, (bool, np.bool_)):
        return "Oui" if v else "Non"
    n = _norm(v)
    if n is None:
        return "(vide)"
    if isinstance(n, float):
        return f"{n:.10g}"
    if isinstance(v, str):
        return re.sub(r"^ +| +$| {2,}", lambda m: "·" * len(m.group()), v)
    return str(n)


def plain_actions(steps: list[dict]) -> list[dict]:
    """Regroupe les étapes appliquées par outil et les traduit en phrases courtes."""
    grouped: dict[str, dict] = {}
    for st in steps:
        if st["status"] != "applied":
            continue
        n = len(st["params"].get("columns", [])) if st["tool"] == "drop_empty_columns" else st["values_changed"]
        if not n:
            continue  # changement de type sans valeur modifiée : invisible pour l'utilisateur
        key = st["tool"] + (":empty" if st["params"].get("mode") == "empty" else "")
        g = grouped.setdefault(key, {"tool": st["tool"], "count": 0, "columns": []})
        g["count"] += n
        if st["column"] and st["column"] not in g["columns"]:
            g["columns"].append(st["column"])
    def text(tool, n):
        one, many = PLAIN.get(tool, (tool, tool))
        return (one if n == 1 else many).format(n=n)
    return [{**g, "text": text(t, g["count"])} for t, g in grouped.items()]


def _kind(s: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(s):
        return "oui/non"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "date"
    if pd.api.types.is_numeric_dtype(s):
        return "nombre"
    return "texte"


def primary_key(df: pd.DataFrame) -> list[str]:
    """Clé de la table : première colonne clé unique, sinon les deux premières clés prises ensemble."""
    keys = [c for c in df.columns if c != ROW_ID and is_key(c)]
    for k in keys:
        if df[k].notna().all() and df[k].is_unique:
            return [k]
    if len(keys) >= 2 and df[keys[:2]].notna().all(axis=None) and not df.duplicated(subset=keys[:2]).any():
        return keys[:2]
    return []


DATE_START = re.compile(r"(order|command|creat|start|d[eé]but|emission|facture)", re.IGNORECASE)
DATE_END = re.compile(r"(ship|livr|deliver|end|fin|exp[eé]di)", re.IGNORECASE)


def etl_checks(name: str, df: pd.DataFrame, keys_of: dict[str, list[str]], tables: dict[str, pd.DataFrame]) -> list[dict]:
    """Contrôles « prêt pour l'ETL » sur la table nettoyée. ok : True (bon), False (à reprendre), None (information)."""
    cols = [c for c in df.columns if c != ROW_ID]
    out = []
    bad = [c for c in cols if not IDENTIFIER.fullmatch(str(c))]
    out.append({"label": "Noms de colonnes utilisables", "ok": not bad,
                "detail": "sans espaces ni caractères spéciaux" if not bad else "à renommer : " + ", ".join(bad)})

    kinds: dict[str, list[str]] = {}
    for c in cols:
        kinds.setdefault(_kind(df[c]), []).append(c)
    left = [c for c in kinds.get("texte", []) if not CODE_NAME.search(c)
            and df[c].dropna().astype(str).map(to_number).notna().mean() >= 0.9 and df[c].notna().any()]
    order = ["nombre", "date", "oui/non", "texte"]
    detail = " · ".join(f"{len(kinds[k])} {k}{'s' if len(kinds[k]) > 1 and k != 'oui/non' else ''}" for k in order if k in kinds)
    out.append({"label": "Chaque colonne a le bon type", "ok": not left,
                "detail": detail if not left else "nombres restés en texte : " + ", ".join(left), "types": kinds})

    pk = keys_of.get(name) or []
    if pk:
        out.append({"label": "Clé unique", "ok": True, "detail": " + ".join(pk) + " identifie chaque ligne"})
    elif any(is_key(c) for c in cols):
        k = next(c for c in cols if is_key(c))
        n = int(df[k].duplicated().sum())
        out.append({"label": "Clé unique", "ok": False, "detail": f"{n} valeur(s) de {k} en double ou vide(s)"})
    else:
        out.append({"label": "Clé unique", "ok": None, "detail": "aucune colonne d'identifiant (…ID, code)"})

    n_dup = int(df.duplicated(subset=cols).sum())
    out.append({"label": "Aucune ligne en double", "ok": n_dup == 0, "detail": "" if not n_dup else f"{n_dup} ligne(s) en double"})

    for other, okeys in keys_of.items():
        if other == name or len(okeys) != 1 or okeys[0] not in df.columns or okeys == pk:
            continue
        k = okeys[0]
        ref = set(_norm(v) for v in tables[other][k].dropna())
        vals = df[k].dropna()
        missing = sorted({str(v) for v in vals if _norm(v) not in ref})
        n_miss = int(sum(_norm(v) not in ref for v in vals))
        out.append({"label": f"Lien {k} vers {other}", "ok": n_miss == 0,
                    "detail": "toutes les valeurs existent" if not n_miss
                    else f"{n_miss} ligne(s) avec un {k} inconnu (ex. {', '.join(missing[:3])})"})

    dates = kinds.get("date", [])
    for a in [c for c in dates if DATE_START.search(c)]:
        for b in [c for c in dates if DATE_END.search(c) and c != a]:
            n = int((df[b] < df[a]).sum())
            out.append({"label": f"{b} après {a}", "ok": n == 0,
                        "detail": "dates cohérentes" if not n else f"{n} ligne(s) où {b} est avant {a}"})
    return out


def derived_view(after: pd.DataFrame, steps: list[dict], max_rows: int = 8) -> list[dict]:
    """Nouvelles colonnes créées par l'agent (conditionnement décomposé), avec des exemples variés."""
    out = []
    for st in steps:
        if st["tool"] != "split_packaging" or st["status"] != "applied" or st["column"] not in after.columns:
            continue
        src, new = st["column"], [c for c in st["params"]["columns"] if c in after.columns]
        sub = after[[src] + new].dropna(subset=[src]).drop_duplicates(subset=[src])
        shape = sub[new].isna().astype(str).agg("".join, axis=1) + sub[new[2]].astype(str) + sub[new[3]].astype(str)
        pick = sub.loc[shape.drop_duplicates().index].head(max_rows)
        out.append({"source": src, "columns": new, "count": st["values_changed"],
                    "unparsed": st["params"].get("unparsed", []),
                    "rows": [[_show(v) for v in r] for r in pick.itertuples(index=False)]})
    return out


def table_view(name: str, source: str, before: pd.DataFrame, after: pd.DataFrame, steps: list[dict],
               llm: dict | None = None, checks: list[dict] | None = None, max_rows: int = 12, max_cols: int = 7) -> dict:
    renamed = {}
    for st in steps:
        if st["tool"] == "clean_column_names" and st["status"] == "applied":
            renamed.update(st["params"].get("mapping", {}))
    before = before.rename(columns=renamed)
    derived = derived_view(after, steps)
    derived_cols = {c for d in derived for c in d["columns"]}
    cols_b = [c for c in before.columns if c != ROW_ID]
    cols_a = [c for c in after.columns if c != ROW_ID]
    b = before.set_index(ROW_ID)
    a = after.set_index(ROW_ID)
    kept = b.index.intersection(a.index)
    changed_cols, changed_rows, n_cells = [], [], 0
    for c in [c for c in cols_b if c in cols_a]:
        diff = [r for r, x, y in zip(kept, b[c].reindex(kept).values, a[c].reindex(kept).values) if not _same(x, y)]
        if diff:
            changed_cols.append(c)
            n_cells += len(diff)
            changed_rows += diff
    changed_rows = sorted(set(changed_rows))

    key = next((c for c in cols_b if is_key(c)), cols_b[0] if cols_b else None)
    shown = ([key] if key and key not in changed_cols else []) + changed_cols[:max_cols]
    grid_rows = []
    for r in changed_rows[:max_rows]:
        cells = []
        for c in shown:
            bv = b.at[r, c]
            av = a.at[r, c] if c in a.columns else None
            ch = c in changed_cols and not _same(bv, av)
            cells.append({"before": _show(bv), "after": _show(av), "changed": ch})
        grid_rows.append({"row": int(r), "cells": cells})

    reason_of = {}
    for st in steps:
        if st["status"] == "applied" and st["tool"] in REMOVE_REASON:
            for ex in st.get("examples") or []:
                for r in ex.get("lignes_supprimées", []):
                    reason_of[int(r)] = REMOVE_REASON[st["tool"]]
    removed_ids = list(b.index.difference(a.index))
    preview_cols = cols_b[:5]
    removed = [{"row": int(r), "reason": reason_of.get(int(r), "supprimée"),
                "values": [_show(b.at[r, c]) for c in preview_cols]} for r in removed_ids[:max_rows]]

    refused = [{"label": st["label"], "column": st["column"], "reason": st["reason"].split("| LLM :")[-1].strip()}
               for st in steps if st["status"] == "rejected"]
    outliers = [{"column": st["column"], "status": st["status"], "count": st["values_changed"],
                 "text": st["reason"].split(" | ")[0]} for st in steps if st["tool"] == "cap_outliers"]
    actions = plain_actions(steps)
    removed_cols = [c for c in cols_b if c not in cols_a]
    added_cols = [c for c in cols_a if c not in cols_b]
    checks = checks or []
    return {
        "name": name, "source": source, "rows_before": len(before), "rows_after": len(after),
        "cols_before": len(cols_b), "cols_after": len(cols_a), "changed_cells": n_cells,
        "changed_rows": len(changed_rows), "removed_count": len(removed_ids), "removed_columns": removed_cols,
        "added_columns": added_cols, "renamed": [{"from": k, "to": v} for k, v in renamed.items()],
        "needs_work": bool(n_cells or removed_ids or removed_cols or added_cols or renamed
                           or any(c["ok"] is False for c in checks)),
        "etl_ready": all(c["ok"] is not False for c in checks),
        "actions": actions, "llm_refused": refused, "outliers": outliers, "checks": checks, "derived": derived,
        "llm": ({"model": llm.get("model"), "analysis": llm.get("analysis"), "role": llm.get("table_role"),
                 "error": llm.get("error")} if llm else None),
        "grid": {"columns": shown, "changed_columns": changed_cols, "rows": grid_rows,
                 "more": max(0, len(changed_rows) - max_rows)},
        "removed": {"columns": preview_cols, "rows": removed, "more": max(0, len(removed_ids) - max_rows)},
    }


def build_view(summary: dict, before: dict[str, pd.DataFrame], after: dict[str, pd.DataFrame],
               sources: dict[str, str]) -> dict:
    keys_of = {n: primary_key(df) for n, df in after.items()}
    views = [table_view(n, sources.get(n, ""), before[n], after[n],
                        [s for s in summary["steps"] if s["table"] == n], summary["tables"].get(n, {}).get("llm"),
                        etl_checks(n, after[n], keys_of, after))
             for n in before]
    todo = [v for v in views if v["needs_work"]]
    return {
        "run_id": summary["run_id"], "finished_at": summary["finished_at"], "planner": summary["planner"],
        "files": [Path(i["file"]).name for i in summary["inputs"]],
        "totals": {"tables": len(views), "tables_to_process": len(todo),
                   "corrections": sum(a["count"] for v in todo for a in v["actions"]),
                   "rows_removed": sum(v["removed_count"] for v in views),
                   "values_changed": sum(v["changed_cells"] for v in views),
                   "columns_added": sum(len(v["added_columns"]) for v in views),
                   "etl_ready": sum(v["etl_ready"] for v in views)},
        "tables": todo,
        "clean_tables": [v["name"] for v in views if not v["needs_work"]],
    }


CSS = """
:root{--text:#1f2328;--muted:#656d76;--border:#d8dee4;--accent:#5b3fd6;--soft:#f6f7f9;--ok:#1a7f37;--ok-bg:#dafbe1;--bad:#cf222e;--bad-bg:#ffebe9}
body{font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;padding:24px;color:var(--text);background:#fff;max-width:1100px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 6px}.muted{color:var(--muted)}
.kpis{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}.kpi{border:1px solid var(--border);border-radius:8px;padding:10px 14px;min-width:150px}
.kpi b{display:block;font-size:22px}.kpi span{color:var(--muted);font-size:12px}
ul.done{list-style:none;padding:0;margin:8px 0}ul.done li{padding:4px 0}ul.done li:before{content:"✓ ";color:var(--ok);font-weight:700}
.llm{background:#efeafe;border-left:4px solid var(--accent);padding:8px 12px;border-radius:6px;margin:8px 0}
table{border-collapse:collapse;margin:8px 0;font-size:13px;width:100%}th,td{border-bottom:1px solid var(--border);padding:6px 8px;text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600}td.ch{background:var(--ok-bg)}.old{display:block;color:var(--bad);text-decoration:line-through;font-size:12px}
.new{font-weight:600}.chip{display:inline-block;padding:1px 8px;border-radius:999px;background:var(--bad-bg);color:var(--bad);font-size:12px}
ul.checks{list-style:none;padding:0;margin:8px 0}ul.checks li{padding:3px 0}ul.checks .ok:before{content:"✓ ";color:var(--ok);font-weight:700}
ul.checks .ko:before{content:"✗ ";color:var(--bad);font-weight:700}ul.checks .info:before{content:"i ";color:var(--muted);font-weight:700}
th.src,td.src{background:var(--soft)}
"""


def write_html_report(path: Path, view: dict) -> None:
    """Rapport autonome et lisible : ce qui a été corrigé, puis les lignes modifiées avant -> après."""
    e = html.escape
    t = view["totals"]
    pl = view.get("planner") or {}
    parts = [f"<!doctype html><html lang='fr'><meta charset='utf-8'><title>Nettoyage : avant / après</title><style>{CSS}</style>",
             "<h1>Nettoyage : avant / après</h1>",
             f"<p class='muted'>{e(', '.join(view['files']))} · {e(view['finished_at'])}"
             + (f" · analyse par {e(pl['model'])}" if pl.get("model") else "") + "</p>",
             "<div class='kpis'>"
             f"<div class='kpi'><b>{t['tables_to_process']} / {t['tables']}</b><span>tables traitées</span></div>"
             f"<div class='kpi'><b>{t['corrections']}</b><span>corrections</span></div>"
             f"<div class='kpi'><b>{t['rows_removed']}</b><span>lignes supprimées</span></div>"
             f"<div class='kpi'><b>{t.get('etl_ready', 0)} / {t['tables']}</b><span>tables prêtes pour l'ETL</span></div></div>"]
    if view["clean_tables"]:
        parts.append(f"<p class='muted'>Déjà propres, rien à faire : {e(', '.join(view['clean_tables']))}</p>")
    for tb in view["tables"]:
        parts.append(f"<h2>{e(tb['name'])}</h2><p class='muted'>{e(tb['source'])} · {tb['rows_before']} → {tb['rows_after']} lignes</p>")
        if tb["llm"] and tb["llm"].get("analysis"):
            parts.append(f"<div class='llm'><b>Analyse du LLM</b> : {e(tb['llm']['analysis'])}</div>")
        parts.append("<ul class='done'>" + "".join(
            f"<li>{e(a['text'])}" + (f" <span class='muted'>({e(', '.join(a['columns']))})</span>" if a["columns"] else "") + "</li>"
            for a in tb["actions"]) + "</ul>")
        if tb.get("renamed"):
            parts.append("<p class='muted'>Colonnes renommées : " + e(", ".join(f"{r['from']} → {r['to']}" for r in tb["renamed"])) + "</p>")
        for d in tb.get("derived", []):
            parts.append(f"<p><b>Nouvelles colonnes à partir de {e(d['source'])}</b></p><table><tr><th class='src'>{e(d['source'])}</th>"
                         + "".join(f"<th>{e(c)}</th>" for c in d["columns"]) + "</tr>")
            for row in d["rows"]:
                parts.append(f"<tr><td class='src'>{e(row[0])}</td>" + "".join(f"<td>{e(v)}</td>" for v in row[1:]) + "</tr>")
            parts.append("</table>")
        if tb.get("checks"):
            cls = {True: "ok", False: "ko", None: "info"}
            parts.append("<p><b>Prêt pour l'ETL</b></p><ul class='checks'>" + "".join(
                f"<li class='{cls[c['ok']]}'>{e(c['label'])}" + (f" <span class='muted'>: {e(c['detail'])}</span>" if c["detail"] else "") + "</li>"
                for c in tb["checks"]) + "</ul>")
        for o in tb["outliers"]:
            if o["status"] == "kept":
                parts.append(f"<p class='muted'>Valeurs extrêmes gardées telles quelles ({e(o['column'])}) : {e(o['text'])}</p>")
        for r in tb["llm_refused"]:
            parts.append(f"<p class='muted'>Non appliqué sur conseil du LLM : {e(r['label'])} ({e(r['column'] or 'table')}) : {e(r['reason'])}</p>")
        g = tb["grid"]
        if g["rows"]:
            parts.append("<table><tr><th>Ligne</th>" + "".join(f"<th>{e(str(c))}</th>" for c in g["columns"]) + "</tr>")
            for row in g["rows"]:
                tds = "".join(
                    f"<td class='ch'><span class='old'>{e(c['before'])}</span><span class='new'>{e(c['after'])}</span></td>"
                    if c["changed"] else f"<td>{e(c['after'])}</td>" for c in row["cells"])
                parts.append(f"<tr><td class='muted'>{row['row']}</td>{tds}</tr>")
            parts.append("</table>")
            if g["more"]:
                parts.append(f"<p class='muted'>… et {g['more']} autre{'s' if g['more'] > 1 else ''} ligne{'s' if g['more'] > 1 else ''} modifiée{'s' if g['more'] > 1 else ''} (toutes sont dans les tables téléchargées).</p>")
        rm = tb["removed"]
        if rm["rows"]:
            parts.append("<p><b>Lignes supprimées</b></p><table><tr><th>Ligne</th><th>Raison</th>"
                         + "".join(f"<th>{e(str(c))}</th>" for c in rm["columns"]) + "</tr>")
            for row in rm["rows"]:
                parts.append(f"<tr><td class='muted'>{row['row']}</td><td><span class='chip'>{e(row['reason'])}</span></td>"
                             + "".join(f"<td>{e(v)}</td>" for v in row["values"]) + "</tr>")
            parts.append("</table>")
    Path(path).write_text("\n".join(parts), encoding="utf-8")


# --------------------------------------------------------------------------- comparaison libre de deux fichiers
def _read_any(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
        return pd.read_excel(path, dtype=str)
    from agent import read_csv
    return read_csv(path)[0]


def main():
    p = argparse.ArgumentParser(description="Compare deux fichiers CSV ou Excel (avant / après)")
    p.add_argument("before", type=Path)
    p.add_argument("after", type=Path)
    p.add_argument("--key", help="colonne d'alignement des lignes (sinon : position)")
    p.add_argument("--out", type=Path, default=Path("comparaison.html"))
    a = p.parse_args()
    b, af = _read_any(a.before), _read_any(a.after)
    if a.key:
        ids = {k: i for i, k in enumerate(b[a.key].astype(str), start=1)}
        b[ROW_ID] = range(1, len(b) + 1)
        af[ROW_ID] = af[a.key].astype(str).map(ids).fillna(-1).astype(int)
    else:
        b[ROW_ID] = range(1, len(b) + 1)
        af[ROW_ID] = range(1, len(af) + 1)
    tv = table_view(a.before.stem, f"{a.before.name} -> {a.after.name}", b, af, [])
    view = {"files": [a.before.name, a.after.name], "finished_at": "", "planner": {},
            "totals": {"tables": 1, "tables_to_process": int(tv["needs_work"]), "corrections": tv["changed_cells"],
                       "rows_removed": tv["removed_count"], "values_changed": tv["changed_cells"]},
            "tables": [tv] if tv["needs_work"] else [], "clean_tables": [] if tv["needs_work"] else [tv["name"]]}
    write_html_report(a.out, view)
    print(f"{tv['changed_cells']} valeur(s) différente(s), {tv['removed_count']} ligne(s) absente(s) -> {a.out}")


if __name__ == "__main__":
    main()
