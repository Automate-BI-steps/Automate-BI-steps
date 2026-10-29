"""Agent Quality & Cleaning de BI Flow (DSO2), avec traçage et comparaison.

Boucle de l'agent, table par table :
  1. lire  : chaque CSV (encodage et séparateur détectés) et chaque feuille Excel devient une table ;
  2. observer puis décider : chaque outil de tools.TOOLS inspecte la table ou la colonne et dit s'il s'applique, et pourquoi ;
  3. agir : l'outil est appliqué (noms de colonnes, types, clés, taux, montants, conditionnements décomposés…) ;
     les valeurs extrêmes, elles, ne sont traitées qu'après accord de l'utilisateur (vider, ramener à la limite ou garder) ;
  4. tracer : l'agent de traçage enregistre la décision, les paramètres et les valeurs modifiées ;
  5. comparer : une vue avant / après simple (view.json + comparaison.html) est produite pour le run.

Usage :
  python agent.py fichier1.csv fichier2.xlsx dossier/ --output ../data/clean [--outliers ask|empty|cap|keep]
  (nœud LangGraph : cleaning_node(state) -> dict)
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

import pandas as pd

try:  # paquet src.agents.cleaning
    from .compare import build_view, cell_changes, write_html_report
    from .tools import ROW_ID, TOOLS, data_cols
    from .tracer import TraceAgent
except ImportError:  # script lancé depuis ce dossier
    from compare import build_view, cell_changes, write_html_report
    from tools import ROW_ID, TOOLS, data_cols
    from tracer import TraceAgent

ROOT = Path(__file__).resolve().parents[3]  # racine du dépôt
DEFAULT_INPUT = ROOT / "Datasets" / "Import&Export" / "data"
DEFAULT_OUTPUT = ROOT / "outputs" / "cleaning" / "clean"
DEFAULT_HISTORY = ROOT / "outputs" / "cleaning" / "history"
SUPPORTED = {".csv", ".txt", ".tsv", ".xlsx", ".xlsm", ".xls"}


# --------------------------------------------------------------------------- lecture
# Noms d'encodage du rapport de profilage -> noms Python.
PROFILING_ENCODINGS = {"ascii": "utf-8-sig", "utf-8": "utf-8-sig", "iso-8859-1": "latin-1", "windows-1252": "cp1252"}


def read_csv(path: Path, encoding: str | None = None, delimiter: str | None = None) -> tuple[pd.DataFrame, str, str]:
    """Lit un CSV. encoding / delimiter : indices venant du profilage (sinon détection locale)."""
    raw = Path(path).read_bytes()
    text, hinted = None, PROFILING_ENCODINGS.get((encoding or "").lower(), encoding)
    for enc in ([hinted] if hinted else []) + ["utf-8-sig", "cp1252", "latin-1"]:
        try:
            text = raw.decode(enc)
            encoding = enc
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if not delimiter:
        try:
            delimiter = csv.Sniffer().sniff(text[:20000], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    df = pd.read_csv(path, sep=delimiter, encoding=encoding, dtype=str, keep_default_na=False)
    return df, encoding, delimiter


def table_name(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z_]+", "_", text).strip("_").lower() or "table"


def load_inputs(paths: list[Path], tracer: TraceAgent, hints: dict[str, dict] | None = None) -> dict[str, tuple[pd.DataFrame, str]]:
    """hints : {chemin résolu: métadonnées du fichier dans le rapport de profilage} (encodage, séparateur)."""
    hints = hints or {}
    files = []
    for p in paths:
        p = Path(p)
        files += sorted(f for f in p.iterdir() if f.suffix.lower() in SUPPORTED) if p.is_dir() else [p]
    tables = {}
    for f in files:
        if f.suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
            sheets = pd.read_excel(f, sheet_name=None, dtype=str, keep_default_na=False)
            names = []
            for sheet, df in sheets.items():
                name = table_name(f.stem if len(sheets) == 1 else f"{f.stem}_{sheet}")
                tables[name] = (df, f"{f.name} / feuille {sheet}")
                names.append(name)
            tracer.record_input(f, names, None, None)
        else:
            h = hints.get(str(f.resolve()), {})
            df, enc, sep = read_csv(f, h.get("encoding"), h.get("delimiter"))
            name = table_name(f.stem)
            tables[name] = (df, f.name)
            tracer.record_input(f, [name], enc, sep)
    return tables


# --------------------------------------------------------------------------- agent
class CleaningAgent:
    def __init__(self, tracer: TraceAgent, impute: bool = False, dayfirst: bool = True, planner=None,
                 profiling_issues: dict[str, list[dict]] | None = None):
        self.tracer = tracer
        self.profiling_issues = profiling_issues or {}  # diagnostic de l'agent de profilage, par table
        self.tools = [t for t in TOOLS if impute or not t.optional]
        self.dayfirst = dayfirst
        self.planner = planner  # None = règles seules ; LLMPlanner = le LLM analyse et choisit
        self.questions: list[dict] = []   # corrections qui attendent l'accord de l'utilisateur
        self._pending: dict[str, tuple] = {}

    @staticmethod
    def prepare(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df.columns = [str(c).strip() for c in df.columns]
        df.insert(0, ROW_ID, range(1, len(df) + 1))  # n° de ligne source (hors en-tête)
        return df.replace(r"^\s*$", pd.NA, regex=True)  # cellule vide ou blanche = valeur manquante

    def _loop(self, df: pd.DataFrame):
        """Observe la table outil par outil et produit (outil, colonne, décision) ; l'appelant décide d'appliquer."""
        derived: set[str] = set()  # colonnes créées par l'agent (conditionnement décomposé) : déjà propres
        for tool in self.tools:
            targets = [None] if tool.scope == "table" else [c for c in data_cols(df) if c not in derived]
            for col in targets:
                if col is not None and col not in df.columns:
                    continue
                kwargs = {"dayfirst": self.dayfirst} if tool.name == "parse_dates" else {}
                decision = tool.detect(df, col, **kwargs) if col is not None else tool.detect(df)
                if decision is not None:
                    df = yield tool, col, decision
                    if tool.name == "split_packaging":
                        derived.update(decision.params.get("columns", []))

    def _apply(self, tool, col, decision, df):
        return tool.apply(df, col, **decision.params) if col is not None else tool.apply(df, **decision.params)

    def candidates(self, df: pd.DataFrame) -> list[dict]:
        """Simulation avec les règles seules : liste des corrections et signalements proposés au LLM."""
        out, gen = [], self._loop(df)
        try:
            tool, col, dec = next(gen)
            while True:
                kind = "ask" if tool.ask else tool.kind
                out.append({"id": len(out) + 1, "tool": tool.name, "kind": kind, "column": col, "rule_reason": dec.reason})
                df = df if kind in {"flag", "ask"} else self._apply(tool, col, dec, df.copy())
                tool, col, dec = gen.send(df)
        except StopIteration:
            return out

    def clean_table(self, name: str, df: pd.DataFrame, source: str = "") -> pd.DataFrame:
        df = self.prepare(df)
        verdicts = {}
        if self.planner is not None:
            cands = self.candidates(df)
            try:
                plan = self.planner.plan(name, source, df, cands, self.profiling_issues.get(name))
                ids = {(c["tool"], c["column"]): c["id"] for c in cands}
                verdicts = {k: plan["verdicts"][i] for k, i in ids.items() if i in plan["verdicts"]}
                self.tracer.record_llm(name, self.planner.model, plan, len(cands))
            except Exception as exc:  # API indisponible : on continue avec les règles, et on le trace
                self.tracer.record_llm(name, getattr(self.planner, "model", "?"), None, len(cands), error=str(exc))

        gen = self._loop(df)
        try:
            tool, col, decision = next(gen)
            while True:
                rows_before = len(df)
                accept, llm_reason = verdicts.get((tool.name, col), (True, ""))
                reason = decision.reason + (f" | LLM : {llm_reason}" if llm_reason else "")
                if tool.ask:  # on ne touche à rien : la question est posée à l'utilisateur à la fin
                    recommended = decision.params.get("recommended", "keep")
                    qid = f"q{len(self.questions) + 1}"
                    self._pending[qid] = (tool, decision)
                    self.questions.append({
                        "id": qid, "table": name, "column": col, "tool": tool.name, "label": tool.label,
                            "summary": decision.reason, "low": decision.params.get("low"), "high": decision.params.get("high"),
                        "n_low": decision.params.get("n_low", 0), "n_high": decision.params.get("n_high", 0),
                        "median": decision.params.get("median"), "log_scale": decision.params.get("log_scale", False),
                        "rows": decision.params.get("rows", []), "examples": decision.params.get("examples", []),
                        "recommended": recommended,
                        "profiling_issue": next((i for i in self.profiling_issues.get(name, [])
                                                 if i.get("column") == col and i.get("issue_type") in {"outlier", "out_of_range"}), None),
                        "llm_advice": ((recommended if recommended != "keep" else "cap") if accept else "keep") if llm_reason else None,
                        "llm_reason": llm_reason})
                elif not accept:
                    self.tracer.log_step(name, tool, col, reason, decision.params, rows_before, rows_before, 0, [], "rejected")
                elif tool.kind == "flag":
                    self.tracer.log_step(name, tool, col, reason, decision.params, rows_before, rows_before, 0, [], "flagged")
                else:
                    before = df.copy()
                    df = self._apply(tool, col, decision, df)
                    if tool.name == "clean_column_names":
                        changed = len(decision.params["mapping"])
                        examples = [{"avant": k, "apres": v} for k, v in decision.params["mapping"].items()][:5]
                    elif tool.name == "split_packaging":
                        changed = decision.params["count"]
                        cols = [col] + decision.params["columns"]
                        examples = df[cols].drop_duplicates(subset=[col]).head(3).astype(str).to_dict("records")
                    elif col is not None:
                        changed, examples = cell_changes(before, df, col)
                    else:
                        removed = sorted(set(before[ROW_ID]) - set(df[ROW_ID]))
                        changed, examples = len(removed), [{"lignes_supprimées": removed}]
                    self.tracer.log_step(name, tool, col, reason, decision.params, rows_before, len(df), changed, examples, "applied")
                tool, col, decision = gen.send(df)
        except StopIteration:
            return df

    def answer(self, qid: str, choice: str, df: pd.DataFrame) -> pd.DataFrame:
        """Réponse de l'utilisateur à une question : 'empty' (vider), 'cap' (ramener à la limite) ou 'keep' (garder)."""
        tool, decision = self._pending[qid]
        q = next(x for x in self.questions if x["id"] == qid)
        if choice in {"cap", "empty"}:
            before = df.copy()
            params = {**decision.params, "mode": choice}
            df = tool.apply(df, q["column"], **params)
            changed, examples = cell_changes(before, df, q["column"])
            what = "mis à vide" if choice == "empty" else "ramené dans la plage habituelle"
            self.tracer.log_step(q["table"], tool, q["column"], decision.reason + f" | {what} avec l'accord de l'utilisateur",
                                 params, len(df), len(df), changed, examples, "applied")
        else:
            self.tracer.log_step(q["table"], tool, q["column"], decision.reason + " | conservé à la demande de l'utilisateur",
                                 decision.params, len(df), len(df), 0, [], "kept")
        return df


def make_planner(mode: str, model: str | None = None, api_key: str | None = None):
    """'rules' : règles seules. 'llm' : LLM Groq ; si la clé ou l'API manque, retour aux règles (tracé)."""
    if mode != "llm":
        return None, "rules", None
    try:
        from llm_planner import GroqClient, LLMPlanner
        planner = LLMPlanner(GroqClient(api_key=api_key, model=model))
        return planner, "llm", None
    except Exception as exc:
        return None, "rules", f"LLM indisponible, mode règles utilisé : {exc}"


class CleaningSession:
    """Un run en deux temps : analyse + nettoyage (start), puis réponses de l'utilisateur et publication (finish)."""

    def __init__(self, inputs: list[Path], output_dir: Path = DEFAULT_OUTPUT, history_dir: Path = DEFAULT_HISTORY,
                 impute: bool = False, dayfirst: bool = True, planner_mode: str = "rules", model: str | None = None,
                 planner=None, api_key: str | None = None, profiling: dict | None = None):
        """profiling : section `profiling` du state (rapport JSON de l'agent de profilage), facultative."""
        self.output_dir = Path(output_dir)
        self.tracer = TraceAgent(Path(history_dir))
        hints, issues = {}, {}
        if profiling:
            hints = {str(Path(f["path"]).resolve()): f for f in profiling.get("metadata", {}).get("files", [])}
            for i in profiling.get("issues", []):
                issues.setdefault(table_name(i["table"]), []).append(i)
            self.tracer.profiling = {"run_id": profiling.get("metadata", {}).get("run_id"),
                                     "issues": len(profiling.get("issues", [])),
                                     "quality_score": profiling.get("summary", {}).get("global_quality_score")}
        if planner is None:
            planner, mode, warning = make_planner(planner_mode, model, api_key)
        else:
            mode, warning = "llm", None
        self.tracer.set_planner(mode, getattr(planner, "model", None), warning)
        self.agent = CleaningAgent(self.tracer, impute=impute, dayfirst=dayfirst, planner=planner, profiling_issues=issues)
        self.before: dict[str, pd.DataFrame] = {}
        self.cleaned: dict[str, pd.DataFrame] = {}
        self.sources: dict[str, str] = {}
        for name, (raw, source) in load_inputs(inputs, self.tracer, hints).items():
            before = raw.copy()
            before.insert(0, ROW_ID, range(1, len(before) + 1))
            self.tracer.snapshot_before(name, before, source)
            self.before[name], self.sources[name] = before, source
            self.cleaned[name] = self.agent.clean_table(name, raw, source)

    @property
    def questions(self) -> list[dict]:
        return self.agent.questions

    def finish(self, answers: dict[str, str] | None = None, default: str = "keep") -> dict:
        answers = answers or {}
        for q in self.questions:
            choice = answers.get(q["id"], default)
            if choice == "advice":  # suivre la recommandation (LLM, sinon règles)
                choice = q["llm_advice"] or q["recommended"]
            self.cleaned[q["table"]] = self.agent.answer(q["id"], choice if choice in {"cap", "empty"} else "keep",
                                                         self.cleaned[q["table"]])
        self.output_dir.mkdir(parents=True, exist_ok=True)
        for name, df in self.cleaned.items():
            self.tracer.snapshot_after(name, df, self.output_dir)
        summary = self.tracer.close()
        view = build_view(summary, self.before, self.cleaned, self.sources)
        run_dir = self.tracer.run_dir
        (run_dir / "view.json").write_text(json.dumps(view, ensure_ascii=False, indent=1), encoding="utf-8")
        write_html_report(run_dir / "comparaison.html", view)
        summary.update({"clean_data_dir": str(self.output_dir), "report": str(run_dir / "comparaison.html"),
                        "trace": str(run_dir / "trace.md"), "view": view})
        return summary


CHOICE_TEXT = {"empty": "mettre à vide", "cap": "ramener à la limite", "keep": "garder"}


def ask_in_terminal(questions: list[dict]) -> dict[str, str]:
    answers = {}
    for q in questions:
        lines = [f"\n{q['table']}.{q['column']} : {q['summary']}"]
        lines += [f"   ligne {r['row']} {r['label']} : {r['value']:g} -> {r['diagnosis']}" for r in q["rows"]]
        advice = q["llm_advice"] or q["recommended"]
        lines.append(f"   Conseil : {CHOICE_TEXT[advice]}" + (f" (LLM : {q['llm_reason']})" if q["llm_advice"] else ""))
        rep = input("\n".join(lines) + f"\n   [v]ider, [r]amener entre {q['low']:g} et {q['high']:g}, [g]arder, "
                    "Entrée = conseil ? ").strip().lower()
        answers[q["id"]] = {"v": "empty", "r": "cap", "g": "keep"}.get(rep[:1], advice)
    return answers


def run(inputs: list[Path], output_dir: Path = DEFAULT_OUTPUT, history_dir: Path = DEFAULT_HISTORY,
        impute: bool = False, dayfirst: bool = True, planner_mode: str = "rules", model: str | None = None,
        planner=None, api_key: str | None = None, outliers: str = "keep") -> dict:
    """Run complet en un appel. outliers : 'keep' (ne rien toucher), 'empty' (vider), 'cap' (ramener à la limite),
    'advice' (suivre le conseil) ou 'ask' (demander dans le terminal)."""
    session = CleaningSession(inputs, output_dir, history_dir, impute, dayfirst, planner_mode, model, planner, api_key)
    if outliers == "ask" and session.questions:
        answers = ask_in_terminal(session.questions)
    else:
        answers = {q["id"]: outliers for q in session.questions}
    return session.finish(answers)


def cleaning_node(state: dict) -> dict:
    """Nœud LangGraph : lit state['input_paths'] et écrit les tables propres dans state['clean_data_dir']."""
    try:
        s = run([Path(p) for p in state.get("input_paths", [DEFAULT_INPUT])],
                Path(state.get("clean_data_dir", DEFAULT_OUTPUT)), Path(state.get("history_dir", DEFAULT_HISTORY)),
                impute=state.get("impute", False), planner_mode=state.get("planner", "rules"),
                outliers=state.get("outliers", "keep"))
        return {"clean_data_dir": s["clean_data_dir"], "cleaning_status": s["status"], "cleaning_run_id": s["run_id"],
                "cleaning_trace": s["trace"], "cleaning_report": s["report"]}
    except Exception as exc:
        return {"cleaning_status": "KO", "cleaning_error": str(exc)}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Agent de nettoyage BI Flow (CSV et Excel, avec traçage)")
    p.add_argument("inputs", nargs="*", type=Path, default=[DEFAULT_INPUT], help="fichiers CSV / Excel ou dossiers")
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    p.add_argument("--impute", action="store_true", help="imputer les valeurs manquantes (médiane / mode)")
    p.add_argument("--monthfirst", action="store_true", help="lire 03/04/2024 comme 4 mars (défaut : 3 avril)")
    p.add_argument("--planner", choices=["rules", "llm"], default="rules", help="llm : analyse et choix des outils par un LLM Groq")
    p.add_argument("--model", help="modèle Groq (défaut : GROQ_MODEL ou openai/gpt-oss-120b)")
    p.add_argument("--outliers", choices=["ask", "advice", "empty", "cap", "keep"], default="ask" if sys.stdin.isatty() else "keep",
                   help="valeurs extrêmes : demander (défaut en terminal), suivre le conseil, vider, ramener à la limite ou garder")
    a = p.parse_args()
    s = run(a.inputs, a.output, a.history, impute=a.impute, dayfirst=not a.monthfirst, planner_mode=a.planner,
            model=a.model, outliers=a.outliers)
    v = s["view"]
    pl = s["planner"]
    print(f"\nPlanificateur : {pl['mode']}" + (f" ({pl['model']})" if pl["model"] else "") + (f" - {pl['warning']}" if pl["warning"] else ""))
    print(f"{v['totals']['tables_to_process']} table(s) à traiter sur {v['totals']['tables']} · "
          f"{v['totals']['corrections']} correction(s) · {v['totals']['rows_removed']} ligne(s) supprimée(s)")
    for t in v["tables"]:
        print(f"\n  {t['name']} ({t['rows_before']} -> {t['rows_after']} lignes)")
        for act in t["actions"]:
            print(f"    - {act['text']}")
    if v["clean_tables"]:
        print(f"\n  Déjà propres : {', '.join(v['clean_tables'])}")
    print(f"\nComparaison : {s['report']}\nTables      : {s['clean_data_dir']}")
