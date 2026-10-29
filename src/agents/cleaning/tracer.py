"""Agent de traçage : historise chaque étape de nettoyage.

Pour chaque exécution (run), il conserve dans history/ :
  - cleaning_history.jsonl : journal append-only de toutes les étapes de tous les runs ;
  - runs_index.csv         : une ligne par run (date, fichiers, nb d'étapes, statut) ;
  - runs/<run_id>/before/  : les tables telles que lues (instantané avant) ;
  - runs/<run_id>/after/   : les tables nettoyées (instantané après) ;
  - runs/<run_id>/trace.json et trace.md : le détail des décisions, lisible par l'agent XAI et par un humain.
Rien n'est jamais écrasé : chaque run a son dossier, ce qui permet de revenir à n'importe quelle version.
"""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

try:  # paquet src.agents.cleaning
    from .tools import ROW_ID
except ImportError:  # script lancé depuis ce dossier
    from tools import ROW_ID


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _jsonable(v):
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_jsonable(x) for x in v]
    if hasattr(v, "item"):
        return v.item()
    if v is pd.NA or (isinstance(v, float) and pd.isna(v)):
        return None
    return v if isinstance(v, (str, int, float, bool)) or v is None else str(v)


def write_table(df: pd.DataFrame, path: Path) -> None:
    df.drop(columns=[ROW_ID], errors="ignore").to_csv(path, index=False, encoding="utf-8", date_format="%Y-%m-%d")


class TraceAgent:
    def __init__(self, history_dir: Path):
        self.history_dir = Path(history_dir)
        self.run_id = datetime.now(timezone.utc).strftime("clean_%Y%m%dT%H%M%S%fZ")
        self.run_dir = self.history_dir / "runs" / self.run_id
        (self.run_dir / "before").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "after").mkdir(parents=True, exist_ok=True)
        self.inputs: list[dict] = []
        self.tables: dict[str, dict] = {}
        self.steps: list[dict] = []
        self.planner = {"mode": "rules", "model": None, "warning": None}
        self.profiling: dict | None = None  # rapport de profilage reçu en entrée (run_id, nb de problèmes, score)

    def set_planner(self, mode: str, model: str | None, warning: str | None) -> None:
        self.planner = {"mode": mode, "model": model, "warning": warning}

    def record_llm(self, table: str, model: str, plan: dict | None, n_candidates: int, error: str | None = None) -> None:
        """Conserve l'analyse du LLM pour la table (ou l'erreur qui a forcé le retour aux règles)."""
        entry = {"model": model, "candidates": n_candidates, "timestamp": _now()}
        if plan is None:
            entry["error"] = error
        else:
            entry.update({"analysis": plan["analysis"], "table_role": plan["table_role"],
                          "recommendations": plan["recommendations"],
                          "verdicts": {str(k): {"accept": v[0], "reason": v[1]} for k, v in plan["verdicts"].items()}})
        self.tables.setdefault(table, {})["llm"] = entry

    # ---- entrées
    def record_input(self, path: Path, tables: list[str], encoding: str | None, delimiter: str | None) -> None:
        self.inputs.append({"file": str(path), "sha256": sha256(path), "encoding": encoding,
                            "delimiter": delimiter, "tables": tables})

    def snapshot_before(self, name: str, df: pd.DataFrame, source: str) -> None:
        write_table(df, self.run_dir / "before" / f"{name}.csv")
        self.tables.setdefault(name, {}).update({"source": source, "rows_before": len(df), "cols_before": len(df.columns) - 1})

    # ---- étapes
    def log_step(self, table: str, tool, column: str | None, reason: str, params: dict,
                 rows_before: int, rows_after: int, changed: int, examples: list, status: str) -> dict:
        step = {
            "run_id": self.run_id, "step": len(self.steps) + 1, "timestamp": _now(), "table": table,
            "tool": tool.name, "label": tool.label, "kind": tool.kind, "column": column, "status": status,
            "reason": reason, "params": _jsonable(params), "rows_before": rows_before, "rows_after": rows_after,
            "values_changed": changed, "examples": _jsonable(examples),
        }
        self.steps.append(step)
        with open(self.history_dir / "cleaning_history.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(step, ensure_ascii=False) + "\n")
        return step

    def snapshot_after(self, name: str, df: pd.DataFrame, output_dir: Path) -> None:
        write_table(df, self.run_dir / "after" / f"{name}.csv")
        write_table(df, Path(output_dir) / f"{name}.csv")
        self.tables[name].update({"rows_after": len(df), "cols_after": len(df.columns) - 1})

    # ---- clôture
    def summary(self) -> dict:
        flags = [s for s in self.steps if s["status"] == "flagged"]
        return {
            "run_id": self.run_id, "finished_at": _now(), "inputs": self.inputs, "tables": self.tables,
            "planner": self.planner, "profiling": self.profiling,
            "steps_applied": sum(s["status"] == "applied" for s in self.steps),
            "steps_rejected": sum(s["status"] == "rejected" for s in self.steps),
            "issues_flagged": len(flags),
            "status": "WARN" if flags else "OK",
            "needs_human_validation": [f"{s['table']}.{s['column'] or '*'} : {s['reason']}" for s in flags],
            "steps": self.steps,
        }

    def close(self) -> dict:
        summary = self.summary()
        (self.run_dir / "trace.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        (self.run_dir / "trace.md").write_text(self._markdown(summary), encoding="utf-8")
        index = self.history_dir / "runs_index.csv"
        new = not index.exists()
        with open(index, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["run_id", "finished_at", "files", "tables", "steps_applied", "issues_flagged", "status"])
            w.writerow([self.run_id, summary["finished_at"], " | ".join(Path(i["file"]).name for i in self.inputs),
                        len(self.tables), summary["steps_applied"], summary["issues_flagged"], summary["status"]])
        return summary

    @staticmethod
    def _markdown(s: dict) -> str:
        out = [f"# Trace de nettoyage {s['run_id']}", "",
               f"Statut : **{s['status']}** · {s['steps_applied']} correction(s) appliquée(s) · {s['steps_rejected']} refusée(s) par le LLM · {s['issues_flagged']} point(s) signalé(s)", "",
               f"Planificateur : {s['planner']['mode']}" + (f" · modèle `{s['planner']['model']}`" if s['planner']['model'] else "")
               + (f" · {s['planner']['warning']}" if s['planner']['warning'] else ""), "",
               "## Fichiers lus", ""]
        out += [f"- `{Path(i['file']).name}` ({i['encoding'] or 'Excel'}{', séparateur ' + repr(i['delimiter']) if i['delimiter'] else ''}) : tables {i['tables']} · sha256 {i['sha256'][:12]}…" for i in s["inputs"]]
        for name, t in s["tables"].items():
            out += ["", f"## Table `{name}`", "",
                    f"{t['rows_before']} -> {t.get('rows_after', '?')} lignes, {t['cols_before']} -> {t.get('cols_after', '?')} colonnes", ""]
            llm = t.get("llm")
            if llm and llm.get("error"):
                out += [f"> LLM ({llm['model']}) indisponible pour cette table, règles utilisées : {llm['error']}", ""]
            elif llm:
                out += [f"**Analyse LLM** ({llm['model']}, rôle : {llm['table_role']}) : {llm['analysis']}", ""]
                out += [f"- Recommandation : {r}" for r in llm["recommendations"]] + ([""] if llm["recommendations"] else [])
            out += [
                    "| # | Outil | Colonne | Statut | Pourquoi | Valeurs modifiées |", "|---|---|---|---|---|---|"]
            for st in [x for x in s["steps"] if x["table"] == name]:
                out.append(f"| {st['step']} | {st['label']} | {st['column'] or '(table)'} | {st['status']} | {st['reason']} | {st['values_changed']} |")
        if s["needs_human_validation"]:
            out += ["", "## Points signalés (information, rien n'a été modifié)", ""] + [f"- {x}" for x in s["needs_human_validation"]]
        return "\n".join(out) + "\n"
