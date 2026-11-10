"""BI Auditor / XAI (Agent 6) : code de Dhouha (notebook `BIAuditor_XAI.ipynb`, partie B) sous forme de module.

La logique est celle du notebook, cellule par cellule (B.1 chargement, B.2 journal d'audit unifié,
B.3 lignée / provenance / réconciliation, B.4 RAG, B.5 LLM, B.6 preuves + réponses vérifiées,
B.7 les quatre questions XAI, B.8 « Pourquoi ? » libre, B.9 file de validation humaine + rapport).

Règle de conception du notebook, conservée : les faits viennent d'enregistrements déterministes
(journaux, lignée, recalcul) ; le LLM ne fait que les REFORMULER, et chaque réponse est vérifiée
(ids cités existants, nombres présents dans les preuves) puis réessayée / filtrée / remplacée par
un texte « preuves seules » si elle n'est pas fiable.

L'Auditor ne lit QUE des fichiers CSV (le « contrat », voir README_auditor.md). Les seuls
changements par rapport au notebook, nécessaires pour en faire un module :
  - les variables globales deviennent les attributs de `Auditor` ; rien n'est exécuté à l'import ;
  - les parties qui dépendaient du jeu de données simulé (recalcul K01-K05, provenance au niveau
    des lignes, colonnes de dimension, format des valeurs) sont des MÉTHODES surchargeables : le
    comportement par défaut est celui du notebook, `adapter.PipelineAuditor` les remplace pour les
    vrais agents ;
  - retrait de Colab / IPython / `%pip` ; fournisseurs LLM : « hf » et « anthropic » du notebook,
    plus « groq » (clé déjà saisie dans l'interface) et « none » (réponses « preuves seules ») ;
  - repli lexical si ChromaDB / sentence-transformers ne sont pas installés (le champ
    `retrieval` du rapport dit lequel a servi) ;
  - quelques gardes (id d'incident profilage absent, recalcul absent) et une affirmation figée du
    notebook (« la réconciliation correspond ») rendue conforme au résultat réel.
"""
from __future__ import annotations

import datetime as dt
import math
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

# ------------------------------------------------------------------ vérification des réponses (B.6)
ID_RE = r"\b(?:[A-Z]{1,2}\d{2,4}|R-[A-Z]+-\d{2}|G-[A-Z-]+)\b"

SYSTEM = """You are the BI Auditor / XAI agent of BI Flow, a multi-agent Power BI automation system for import/export trade data.
You explain results to a customs compliance manager who must justify every number in an audit.
STRICT RULES:
1. Use ONLY facts written in the EVIDENCE block. Never add outside knowledge, guesses, reasons or consequences that are not written there.
2. Write 3 to 5 short sentences. End EVERY sentence with the id of the evidence item it comes from in square brackets, for example: The rate used was 3.35 TND per EUR [C0100].
3. Copy numbers exactly as written in the evidence.
4. If the evidence does not contain what is asked, say precisely what is missing.
5. No headings, no bullet lists, no formulas, no closing remarks."""

CITE_RE = r"\[((?:[A-Z]{1,2}\d{2,4}|R-[A-Z]+-\d{2}|G-[A-Z-]+))\]"      # only real evidence ids count (DAX refs like [flow] do not)
STOP = set("a an the of to in on for and or is are was were be been being by with as that this these those it its from at which who than then so such "
           "into over under per via not no can could should would will may might also has have had there their they them what when where how why because "
           "while about between during each other more most some any all one two both only "
           "used use based shows show showing indicates indicating applied within context equals resulted result calculated calculates across due given made make makes "
           "provided provides according following specific specifically key main".split())

GLOSSARY = [
    ("G-HS", "HS code", "The Harmonized System (HS) classifies traded goods. The first 6 digits are common to all countries (chapter, heading, subheading). A valid HS6 code has exactly 6 digits; punctuation such as 1509.10 or 1509-10 is only formatting. A code with fewer or more digits cannot be mapped to a product and must be reviewed."),
    ("G-ISO", "ISO country codes", "ISO 3166-1 alpha-2 codes (TN, FR, IT) give each country one unambiguous two-letter code. Customs data often contains local names (Tunisie, Italie), abbreviations (UK, USA) or typos, so country names must be normalised to ISO codes before any aggregation by partner country."),
    ("G-FX", "Currency conversion", "Trade values are declared in several currencies (EUR, USD, TND). To compare or add them, each value is converted to one reporting currency, here the Tunisian dinar (TND), using a documented exchange-rate table. The rate used must be recorded for every conversion so the figure can be reproduced in an audit."),
    ("G-DUP", "Duplicate declarations", "A customs declaration has a unique identifier. If the same declaration_id appears twice, for example because the same file was loaded twice, the value is counted twice and inflates exports or imports. Duplicates are removed keeping the first occurrence, and each removal is logged."),
    ("G-TRADE-BALANCE", "Trade balance", "Trade balance = exports minus imports over a period. A positive balance is a surplus, a negative one is a deficit. It depends on the same value column and the same cleaning rules as total exports and total imports."),
    ("G-COVERAGE", "Coverage ratio", "Coverage ratio = exports divided by imports. A value above 1 means exports pay for all imports; below 1 means imports exceed exports. Customs and finance teams use it to judge external balance."),
    ("G-YOY", "Year-over-year growth", "Year-over-year (YoY) growth = (current year minus previous year) divided by previous year. In DAX it is computed with SAMEPERIODLASTYEAR. It needs complete, consistently cleaned data for both years, otherwise growth reflects data errors instead of trade."),
    ("G-AUDIT", "Customs audit traceability", "In a customs audit the company must justify every reported figure. This requires lineage from the dashboard number back to the declarations: which rows were used, which corrections were applied, with which rule, by whom, and whether a human approved it."),
    ("G-HUMAN", "Human validation policy", "Corrections proposed by heuristics or language models must remain under human control. Low-confidence changes (fuzzy matches, negative values, missing countries) are queued for a human reviewer. Until approved, the status is pending and the figure should be considered provisional."),
    ("G-QUARANTINE", "Quarantine of rows", "Rows that cannot be repaired automatically, such as a missing partner country or an invalid HS code, are quarantined: they stay in quarantine.csv and are excluded from the analytical model, so reported totals are lower than raw totals and the exclusion is documented."),
    ("G-DAX", "DAX measures", "DAX is the Power BI formula language. CALCULATE changes the filter context of an aggregation, for example summing value_tnd only where flow is Export. DIVIDE performs a safe division that avoids divide-by-zero errors."),
    ("G-ZSCORE", "Anomaly detection with z-score", "A z-score measures how many standard deviations a value is from the mean. An absolute z-score of at least 2 is a common threshold to flag an unusual month. An anomaly may be a real shipment or a data error, so it must be checked before it is reported."),
    ("G-CONCENTRATION", "Concentration risk", "Concentration risk appears when a large share of exports depends on one product or one partner country, or imports on one supplier. Thresholds such as 30% for a product or 25% for a supplier trigger a recommendation to diversify."),
    ("G-STAR", "Star schema", "A star schema has one fact table (Fact_Trade, one row per declaration) linked by surrogate keys to dimension tables (Dim_Country, Dim_Product, Dim_Date). Power BI models perform best with this structure."),
    ("G-INCOTERMS", "Incoterms", "Incoterms (FOB, CIF, EXW) define who pays transport and insurance. Values declared under different Incoterms are not directly comparable unless adjusted, which is a known limitation when comparing declared trade values."),
    ("G-DATE", "Date formats", "Dates in trade files appear as 2024-03-15, 15/03/2024 or Mar 15, 2024. Day/month ambiguity is a classic error source, so all dates are parsed with an explicit format and stored as ISO 8601."),
]


def stems(text):
    text = re.sub(ID_RE, " ", re.sub(r"\[[^\]]*\]", " ", text))
    return {w[:5] for w in re.findall(r"[a-z][a-z_\-]+", text.lower()) if w not in STOP and len(w) > 2}


def num_tokens(text):
    text = re.sub(r"\[[^\]]*\]", " ", text)
    text = re.sub(ID_RE, " ", text)
    out = []
    for t in re.findall(r"\d[\d,]*\.?\d*", text):
        t = t.replace(",", "").rstrip(".")
        try:
            out.append((float(t), "." in t))
        except ValueError:
            pass
    return out


def split_sentences(text):
    out = []
    for line in re.split(r"\n+", re.sub(r"\*\*|`", "", text)):
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", line).strip()
        if not line or "\\" in line or line.startswith("$") or line.endswith(":"):
            continue
        out += [x.strip() for x in re.split(r"(?<=[.!?])\s+(?=[A-Z\[(\"'])", line) if x.strip()]
    return out


def ground_answer(answer, evidence, min_cov=0.5):
    """Sentence-level grounding: keep only sentences supported by the evidence, attach the supporting evidence ids."""
    ev_tok = {e["id"]: stems(e["text"]) for e in evidence}
    ev_all = set().union(*ev_tok.values()) if ev_tok else set()
    ev_nums = [v for e in evidence for v, _ in num_tokens(e["text"])]
    kept, dropped = [], []
    for sent in split_sentences(answer):
        own = [c for c in re.findall(CITE_RE, sent) if c in ev_tok]
        core = re.sub(r"\s*(?:" + CITE_RE + r"\s*)+", " ", sent).strip()
        core = re.sub(r"\s+([.!?])", r"\1", core)
        if not core or core[-1] not in ".!?":
            dropped.append((sent, "incomplete sentence (cut off)")); continue
        toks = stems(core)
        if not toks:
            dropped.append((sent, "no factual content")); continue
        bad = [v for v, d in num_tokens(core) if (v >= 10 or d) and not any(abs(v - x) <= max(0.06, 0.005 * abs(x)) for x in ev_nums)]
        cov = len(toks & ev_all) / len(toks)
        if bad:
            dropped.append((sent, f"number {bad[0]:g} not in evidence")); continue
        if cov < min_cov:
            dropped.append((sent, f"only {cov:.0%} of its content words are in the evidence")); continue
        ranked = sorted(((len(toks & t), i) for i, t in ev_tok.items()), reverse=True)
        cites = list(dict.fromkeys(own + [i for sc, i in ranked if sc >= 2][:2]))[:3] or [ranked[0][1]]
        kept.append(core + " " + " ".join(f"[{c}]" for c in cites))
    status = "failed" if not kept else ("verified" if not dropped else "filtered")
    return " ".join(kept), dict(status=status, kept=kept, dropped=dropped)


def template_answer(evidence):
    out = ""
    for e in evidence:
        if e["id"].startswith("G-"):
            continue
        piece = f"{e['text']} [{e['id']}] "
        if out and len(out) + len(piece) > 900:
            break
        out += piece
    return out.strip()


def split(s):
    return [x for x in str(s).split("|") if x]


def resolve_provider(value: str | None) -> str:
    """Fournisseur du LLM de l'Agent 6. « auto » (défaut) : Groq si l'Agent 6 a une clé (GROQ_API_KEY_AGENT6, sinon
    GROQ_API_KEY), sinon Claude si ANTHROPIC_API_KEY existe, sinon aucun (preuves seules).
    BI_FLOW_USE_LLM=false coupe les API (le modèle local « hf » reste possible)."""
    from src.llm_keys import llm_allowed, llm_enabled

    provider = (value or "auto").strip().lower() or "auto"
    if provider == "auto":
        if llm_enabled(6):
            provider = "groq"
        elif llm_allowed() and (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
            provider = "anthropic"
        else:
            provider = "none"
    if provider in ("groq", "anthropic") and not llm_allowed():
        provider = "none"
    return provider


# ------------------------------------------------------------------ configuration (B.5 + B.7)
@dataclass
class AuditorConfig:
    llm_provider: str = "none"                 # none | hf | anthropic | groq ; « auto » (défaut de from_env) : voir resolve_provider
    hf_model: str = "Qwen/Qwen2.5-1.5B-Instruct"
    anthropic_model: str = "claude-sonnet-5-5"  # ANTHROPIC_API_KEY
    groq_model: str = "openai/gpt-oss-120b"     # GROQ_API_KEY
    max_new_tokens: int = 280
    n_corr_explain: int = 6                    # corrections expliquées (une par règle, validation humaine d'abord)
    n_kpi_explain: int = 5
    n_insight_explain: int = 4
    retrieval: str = "auto"                    # auto | chroma | lexical

    @classmethod
    def from_env(cls) -> "AuditorConfig":
        e = os.environ.get
        return cls(llm_provider=resolve_provider(e("AUDITOR_LLM_PROVIDER", "auto")), hf_model=e("AUDITOR_HF_MODEL", cls.hf_model),
                   anthropic_model=e("AUDITOR_ANTHROPIC_MODEL", cls.anthropic_model),
                   groq_model=e("AUDITOR_GROQ_MODEL") or e("GROQ_MODEL") or cls.groq_model,
                   max_new_tokens=int(e("AUDITOR_MAX_NEW_TOKENS", cls.max_new_tokens)),
                   n_corr_explain=int(e("AUDITOR_N_CORRECTIONS", cls.n_corr_explain)),
                   n_kpi_explain=int(e("AUDITOR_N_KPI", cls.n_kpi_explain)),
                   n_insight_explain=int(e("AUDITOR_N_INSIGHTS", cls.n_insight_explain)),
                   retrieval=e("AUDITOR_RETRIEVAL", "auto").lower())


class LLM:
    """B.5 : modèle Hugging Face local ou API (Claude, Groq). Chargé une fois ; sans modèle, l'Auditor répond avec les preuves seules."""

    def __init__(self, cfg: AuditorConfig):
        self.provider, self.max_new_tokens = cfg.llm_provider, cfg.max_new_tokens
        self.name = {"hf": cfg.hf_model, "anthropic": cfg.anthropic_model, "groq": cfg.groq_model}[self.provider]

    def load(self) -> "LLM":
        if self.provider == "hf":
            from transformers import AutoModelForCausalLM, AutoTokenizer
            self.tok = AutoTokenizer.from_pretrained(self.name)
            self.model = AutoModelForCausalLM.from_pretrained(self.name, torch_dtype="auto", device_map="auto")
            self.model.eval()
        elif self.provider == "anthropic":
            import anthropic
            # clé vide -> None : le SDK cherche alors seul ses identifiants (ANTHROPIC_AUTH_TOKEN, profil `ant auth login`)
            self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY") or None)
        else:  # groq : clé propre de l'Agent 6 (GROQ_API_KEY_AGENT6, sinon GROQ_API_KEY ; elle arrive dans GROQ_API_KEY)
            from langchain_groq import ChatGroq
            key = (os.environ.get("GROQ_API_KEY") or "").strip()
            if not key:
                raise RuntimeError("GROQ_API_KEY absente (GROQ_API_KEY_AGENT6 ou GROQ_API_KEY)")
            # les modèles gpt-oss raisonnent avant de répondre et ce raisonnement compte dans max_tokens :
            # effort « low » et une marge, pour que le budget serve à la réponse
            reasoning = self.name.startswith("openai/gpt-oss")
            self.client = ChatGroq(model=self.name, temperature=0, api_key=key, max_retries=1, timeout=60,
                                   max_tokens=self.max_new_tokens + (500 if reasoning else 0),
                                   **({"reasoning_effort": "low"} if reasoning else {}))
        return self

    def __call__(self, system, user, max_new_tokens=None):
        max_new_tokens = max_new_tokens or self.max_new_tokens
        if self.provider == "hf":
            msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            text = self.tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
            inputs = self.tok(text, return_tensors="pt").to(self.model.device)
            out = self.model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
            return self.tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()
        if self.provider == "anthropic":
            # pas de `temperature` : les modèles récents la refusent (400). Sonnet 5.5 réfléchit par défaut et la réflexion
            # compte dans max_tokens (280) : `between_tools` la coupe pour que le budget serve à la réponse. Les blocs de
            # réflexion ne sont pas du texte : on ne garde que les blocs « text ».
            extra = {"thinking": {"type": "between_tools"}} if self.name == "claude-sonnet-5-5" else {}
            r = self.client.messages.create(model=self.name, max_tokens=max_new_tokens, system=system,
                                            messages=[{"role": "user", "content": user}], **extra)
            return "".join(b.text for b in r.content if b.type == "text").strip()
        return str(self.client.invoke([("system", system), ("user", user)]).content).strip()


# ------------------------------------------------------------------ recherche (B.4)
class Retriever:
    """Deux collections (glossaire, journal d'audit). ChromaDB + all-MiniLM-L6-v2 comme dans le notebook ;
    à défaut (bibliothèques absentes ou modèle injoignable) : recherche lexicale pondérée par la rareté des mots."""

    def __init__(self, store_dir: Path, mode: str = "auto"):
        self.backend, self.note, self.client, self.embed = "lexical", "", None, None
        if mode in ("auto", "chroma"):
            try:
                import chromadb
                from chromadb.utils import embedding_functions
                self.embed = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="sentence-transformers/all-MiniLM-L6-v2")
                self.client = chromadb.PersistentClient(path=str(store_dir))
                self.backend = "chromadb + sentence-transformers (all-MiniLM-L6-v2)"
            except Exception as exc:  # noqa: BLE001
                self.client, self.embed = None, None
                self.note = f"{type(exc).__name__}: {str(exc)[:120]}"
                if mode == "chroma":
                    raise
        self.collections: dict[str, dict] = {}

    def build(self, name: str, ids: list, docs: list, metas: list) -> str:
        if self.client is not None:
            try:
                self.client.delete_collection(name)
            except Exception:  # noqa: BLE001
                pass
            col = self.client.create_collection(name, embedding_function=self.embed, metadata={"hnsw:space": "cosine"})
            for i in range(0, len(ids), 500):
                col.add(ids=ids[i:i + 500], documents=docs[i:i + 500], metadatas=metas[i:i + 500])
            self.collections[name] = {"chroma": col}
        else:
            toks = [set(re.findall(r"[a-z0-9]+", d.lower())) - STOP for d in docs]
            df = {}
            for t in toks:
                for w in t:
                    df[w] = df.get(w, 0) + 1
            idf = {w: math.log(1 + len(docs) / c) for w, c in df.items()}
            self.collections[name] = {"ids": ids, "docs": docs, "metas": metas, "toks": toks, "idf": idf}
        return name

    def query(self, name: str, query: str, k: int = 3) -> list[dict]:
        col = self.collections[name]
        if "chroma" in col:
            res = col["chroma"].query(query_texts=[query], n_results=k)
            return [dict(id=i, text=d, distance=round(s, 3), meta=m)
                    for i, d, s, m in zip(res["ids"][0], res["documents"][0], res["distances"][0], res["metadatas"][0])]
        q = set(re.findall(r"[a-z0-9]+", query.lower())) - STOP
        scores = []
        for i, t in enumerate(col["toks"]):
            common = q & t
            s = sum(col["idf"][w] for w in common) / (1 + math.log(1 + len(t)))
            scores.append((s, i))
        top = sorted(scores, key=lambda x: (-x[0], x[1]))[:k]
        return [dict(id=col["ids"][i], text=col["docs"][i], distance=round(1 - s / (1 + s), 3), meta=col["metas"][i]) for s, i in top]


# ------------------------------------------------------------------ l'Auditor
class Auditor:
    """Lit les CSV de `out_dir`, écrit : audit_trail, lineage, reconciliation, xai_explanations, human_review_queue, xai_report.md."""

    # visuel -> colonnes de dimension en plus des colonnes de ses KPI (B.3) ; surchargé pour les vrais agents
    DIM_COLS = {"none": [], "country": ["Fact_Trade[country_key]", "Dim_Country[country_iso]"],
                "product": ["Fact_Trade[product_key]", "Dim_Product[hs6]"], "month": ["Fact_Trade[date_key]", "Dim_Date[year]", "Dim_Date[month]"]}
    QUARANTINE_RULES = {"R-CTRY-03", "R-HS-02"}      # règles qui mettent la ligne en quarantaine (file de validation, B.9)
    INSIGHT_AGENT = "BI Analyst"                     # agent qui a produit les constats
    SYSTEM = SYSTEM                                  # consigne du LLM (surchargeable)
    GLOSSARY_EXTRA: list = []                        # entrées de glossaire ajoutées par un adaptateur : (id, titre, texte)
    LINEAGE_COLS = ["visual_id", "visual_title", "kpi_id", "kpi_name", "fact_columns", "etl_steps",
                    "cleaning_rules", "profiler_issues", "raw_columns", "raw_sources"]
    LIMITATIONS = ["Upstream agent outputs in this notebook are simulated; the Auditor logic is independent of that."]

    def __init__(self, out_dir: str | Path, cfg: AuditorConfig | None = None, llm: LLM | None | str = "auto", log: Callable = print):
        self.out, self.cfg, self.log = Path(out_dir), cfg or AuditorConfig.from_env(), log
        self.llm = self._load_llm() if llm == "auto" else llm
        self.llm_note = getattr(self, "llm_note", "")
        self.retriever: Retriever | None = None
        self.xai = pd.DataFrame()

    # ---- B.1 -----------------------------------------------------------------------------
    def rd(self, name: str, num=()) -> pd.DataFrame:
        """Lit un CSV en texte (garde les codes comme 150910), puis convertit les colonnes numériques."""
        df = pd.read_csv(self.out / name, dtype=str)
        for c in num:
            df[c] = pd.to_numeric(df[c])
        return df

    def save(self, df: pd.DataFrame, name: str) -> None:
        df.to_csv(self.out / name, index=False)
        self.log(f"  saved {name:<28s} {len(df):>6} rows x {df.shape[1]:>2} cols")

    def _load_llm(self) -> LLM | None:
        if self.cfg.llm_provider in ("", "none"):
            self.llm_note = "aucun modèle configuré (AUDITOR_LLM_PROVIDER) : réponses « preuves seules »"
            return None
        try:
            llm = LLM(self.cfg).load()
            self.llm_note = f"{llm.name} ({llm.provider})"
            return llm
        except Exception as exc:  # noqa: BLE001 - repli prévu par le notebook : preuves seules
            self.llm_note = f"modèle non chargé ({type(exc).__name__}: {str(exc)[:100]}) : réponses « preuves seules »"
            return None

    def load(self) -> "Auditor":
        self.prof = self.rd("profiler_report.csv", num=["affected_rows", "pct_rows"]).fillna("")
        self.rules_df = self.rd("cleaning_rules.csv").fillna("")
        self.clog = self.rd("cleaning_log.csv", num=["confidence"]).fillna("")
        self.clog["needs_human_validation"] = self.clog.needs_human_validation == "True"
        self.etl = self.rd("etl_log.csv", num=["step_no", "rows_in", "rows_out"]).fillna("")
        self.collin = self.rd("column_lineage.csv").fillna("")
        self.kpi_cat = self.rd("kpi_catalog.csv").fillna("")
        self.kpi_val = self.rd("kpi_values.csv", num=["value", "rows_used"]).fillna("")
        self.ins = self.rd("analyst_insights.csv", num=["metric_value", "threshold", "rows_used"]).fillna("")
        self.ins["rule_triggered"] = self.ins.rule_triggered == "True"
        self.vis = self.rd("dashboard_visuals.csv").fillna("")
        self.orch = self.rd("orchestrator_runs.csv", num=["step_no", "retries"]).fillna("")
        self.raw = self.rd("raw_trade_data.csv") if (self.out / "raw_trade_data.csv").exists() else pd.DataFrame({"source_file": []})
        if (self.out / "clean_trade_data.csv").exists():     # table nettoyée au niveau des lignes (jeu du notebook)
            self.clean = self.rd("clean_trade_data.csv", num=["value_tnd", "quantity", "weight_kg"]).fillna("")
            self.clean["year"] = self.clean.date.str[:4].astype(int)
            self.clean["month"] = self.clean.date.str[5:7].astype(int)
            self.BASE = self.clean[self.clean.quality_flag != "quarantined"]
        else:
            self.clean, self.BASE = pd.DataFrame(), pd.DataFrame()
        self.RULE_ISSUE = self.rules_df.set_index("rule_id")["profiler_issue_id"].to_dict()
        self.log("Loaded: " + str({k: len(v) for k, v in dict(profiler=self.prof, rules=self.rules_df, cleaning_log=self.clog, etl=self.etl,
                 kpi_catalog=self.kpi_cat, kpi_values=self.kpi_val, insights=self.ins, visuals=self.vis, orchestrator=self.orch).items()}))
        return self

    # ---- B.2 : journal d'audit unifié ------------------------------------------------------
    def fmt_kpi(self, kid: str, v: float, scope_type: str = "all") -> str:
        """Notebook : K04 -> 4 décimales, K05 -> pourcentage, sinon « 1,234 TND » ; ici d'après l'unité du catalogue."""
        unit = self.kpi_cat.loc[self.kpi_cat.kpi_id == kid, "unit"].iloc[0] if "unit" in self.kpi_cat and (self.kpi_cat.kpi_id == kid).any() else ""
        if unit == "ratio":
            return f"{v:.4f}"
        if unit == "percent":
            return f"{v * 100:.2f}%"
        txt = f"{v:,.0f}" if float(v).is_integer() or abs(v) >= 1000 else f"{v:,.2f}"
        return f"{txt} {unit}".strip()

    def correction_subject(self, r) -> str:
        return f"row {r.row_id}"

    def correction_summary(self, r) -> str:
        """Texte du journal pour une correction (notebook : une ligne, une règle) ; surchargé pour les étapes du vrai agent."""
        who = f" by {r.approver}" if r.approver else ""
        return (f"Row {r.row_id} ({r.declaration_id}): rule {r.rule_id} changed {r.column} from '{r.before}' to '{r.after}'. "
                f"Reason: {r.reason}. Confidence {r.confidence}. Human validation required: "
                f"{'yes' if r.needs_human_validation else 'no'}; status {r.approval_status}{who}.")

    def correction_question(self, e) -> str:
        return f"Why was the {e.column} of row {e.row_id} changed from '{e.before}' to '{e.after}'?"

    def insight_summary(self, r) -> str:
        return (f"Insight {r.insight_id} (KPI {r.kpi_id}): {r.text} Method {r.method}, flow {r.flow}, years {r.years}, "
                f"filter [{r.filter_expr}], {r.rows_used} rows. Metric {r.metric_name} = {r.metric_value}. "
                f"Rule: {r.rule_text} Threshold {r.threshold}. Rule triggered: {'yes' if r.rule_triggered else 'no'}. "
                f"Recommendation: {r.recommendation}")

    def value_label(self, r) -> str:
        """Libellé d'une valeur publiée dans les preuves (notebook : la période)."""
        return str(r.period)

    def review_priority(self, queue):
        return np.where(queue.rule_id.isin(self.QUARANTINE_RULES), "high", np.where(queue.confidence < 0.8, "medium", "low"))

    def build_trail(self) -> pd.DataFrame:
        events = []

        def ev(eid, agent, etype, subject, summary, when):
            events.append(dict(event_id=eid, agent=agent, event_type=etype, subject=subject, summary=summary, timestamp=when))

        for r in self.prof.itertuples():
            ev(r.issue_id, "Data Profiler", "issue_detected", r.column,
               f"Data Profiler detected {r.issue_type} in column {r.column}: {r.affected_rows} rows ({r.pct_rows}%), severity {r.severity}. "
               + (f"Example: {r.example}. " if str(r.example).strip() else "") + f"Recommended action: {str(r.recommended_action).rstrip('.')}.", r.detected_at)
        for r in self.rules_df.itertuples():
            ev(r.rule_id, "Quality & Cleaning", "rule_definition", r.rule_id,
               f"Cleaning rule {r.rule_id}: {str(r.description).rstrip('.')}. Method: {r.method}."
               + (f" Addresses profiler issue {r.profiler_issue_id}." if str(r.profiler_issue_id).strip() else ""), "")
        for r in self.clog.itertuples():
            ev(r.event_id, "Quality & Cleaning", "correction", self.correction_subject(r), self.correction_summary(r), r.timestamp)
        for r in self.etl.itertuples():
            ev(r.step_id, "ETL / Data Engineering", "etl_step", r.output_table,
               f"ETL step {r.step_no} ({r.operation}): {r.description} Input: {r.input_tables}. Output: {r.output_table}. "
               f"Rows {r.rows_in} to {r.rows_out}. Columns: {r.columns_used}.", r.timestamp)
        for r in self.kpi_cat.itertuples():
            ev(r.kpi_id, "Semantic & KPI", "kpi_definition", r.name,
               f"KPI {r.kpi_id} {r.name} is defined in DAX as {r.dax}. It depends on {r.fact_columns}. {r.description}", "")
        for r in self.ins.itertuples():
            ev(r.insight_id, self.INSIGHT_AGENT, "insight", r.kpi_id, self.insight_summary(r), r.created_at)
        for r in self.vis.itertuples():
            ev(r.visual_id, "Dashboard Generator", "visual", r.title,
               f"Visual {r.visual_id} ({r.visual_type}) '{r.title}' on page {r.page} shows {r.kpi_ids} by {r.dimension}. "
               f"Chosen because: {r.chosen_because}", r.generated_at)
        for r in self.orch.itertuples():
            ev(f"O{r.step_no:02d}", "Orchestrator", "run_step", r.agent,
               f"Run {r.run_id} step {r.step_no}: agent {r.agent} finished with status {r.status} after {r.retries} retries"
               f"{(' (' + r.error + ')') if r.error else ''}. Human validation required: {r.human_validation_required}"
               f"{(' - ' + r.human_validation_status) if r.human_validation_status else ''}. Outputs: {r.outputs}.", r.started_at)
        self.trail = pd.DataFrame(events, columns=["event_id", "agent", "event_type", "subject", "summary", "timestamp"])
        self.save(self.trail, "audit_trail.csv")
        self.TRAIL = self.trail.set_index("event_id")["summary"].to_dict()
        return self.trail

    # ---- B.3 : lignée, provenance, réconciliation ------------------------------------------
    def lineage_for(self, kpi_id, dimension="none"):
        k = self.kpi_cat[self.kpi_cat.kpi_id == kpi_id].iloc[0]
        cols = split(k.fact_columns) + self.DIM_COLS.get(dimension, [])
        cl = self.collin[self.collin.fact_column.isin(cols)]
        rules = sorted({r for s in cl.cleaning_rules for r in split(s)})
        return dict(kpi_id=kpi_id, kpi_name=k["name"], fact_columns="|".join(cols),
                    etl_steps="|".join(sorted({e for s in cl.etl_step for e in split(s)})),
                    cleaning_rules="|".join(rules),
                    profiler_issues="|".join(sorted({self.RULE_ISSUE[r] for r in rules if self.RULE_ISSUE.get(r)})),
                    raw_columns="|".join(sorted({c for s in cl.raw_columns for c in split(s)})),
                    raw_sources="|".join(sorted(self.raw.source_file.dropna().unique())))

    def build_lineage(self) -> pd.DataFrame:
        rows = []
        for v in self.vis.itertuples():
            for kid in split(v.kpi_ids.replace(",", "|")):
                if (self.kpi_cat.kpi_id == kid).any():
                    rows.append(dict(visual_id=v.visual_id, visual_title=v.title, **self.lineage_for(kid, v.dimension)))
        self.lineage = pd.DataFrame(rows, columns=None if rows else self.LINEAGE_COLS)
        self.save(self.lineage, "lineage.csv")
        return self.lineage

    def prov_stats(self, sub):
        ids = set(sub.row_id)
        e = self.clog[self.clog.row_id.isin(ids)]
        norm = set(e[e.rule_id != "R-FX-01"].row_id)                         # any format normalisation
        judg = set(e[e.needs_human_validation].row_id)                       # fuzzy match / negative value: needs judgment
        pend = set(e[e.needs_human_validation & (e.approval_status == "pending")].row_id)
        tot = sub.value_tnd.sum()
        return dict(rows=len(sub), normalised_rows=len(norm), judgment_rows=len(judg), pending_rows=len(pend),
                    judgment_value_pct=round(100 * sub[sub.row_id.isin(judg)].value_tnd.sum() / tot, 1) if tot else 0.0,
                    top_rules=e[e.rule_id != "R-FX-01"].rule_id.value_counts().head(3).to_dict())

    def subset_for(self, kpi_id, year, country=None):
        s = self.BASE[self.BASE.year == year] if kpi_id != "K05" else self.BASE[self.BASE.year.isin([year - 1, year])]
        if kpi_id in ("K01", "K05"):
            s = s[s.flow == "Export"]
        if kpi_id == "K02":
            s = s[s.flow == "Import"]
        if country:
            s = s[s.country_iso == country]
        return s

    def recompute(self, kpi_id, scope_type, scope_value, period):
        """Recalcul indépendant d'une valeur publiée (K01-K05 du jeu du notebook) ; None = non recalculable."""
        if self.BASE.empty:
            return None
        y = int(period)
        c = scope_value if scope_type == "country" else None

        def tot(flow, yr):
            s = self.BASE[(self.BASE.year == yr) & (self.BASE.flow == flow)]
            if c:
                s = s[s.country_iso == c]
            return s.value_tnd.sum()
        if kpi_id == "K01":
            return tot("Export", y)
        if kpi_id == "K02":
            return tot("Import", y)
        if kpi_id == "K03":
            return tot("Export", y) - tot("Import", y)
        if kpi_id == "K04":
            return tot("Export", y) / tot("Import", y)
        if kpi_id == "K05":
            return (tot("Export", y) - tot("Export", y - 1)) / tot("Export", y - 1)
        return None

    def insight_rows(self, i):
        """Nombre de lignes que l'analyste dit avoir utilisées, recompté indépendamment (None : pas de table au niveau des lignes)."""
        if self.BASE.empty:
            return None
        return len(self.BASE.query(f"flow == '{i.flow}' and year in {[int(y) for y in split(i.years)]}" + (f" and {i.filter_expr}" if i.filter_expr else "")))

    def reconcile(self) -> pd.DataFrame:
        rec = []
        for r in self.kpi_val.itertuples():
            mine = self.recompute(r.kpi_id, r.scope_type, r.scope_value, r.period)
            if mine is None:
                rec.append(dict(kpi_id=r.kpi_id, scope_type=r.scope_type, scope_value=r.scope_value, period=r.period,
                                published=r.value, recomputed=np.nan, abs_diff=np.nan, status="NOT_RECOMPUTED"))
                continue
            rec.append(dict(kpi_id=r.kpi_id, scope_type=r.scope_type, scope_value=r.scope_value, period=r.period,
                            published=r.value, recomputed=mine, abs_diff=abs(mine - r.value),
                            status="MATCH" if abs(mine - r.value) <= 1e-6 * max(1, abs(r.value)) else "MISMATCH"))
        for r in self.ins.itertuples():
            n = self.insight_rows(r)
            if n is None:
                continue
            rec.append(dict(kpi_id=r.insight_id, scope_type="insight_rows", scope_value=r.filter_expr, period=r.years,
                            published=r.rows_used, recomputed=n, abs_diff=abs(n - r.rows_used),
                            status="MATCH" if n == r.rows_used else "MISMATCH"))
        self.recon = pd.DataFrame(rec, columns=["kpi_id", "scope_type", "scope_value", "period", "published", "recomputed", "abs_diff", "status"])
        self.save(self.recon, "reconciliation.csv")
        self.log(str(self.recon.status.value_counts().to_dict()))
        return self.recon

    # ---- B.4 : RAG ---------------------------------------------------------------------------
    def index(self) -> None:
        self.glossary_df = pd.DataFrame(list(GLOSSARY) + list(self.GLOSSARY_EXTRA), columns=["id", "title", "text"])
        self.save(self.glossary_df, "trade_glossary.csv")
        self.retriever = Retriever(self.out / "chroma", self.cfg.retrieval)
        self.retriever.build("trade_glossary", self.glossary_df.id.tolist(), (self.glossary_df.title + ". " + self.glossary_df.text).tolist(),
                             [{"title": t} for t in self.glossary_df.title])
        self.retriever.build("audit_trail", self.trail.event_id.tolist(), self.trail.summary.tolist(),
                             [{"agent": a, "event_type": t, "subject": str(s)} for a, t, s in zip(self.trail.agent, self.trail.event_type, self.trail.subject)])
        self.log(f"indexed ({self.retriever.backend}): trade_glossary={len(self.glossary_df)} audit_trail={len(self.trail)}")

    def retrieve(self, query, name, k=3):
        return self.retriever.query(name, query, k)

    def glossary_evidence(self, query, k=2):
        return [dict(id=h["id"], text=h["text"]) for h in self.retrieve(query, "trade_glossary", k)]

    # ---- B.6 : preuves, réponses ancrées -----------------------------------------------------
    def E(self, eid, text=None):
        return dict(id=eid, text=text or self.TRAIL[eid])

    def answer_with_evidence(self, question, evidence):
        pack = "\n".join(f"[{e['id']}] {e['text']}" for e in evidence)
        prompt = f"QUESTION: {question}\n\nEVIDENCE:\n{pack}\n\nAnswer in 3 to 5 short sentences, each ending with an evidence id in brackets:"
        if not self.llm:                                     # no LLM loaded: plain evidence-only answer (already cited)
            text = template_answer(evidence)
            return text, dict(ok=True, status="template_fallback", n_dropped=0, cited=sorted(set(re.findall(CITE_RE, text))), issues=[])
        feedback, best, llm_issue = "", None, []
        for _ in range(2):
            try:
                raw_text = self.llm(self.SYSTEM, prompt + feedback)
            except Exception as exc:  # noqa: BLE001 - un appel qui échoue ne doit pas arrêter l'audit : repli sur les preuves
                llm_issue = [f"LLM call failed: {type(exc).__name__}: {str(exc)[:80]}"]
                break
            text, info = ground_answer(raw_text, evidence)
            if best is None or len(info["kept"]) > len(best[1]["kept"]):
                best = (text, info)
            if info["status"] == "verified" or len(info["kept"]) > len(info["dropped"]):
                break
            why = "; ".join(f"'{s[:60]}...' ({r})" for s, r in info["dropped"][:2])
            feedback = f"\n\nYour previous answer contained unsupported or cut-off sentences: {why}. Write a shorter answer using only the evidence."
        if best is None:
            best = ("", dict(status="failed", kept=[], dropped=[]))
        text, info = best
        if info["status"] == "failed":                      # never return an empty answer: fall back to the raw evidence
            text, info = ground_answer(template_answer(evidence), evidence)
            info["status"] = "template_fallback"
        v = dict(ok=info["status"] != "failed", status=info["status"], n_dropped=len(info["dropped"]),
                 cited=sorted(set(re.findall(CITE_RE, text))), issues=(llm_issue + [r for _, r in info["dropped"]])[:4])
        return text, v

    # preuves propres à chaque question XAI ---------------------------------------------------
    def provenance_kpi(self, kpi_id, last_period) -> str:
        ps = self.prov_stats(self.subset_for(kpi_id, int(last_period)))
        return (f"Provenance of {kpi_id} for {last_period}: {ps['rows']} rows feed the figure; {ps['normalised_rows']} of them only needed label or format "
                f"normalisation, and {ps['judgment_rows']} received a judgment-based correction (fuzzy match or negative value) representing "
                f"{ps['judgment_value_pct']}% of its value; {ps['pending_rows']} rows still have a correction pending human validation. "
                f"Most frequent rules: {ps['top_rules']}.")

    def provenance_insight(self, i) -> str:
        q = f"flow == '{i.flow}' and year in {[int(y) for y in split(i.years)]}" + (f" and {i.filter_expr}" if i.filter_expr else "")
        ps = self.prov_stats(self.BASE.query(q))
        return (f"Provenance of insight {i.insight_id}: it uses {ps['rows']} rows; {ps['normalised_rows']} only needed label or format normalisation and "
                f"{ps['judgment_rows']} received a judgment-based correction ({ps['judgment_value_pct']}% of their value); "
                f"{ps['pending_rows']} have a correction pending human validation.")

    def _all_rows(self, kpi_id):
        v = self.kpi_val[self.kpi_val.kpi_id == kpi_id]
        a = v[v.scope_type == "all"]
        return (a if len(a) else v).sort_values("period")

    def kpi_evidence(self, kpi_id, dimension="none"):
        ln = self.lineage_for(kpi_id, dimension)
        n = kpi_id[1:]
        vals = self._all_rows(kpi_id)
        ev_ = [self.E(kpi_id)]
        if len(vals):
            ev_.append(self.E(f"KV{n}", f"Published values of {kpi_id}: " + "; ".join(
                f"{self.value_label(r)} = {self.fmt_kpi(kpi_id, r.value, r.scope_type)} from {r.rows_used} rows" for r in vals.itertuples())))
        r_ = self.recon[(self.recon.kpi_id == kpi_id) & (self.recon.scope_type == "all")]
        if len(r_):
            ev_.append(self.E(f"RC{n}", f"Independent recomputation of {kpi_id} from the cleaned data: {(r_.status == 'MATCH').sum()} of {len(r_)} "
                                        f"published overall values match; largest absolute difference {r_.abs_diff.max():.6f}."))
        if len(vals):
            ev_.append(self.E(f"PV{n}", self.provenance_kpi(kpi_id, vals.period.max())))
        ev_.append(self.E(f"LN{n}", f"Lineage of {kpi_id} {ln['kpi_name']}: fact columns {ln['fact_columns']}; ETL steps {ln['etl_steps']}; "
                                    f"cleaning rules {ln['cleaning_rules']}; profiler issues {ln['profiler_issues']}; raw columns {ln['raw_columns']}; "
                                    f"raw sources {ln['raw_sources']}."))
        ops = self.etl.set_index("step_id")["operation"].to_dict()
        for e_id in split(ln["etl_steps"]):
            if ops.get(e_id) in ("filter", "load", "build_fact") and e_id in self.TRAIL:   # notebook : E01 (filtre) et E05 (table de faits)
                ev_.append(self.E(e_id))
        return ev_

    def _cleaning_step_id(self):
        m = self.orch[self.orch.agent.str.contains("clean", case=False)]
        return f"O{int(m.step_no.iloc[0]):02d}" if len(m) else None

    def explain_correction(self, event_id):
        e = self.clog[self.clog.event_id == event_id].iloc[0]
        q = self.correction_question(e)
        evid = [self.E(event_id)]
        evid += [self.E(i) for i in (e.profiler_issue_id, e.rule_id, self._cleaning_step_id()) if i and i in self.TRAIL]
        return q, evid + self.glossary_evidence(f"{e.rule_description} {e.column}", 2)

    def explain_kpi(self, kpi_id):
        row = self.kpi_cat.loc[self.kpi_cat.kpi_id == kpi_id].iloc[0]
        return (f"Why does the KPI {kpi_id} exist, how is it computed and where does its figure come from?",
                self.kpi_evidence(kpi_id) + self.glossary_evidence(row["name"] + " " + row["description"], 1))

    def explain_insight(self, insight_id):
        i = self.ins[self.ins.insight_id == insight_id].iloc[0]
        kid = i.kpi_id
        evid = [self.E(insight_id), self.E(kid)] if kid in self.TRAIL else [self.E(insight_id)]
        evid.append(self.E(f"PI{insight_id[1:]}", self.provenance_insight(i)))
        vals = self._all_rows(kid)
        if len(vals):
            kv = vals.iloc[-1]
            evid.append(self.E(f"KV{kid[1:]}", f"Published {kid} for {kv.period} = {self.fmt_kpi(kid, kv.value, kv.scope_type)} from {kv.rows_used} rows."))
        r_ = self.recon[(self.recon.kpi_id == kid) & (self.recon.scope_type == "all")]
        if len(r_):
            n_ok = int((r_.status == "MATCH").sum())
            evid.append(self.E(f"RC{kid[1:]}", f"Independent recomputation of {kid}: {n_ok} of {len(r_)} published overall values match."))
        return f"Where does the insight {insight_id} come from?", evid + self.glossary_evidence(f"{i.method} {i.text}", 1)

    def explain_recommendation(self, insight_id):
        i = self.ins[self.ins.insight_id == insight_id].iloc[0]
        return (f"How is the recommendation of insight {insight_id} justified?",
                [self.E(insight_id), self.E(f"RL{insight_id[1:]}", f"Decision rule for {insight_id}: {i.rule_text} Observed {i.metric_name} = {i.metric_value}; "
                                                                  f"threshold = {i.threshold}; rule triggered = {'yes' if i.rule_triggered else 'no'}. Recommendation issued: {i.recommendation}")]
                + self.glossary_evidence(i.rule_text, 1))

    # ---- B.7 : les quatre questions XAI ------------------------------------------------------
    def _run(self, qtype, subject, builder):
        q, evid = builder(subject)
        t0 = time.time()
        text, v = self.answer_with_evidence(q, evid)
        self.xai_rows.append(dict(question_type=qtype, subject_id=subject, question=q, answer=text, verified=v["ok"], status=v["status"], n_dropped=v["n_dropped"],
                                  cited_ids="|".join(v["cited"]), verification_issues="; ".join(v["issues"]),
                                  evidence_ids="|".join(e["id"] for e in evid), model=(self.llm.name if self.llm else "template-fallback"),
                                  seconds=round(time.time() - t0, 1), timestamp=dt.datetime.now().isoformat(timespec="seconds")))
        self.log(f"[{qtype} | {subject}] status={v['status']} (removed {v['n_dropped']} unsupported sentences)")

    def explain_all(self) -> pd.DataFrame:
        self.xai_rows: list[dict] = []
        sel = []                                              # one correction per rule: human-validation cases first
        for _, g in self.clog.groupby("rule_id"):
            sel.append(g.sort_values(["needs_human_validation", "confidence"], ascending=[False, True]).iloc[0])
        sel = sorted(sel, key=lambda r: (not r.needs_human_validation, r.rule_id))[:self.cfg.n_corr_explain]
        for r in sel:
            self._run("correction", r.event_id, self.explain_correction)
        for kid in self.kpi_cat.kpi_id[:self.cfg.n_kpi_explain]:
            self._run("kpi", kid, self.explain_kpi)
        for iid in self.ins.insight_id[:self.cfg.n_insight_explain]:
            self._run("insight", iid, self.explain_insight)
            if str(self.ins.loc[self.ins.insight_id == iid, "recommendation"].iloc[0]).strip():   # constat sans recommandation : rien à justifier
                self._run("recommendation", iid, self.explain_recommendation)
        cols = ["question_type", "subject_id", "question", "answer", "verified", "status", "n_dropped", "cited_ids", "verification_issues",
                "evidence_ids", "model", "seconds", "timestamp"]
        self.xai = pd.DataFrame(self.xai_rows, columns=cols)
        self.save(self.xai, "xai_explanations.csv")
        self.log(f"Status of the {len(self.xai)} answers: {self.xai.status.value_counts().to_dict()}")
        return self.xai

    # ---- B.8 : « Pourquoi ? » libre ----------------------------------------------------------
    def ask(self, question, k=4):
        evid, seen = [], set()

        def add(items):
            for it in items:
                if it and it["id"] not in seen:
                    seen.add(it["id"])
                    evid.append(it)

        m = re.search(r"row\s*#?\s*(\d+)", question, re.I)
        if m:
            if self._cleaning_step_id():
                add([self.E(self._cleaning_step_id())])
            for r in self.clog[self.clog.row_id == m.group(1)].itertuples():
                if r.rule_id != "R-FX-01" or "convert" in question.lower() or "tnd" in question.lower():
                    add([self.E(r.event_id)])
        for i in re.findall(ID_RE, question):
            if i in self.TRAIL:
                add([self.E(i)])
        ql = question.lower()
        for r in self.kpi_cat.itertuples():
            if r.kpi_id.lower() in ql or r.name.lower() in ql:
                add(self.kpi_evidence(r.kpi_id))
        if not m:                                             # a named row is answered from its own events only
            add([dict(id=h["id"], text=h["text"]) for h in self.retrieve(question, "audit_trail", k)])
        add(self.glossary_evidence(question, 2))
        text, v = self.answer_with_evidence(question, evid[:14])
        return text, v

    # ---- B.9 : file de validation humaine + rapport ------------------------------------------
    def review_queue(self) -> pd.DataFrame:
        queue = self.clog[self.clog.needs_human_validation].copy()
        queue["priority"] = self.review_priority(queue)
        queue = queue.sort_values(["approval_status", "priority"], ascending=[False, True])[
            ["event_id", "row_id", "declaration_id", "column", "before", "after", "rule_id", "confidence", "approval_status", "approver", "priority", "reason"]]
        self.queue = queue
        self.save(queue, "human_review_queue.csv")
        return queue

    def report(self) -> str:
        def md_table(df):
            try:
                return df.to_markdown(index=False)
            except ImportError:                              # tabulate absent
                return df.to_string(index=False)

        run_id = self.orch.run_id.iloc[0] if len(self.orch) else "n/a"
        cl_nofx = self.clog[self.clog.rule_id != "R-FX-01"]
        recon_all = self.recon[self.recon.scope_type == "all"].assign(published=lambda d: d.published.round(4), recomputed=lambda d: d.recomputed.round(4))
        n_rc = int((self.recon.status != "NOT_RECOMPUTED").sum())
        retr = self.retriever.backend if self.retriever else "n/a"
        parts = [f"# XAI Report — BI Flow run {run_id}\n",
                 f"*Generated {dt.datetime.now().isoformat(timespec='seconds')} by the BI Auditor / XAI agent. "
                 f"Explanations written by `{self.llm.name if self.llm else 'template-fallback'}` and verified against the evidence. "
                 f"Retrieval: {retr}.*\n",
                 "## 1. Run summary\n", md_table(self.orch[["step_no", "agent", "status", "retries", "human_validation_required", "human_validation_status"]]),
                 "\n## 2. Data quality and corrections\n", md_table(self.prof[["issue_id", "issue_type", "column", "affected_rows", "pct_rows", "severity"]]),
                 "\nCorrections by rule" + (" (excluding currency conversion)" if (self.clog.rule_id == "R-FX-01").any() else "") + ":\n",
                 md_table(cl_nofx.groupby("rule_id").agg(corrections=("event_id", "count"), pending=("approval_status", lambda s: (s == "pending").sum())).reset_index())]
        if len(self.clean):
            parts.append(f"\nRows quarantined (excluded from the model): **{(self.clean.quality_flag == 'quarantined').sum()}**; "
                         f"rows pending review: **{(self.clean.quality_flag == 'pending_review').sum()}**.\n")
        parts += ["## 3. Reconciliation of published figures\n",
                  f"{(self.recon.status == 'MATCH').sum()} of {n_rc} recomputable published figures were recomputed independently and match"
                  f" ({len(self.recon) - n_rc} not recomputable by the Auditor).\n",
                  md_table(recon_all[["kpi_id", "period", "published", "recomputed", "status"]])]

        def section(title, qtype):
            out = [f"\n## {title}\n"]
            for r in self.xai[self.xai.question_type == qtype].itertuples():
                flag = {"verified": "verified", "filtered": f"verified after removing {r.n_dropped} unsupported sentence(s)",
                        "template_fallback": ("evidence-only fallback (LLM answer unusable)" if self.llm else "evidence only (no LLM configured)")}.get(r.status, r.status)
                out.append(f"**{r.subject_id}** — *{r.question}*\n\n{r.answer}\n\n`{flag}; evidence: {r.evidence_ids}`\n")
            return out

        parts += section("4. Why were values corrected?", "correction")
        parts += section("5. Why these KPIs?", "kpi")
        parts += section("6. Where do the insights come from?", "insight")
        parts += section("7. How are the recommendations justified?", "recommendation")
        parts += ["\n## 8. Lineage (visual → KPI → source)\n",
                  md_table(self.lineage[["visual_id", "visual_title", "kpi_id", "etl_steps", "cleaning_rules", "profiler_issues"]]),
                  "\n## 9. Human review queue (top 10)\n",
                  md_table(self.queue.head(10)[["event_id", "row_id", "column", "before", "after", "rule_id", "confidence", "approval_status", "priority"]]),
                  "\n## 10. Limitations\n"]
        parts += [f"- {x}\n" for x in self.LIMITATIONS]
        parts += ["- LLM text is verified for cited ids and numbers, not for every semantic nuance; the deterministic evidence is the source of truth.\n"
                  "- Corrections still marked `pending` are provisional until a human approves them.\n"]
        text = "\n".join(parts)
        (self.out / "xai_report.md").write_text(text, encoding="utf-8")
        self.log(f"saved xai_report.md ({len(text):,} characters)")
        return text

    # ---- tout enchaîner ---------------------------------------------------------------------
    def prepare(self) -> "Auditor":
        """B.1 à B.4 : de quoi répondre (journal, lignée, réconciliation, index). Rapide, sans LLM."""
        self.load()
        self.build_trail()
        self.build_lineage()
        self.reconcile()
        self.index()
        return self

    def run(self) -> dict:
        self.prepare()
        self.explain_all()
        self.review_queue()
        self.report()
        recon = self.recon.status.value_counts().to_dict()
        return {"events": len(self.trail), "reconciliation": recon, "explanations": self.xai.status.value_counts().to_dict(),
                "review_queue": len(self.queue), "pending_review": int((self.queue.approval_status == "pending").sum()),
                "model": self.llm.name if self.llm else "template-fallback", "model_note": self.llm_note,
                "retrieval": self.retriever.backend, "retrieval_note": self.retriever.note}
