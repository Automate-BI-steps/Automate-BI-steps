"""Données simulées des 7 agents amont : partie A du notebook `BIAuditor_XAI.ipynb` de Dhouha.

Le notebook simule un jeu de déclarations import/export (valeurs sales : orthographes, formats de
date, devises, doublons...) puis le nettoie / modélise / agrège VRAIMENT, pour que chaque journal
soit cohérent avec les chiffres. Les sorties sont les CSV que l'Auditor lit (le « contrat »).

Ici : le même code, sous forme de module (aucune exécution à l'import). Il sert de données de
démonstration (`python -m src.agents.auditor.cli demo <dossier>`) et de jeu de test de l'Auditor sur
le contrat pour lequel il a été écrit. Il n'est PAS utilisé dans le pipeline réel, où
`adapter.py` fabrique ces mêmes CSV à partir de la sortie des vrais agents.
"""
from __future__ import annotations

import datetime as dt
import difflib
import os
import random
import re
import unicodedata
from collections import defaultdict

import numpy as np
import pandas as pd

SEED = 42
N_BASE_ROWS = 600                  # size of the simulated raw trade file (before duplicates)
T0 = dt.datetime(2026, 10, 28, 9, 0, 0)


def ts(seconds):
    return (T0 + dt.timedelta(seconds=float(seconds))).isoformat(timespec="seconds")


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_txt(s):
    return re.sub(r"[.\s]+", "", strip_accents(str(s)).lower())


COUNTRIES = {
    "TN": ("Tunisia", "Africa"), "FR": ("France", "Europe"), "IT": ("Italy", "Europe"),
    "DE": ("Germany", "Europe"), "ES": ("Spain", "Europe"), "LY": ("Libya", "Africa"),
    "DZ": ("Algeria", "Africa"), "MA": ("Morocco", "Africa"), "US": ("United States", "Americas"),
    "CN": ("China", "Asia"), "TR": ("Turkey", "Asia"), "AE": ("United Arab Emirates", "Asia"),
    "GB": ("United Kingdom", "Europe"), "EG": ("Egypt", "Africa"), "SA": ("Saudi Arabia", "Asia"),
}
VARIANTS = {
    "TN": ["Tunisia", "Tunisie", "TUNISIA", "TUN"], "FR": ["France", "FRANCE", "Fr."],
    "IT": ["Italy", "Italie", "ITALY"], "DE": ["Germany", "Allemagne", "Deutschland"],
    "ES": ["Spain", "Espagne", "SPAIN"], "LY": ["Libya", "Libye"], "DZ": ["Algeria", "Algérie", "Algerie"],
    "MA": ["Morocco", "Maroc"], "US": ["United States", "USA", "U.S.A", "États-Unis"],
    "CN": ["China", "Chine"], "TR": ["Turkey", "Turquie"],
    "AE": ["UAE", "United Arab Emirates", "Emirats Arabes Unis"],
    "GB": ["United Kingdom", "UK", "Royaume-Uni"], "EG": ["Egypt", "Egypte"], "SA": ["Saudi Arabia", "Arabie Saoudite"],
}
TYPOS = {"Frnace": "FR", "Germny": "DE", "Itally": "IT", "Algerai": "DZ",
         "Moroco": "MA", "Turkye": "TR", "Tunsia": "TN", "Egpyt": "EG"}
TYPO_BY_ISO = {v: k for k, v in TYPOS.items()}
ALIAS = {norm_txt(v): iso for iso, vs in VARIANTS.items() for v in vs + [iso, COUNTRIES[iso][0]]}

PRODUCTS = {
    "150910": "Virgin olive oil", "080410": "Dates, fresh or dried", "610910": "T-shirts, cotton, knitted",
    "854442": "Insulated electric conductors", "270900": "Crude petroleum oils", "100199": "Wheat and meslin",
    "300490": "Medicaments, retail packaged", "870323": "Passenger cars 1500-3000cc", "252329": "Portland cement",
}
EXPORT_PRODUCTS = ["150910", "080410", "610910", "854442"]
IMPORT_PRODUCTS = ["270900", "100199", "300490", "870323", "252329"]
BASE_EUR = {"150910": 60000, "080410": 25000, "610910": 18000, "854442": 40000, "270900": 90000,
            "100199": 45000, "300490": 35000, "870323": 28000, "252329": 15000}
EXP_MKT = {"FR": .28, "IT": .22, "DE": .15, "ES": .08, "LY": .10, "US": .06, "GB": .04, "AE": .04, "DZ": .03}
IMP_MKT = {"CN": .25, "IT": .15, "FR": .14, "TR": .12, "DE": .10, "DZ": .10, "ES": .05, "AE": .04, "EG": .03, "SA": .02}
CCY_BY_ISO = {"FR": "EUR", "IT": "EUR", "DE": "EUR", "ES": "EUR", "US": "USD", "CN": "USD", "AE": "USD", "SA": "USD"}
FX = {"EUR": 3.35, "USD": 3.10, "TND": 1.0}
CCY_VARIANTS = {"EUR": ["EUR", "eur", "€", "Euro"], "USD": ["USD", "usd", "$", "US$"], "TND": ["TND", "tnd", "DT", "Dinar"]}
FLOW_VARIANTS = {"Export": ["Export", "export", "EXPORT", "exp", "Exp."], "Import": ["Import", "import", "IMPORT", "imp", "Imp."]}
FLOW_MAP = {norm_txt(v): k for k, vs in FLOW_VARIANTS.items() for v in vs + [k]}
CCY_MAP = {norm_txt(v): k for k, vs in CCY_VARIANTS.items() for v in vs + [k]}
SOURCES = ["customs_declarations.csv", "erp_export.xlsx", "manual_sheet.xlsx"]


# ---- shared parsing helpers (used by the Profiler and the Cleaning agent) ----
def parse_date(s):
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%b %d, %Y"):
        try:
            return dt.datetime.strptime(str(s), fmt).date(), fmt
        except ValueError:
            pass
    return None, None


def hs_digits(s):
    return re.sub(r"\D", "", str(s))


def resolve_country(raw):
    if pd.isna(raw) or str(raw).strip() == "":
        return None, "missing", 0.0
    k = norm_txt(raw)
    if k in ALIAS:
        return ALIAS[k], "alias_lookup", 1.0
    m = difflib.get_close_matches(k, list(ALIAS), n=1, cutoff=0.75)
    if m:
        return ALIAS[m[0]], "fuzzy_match", round(difflib.SequenceMatcher(None, k, m[0]).ratio(), 2)
    return None, "unresolved", 0.0


ISSUE_ORDER = ["duplicate_declaration", "missing_country", "country_spelling", "hs_format", "hs_invalid",
               "date_format", "flow_label", "currency_label", "negative_value", "mixed_currency"]

RULES = {
    "R-DUP-01": ("Drop repeated declaration_id, keep the first occurrence", "deterministic", "P01"),
    "R-CTRY-01": ("Map country spelling to ISO-3166 alpha-2 with the alias reference table", "alias_lookup", "P03"),
    "R-CTRY-02": ("Fuzzy-match a misspelled country to the nearest alias; human validation if confidence < 0.90", "fuzzy_match", "P03"),
    "R-CTRY-03": ("Missing country: set UNK and quarantine the row", "deterministic", "P02"),
    "R-HS-01": ("Strip punctuation and spaces so the HS code has 6 digits", "deterministic", "P04"),
    "R-HS-02": ("HS code is not 6 digits: set INVALID and quarantine the row", "deterministic", "P05"),
    "R-DATE-01": ("Parse the date (YYYY-MM-DD, DD/MM/YYYY or Mon DD, YYYY) into ISO 8601", "deterministic", "P06"),
    "R-FLOW-01": ("Normalise the flow label to Export or Import", "deterministic", "P07"),
    "R-CUR-01": ("Normalise the currency label to its ISO-4217 code", "deterministic", "P08"),
    "R-VAL-01": ("Negative value: use the absolute value and flag it for human review", "heuristic", "P09"),
    "R-FX-01": ("Convert the value to TND with fx_rates.csv", "deterministic", "P10"),
}
QUARANTINE_RULES = {"R-CTRY-03", "R-HS-02"}

KPI_DEFS = [
    ("K01", "Total Exports", 'CALCULATE(SUM(Fact_Trade[value_tnd]), Fact_Trade[flow] = "Export")',
     "Fact_Trade[value_tnd]|Fact_Trade[flow]", "TND", "Sum of export values in TND.", "BO3"),
    ("K02", "Total Imports", 'CALCULATE(SUM(Fact_Trade[value_tnd]), Fact_Trade[flow] = "Import")',
     "Fact_Trade[value_tnd]|Fact_Trade[flow]", "TND", "Sum of import values in TND.", "BO3"),
    ("K03", "Trade Balance", "[Total Exports] - [Total Imports]",
     "Fact_Trade[value_tnd]|Fact_Trade[flow]", "TND", "Exports minus imports; a positive value is a trade surplus.", "BO3"),
    ("K04", "Coverage Ratio", "DIVIDE([Total Exports], [Total Imports])",
     "Fact_Trade[value_tnd]|Fact_Trade[flow]", "ratio", "Exports divided by imports; above 1 means exports cover imports.", "BO3"),
    ("K05", "Export YoY Growth",
     "VAR cur = [Total Exports] VAR prev = CALCULATE([Total Exports], SAMEPERIODLASTYEAR(Dim_Date[date])) RETURN DIVIDE(cur - prev, prev)",
     "Fact_Trade[value_tnd]|Fact_Trade[flow]|Dim_Date[year]", "percent", "Year-over-year growth of exports.", "BO3"),
]


class Simulator:
    """Écrit dans `out` les CSV des agents amont : profiler, cleaning, ETL, KPI, analyste, dashboard, orchestrateur."""

    def __init__(self, out, seed=SEED, n_base_rows=N_BASE_ROWS):
        self.out, self.seed, self.n_base_rows = str(out), seed, n_base_rows
        os.makedirs(self.out, exist_ok=True)
        self.rng = np.random.default_rng(seed)
        random.seed(seed)

    def save(self, df, name):
        df.to_csv(f"{self.out}/{name}", index=False)

    def rd(self, name, num=()):
        """Read a CSV as text (keeps HS codes like 150910 intact), then cast the numeric columns."""
        df = pd.read_csv(f"{self.out}/{name}", dtype=str)
        for c in num:
            df[c] = pd.to_numeric(df[c])
        return df

    # ---- A.1 raw (dirty) trade file --------------------------------------------------------
    def make_raw(self):
        rng, rows = self.rng, []
        for i in range(self.n_base_rows):
            flow = str(rng.choice(["Export", "Import"], p=[.55, .45]))
            pool = EXP_MKT if flow == "Export" else IMP_MKT
            iso = str(rng.choice(list(pool), p=np.array(list(pool.values())) / sum(pool.values())))
            hs = str(rng.choice(EXPORT_PRODUCTS if flow == "Export" else IMPORT_PRODUCTS))
            d = dt.date(int(rng.choice([2023, 2024])), int(rng.integers(1, 13)), int(rng.integers(1, 29)))
            eur = BASE_EUR[hs] * float(rng.lognormal(0, 0.45))
            if flow == "Export" and iso == "IT" and d.year == 2024: eur *= 0.60     # planted story: Italy drops
            if flow == "Export" and iso == "LY" and d.year == 2024: eur *= 1.40     # planted story: Libya grows
            if flow == "Export" and d.year == 2024 and d.month == 7: eur *= 2.50    # planted anomaly: July 2024
            ccy = CCY_BY_ISO.get(iso) or str(rng.choice(["EUR", "USD", "TND"]))
            value = round(eur * FX["EUR"] / FX[ccy], 2)
            qty = int(rng.integers(10, 5000))

            date_raw = [d.isoformat(), d.strftime("%d/%m/%Y"), d.strftime("%b %d, %Y")][int(rng.choice(3, p=[.5, .3, .2]))]
            r = rng.random()
            if r < .03: country_raw = np.nan
            elif r < .08 and iso in TYPO_BY_ISO: country_raw = TYPO_BY_ISO[iso]
            elif r < .25: country_raw = iso
            else: country_raw = str(rng.choice(VARIANTS[iso]))
            if rng.random() < .015:
                hs_raw = hs[:5]
            else:
                hs_raw = [hs, f"{hs[:4]}.{hs[4:]}", f"{hs[:4]} {hs[4:]}", f"{hs[:4]}-{hs[4:]}"][int(rng.choice(4, p=[.35, .35, .15, .15]))]
            if rng.random() < .01: value = -value
            rows.append(dict(
                declaration_id=f"DEC-{i:05d}", date_raw=date_raw, flow_raw=str(rng.choice(FLOW_VARIANTS[flow])),
                country_raw=country_raw, hs_code_raw=hs_raw, value_raw=value, currency_raw=str(rng.choice(CCY_VARIANTS[ccy])),
                quantity=qty, weight_kg=round(qty * float(rng.uniform(.5, 20)), 1),
                source_file=str(rng.choice(SOURCES, p=[.5, .35, .15]))))

        raw_df = pd.DataFrame(rows)
        dups = raw_df.sample(frac=.05, random_state=self.seed)                      # duplicated declarations
        raw_df = pd.concat([raw_df, dups]).sample(frac=1, random_state=self.seed).reset_index(drop=True)
        raw_df.insert(0, "row_id", range(1, len(raw_df) + 1))
        return raw_df

    # ---- A.2 Agent 1 : Data Profiler ---------------------------------------------------------
    def run_profiler(self):
        raw = pd.read_csv(f"{self.out}/raw_trade_data.csv", dtype=str)
        n = len(raw)
        iso = raw.country_raw.map(lambda s: resolve_country(s)[0])
        digits = raw.hs_code_raw.map(hs_digits)
        fmt = raw.date_raw.map(lambda s: parse_date(s)[1])
        ccy = raw.currency_raw.map(lambda s: CCY_MAP.get(norm_txt(s)))
        val = pd.to_numeric(raw.value_raw)
        checks = [
            ("duplicate_declaration", "declaration_id", raw.duplicated("declaration_id"), "high", "Drop repeated declarations, keep the first occurrence"),
            ("missing_country", "country_raw", raw.country_raw.isna(), "high", "Quarantine rows without a partner country"),
            ("country_spelling", "country_raw", raw.country_raw.notna() & (iso.fillna("") != raw.country_raw.fillna("")), "medium", "Normalise to ISO-3166 alpha-2 codes"),
            ("hs_format", "hs_code_raw", (digits.str.len() == 6) & (digits != raw.hs_code_raw), "low", "Strip punctuation / spaces from HS codes"),
            ("hs_invalid", "hs_code_raw", digits.str.len() != 6, "high", "Quarantine rows with an invalid HS code"),
            ("date_format", "date_raw", fmt != "%Y-%m-%d", "low", "Parse all dates to ISO 8601"),
            ("flow_label", "flow_raw", ~raw.flow_raw.isin(["Export", "Import"]), "low", "Normalise flow labels"),
            ("currency_label", "currency_raw", ~raw.currency_raw.isin(["EUR", "USD", "TND"]), "low", "Normalise currency labels to ISO-4217"),
            ("negative_value", "value_raw", val < 0, "high", "Review negative values with a human"),
            ("mixed_currency", "currency_raw", ccy != "TND", "medium", "Convert all values to TND"),
        ]
        out = []
        for typ, col, mask, sev, action in checks:
            k = int(mask.sum())
            if k == 0:
                continue
            out.append(dict(issue_id=f"P{ISSUE_ORDER.index(typ) + 1:02d}", column=col, issue_type=typ, affected_rows=k,
                            pct_rows=round(100 * k / n, 2), severity=sev, example=str(raw.loc[mask, col].iloc[0]),
                            recommended_action=action, detected_by="Data Profiler", detected_at=ts(20)))
        return pd.DataFrame(out)

    # ---- A.3 Agent 2 : Quality & Cleaning ------------------------------------------------------
    def run_cleaning(self):
        rng = self.rng
        raw = pd.read_csv(f"{self.out}/raw_trade_data.csv", dtype=str)
        fx = self.rd("fx_rates.csv", num=["rate_to_tnd"]).set_index("currency")["rate_to_tnd"].to_dict()
        events, clean_rows, seen = [], [], set()
        by_row = defaultdict(list)
        counter = [0]

        def log(r, column, before, after, rule, conf, reason):
            counter[0] += 1
            desc, method, issue = RULES[rule]
            needs = rule in QUARANTINE_RULES or rule == "R-VAL-01" or (rule == "R-CTRY-02" and conf < 0.90)
            if not needs:
                status, who = "auto_approved", "system"
            elif rule in QUARANTINE_RULES or rng.random() >= .5:
                status, who = "pending", ""
            else:
                status, who = "approved", "human:karim.trabelsi"
            e = dict(event_id=f"C{counter[0]:04d}", row_id=r.row_id, declaration_id=r.declaration_id, column=column,
                     before=before, after=after, rule_id=rule, rule_description=desc, method=method, confidence=conf,
                     reason=reason, profiler_issue_id=issue, needs_human_validation=needs, approval_status=status,
                     approver=who, timestamp=ts(40 + counter[0] * 0.01))
            events.append(e)
            by_row[r.row_id].append(e)

        for r in raw.itertuples(index=False):
            if r.declaration_id in seen:
                log(r, "row", r.declaration_id, "(row dropped)", "R-DUP-01", 1.0,
                    "declaration_id already present in an earlier row of the same file")
                continue
            seen.add(r.declaration_id)

            d, _ = parse_date(r.date_raw)
            if r.date_raw != d.isoformat():
                log(r, "date", r.date_raw, d.isoformat(), "R-DATE-01", 1.0, "date written in a non-ISO format")

            flow = FLOW_MAP[norm_txt(r.flow_raw)]
            if r.flow_raw != flow:
                log(r, "flow", r.flow_raw, flow, "R-FLOW-01", 1.0, "flow label written in a non-standard way")

            iso, how, conf = resolve_country(r.country_raw)
            if how == "missing" or iso is None:
                iso = "UNK"
                log(r, "country", "(missing)", "UNK", "R-CTRY-03", 0.0, "no partner country in the declaration")
            elif r.country_raw != iso:
                rule = "R-CTRY-01" if how == "alias_lookup" else "R-CTRY-02"
                why = ("country name found in the alias reference table" if how == "alias_lookup"
                       else f"misspelled country name matched to the nearest alias with similarity {conf}")
                log(r, "country", r.country_raw, iso, rule, conf, why)

            digits = hs_digits(r.hs_code_raw)
            if len(digits) != 6:
                log(r, "hs_code", r.hs_code_raw, "INVALID", "R-HS-02", 0.0, f"HS code has {len(digits)} digits instead of 6")
                hs6 = "INVALID"
            else:
                hs6 = digits
                if r.hs_code_raw != digits:
                    log(r, "hs_code", r.hs_code_raw, digits, "R-HS-01", 1.0, "HS code contained punctuation or spaces")

            v = float(r.value_raw)
            if v < 0:
                log(r, "value", f"{v:.2f}", f"{abs(v):.2f}", "R-VAL-01", 0.60,
                    "negative trade value is not plausible; absolute value used pending human review")
                v = abs(v)
            c = CCY_MAP[norm_txt(r.currency_raw)]
            if r.currency_raw != c:
                log(r, "currency", r.currency_raw, c, "R-CUR-01", 1.0, "currency label was not an ISO-4217 code")
            value_tnd = round(v * fx[c], 2)
            log(r, "value_tnd", f"{v:.2f} {c}", f"{value_tnd:.2f} TND", "R-FX-01", 1.0,
                f"converted at {fx[c]} TND per {c} (fx_rates.csv)")

            clean_rows.append(dict(row_id=r.row_id, declaration_id=r.declaration_id, date=d.isoformat(), flow=flow,
                                   country_iso=iso, hs6=hs6, value_tnd=value_tnd, quantity=r.quantity,
                                   weight_kg=r.weight_kg, source_file=r.source_file))
        clean = pd.DataFrame(clean_rows)

        def flag(row):
            if row.country_iso == "UNK" or row.hs6 == "INVALID":
                return "quarantined"
            if any(e["approval_status"] == "pending" for e in by_row[row.row_id]):
                return "pending_review"
            return "ok"
        clean["quality_flag"] = clean.apply(flag, axis=1)
        return pd.DataFrame(events), clean

    # ---- A.4 Agent 3 : ETL / Data Engineering ----------------------------------------------------
    def run_etl(self):
        c = self.rd("clean_trade_data.csv", num=["value_tnd", "quantity", "weight_kg"])
        steps = []

        def step(no, op, inp, outp, rin, rout, cols, desc):
            steps.append(dict(step_id=f"E{no:02d}", step_no=no, operation=op, input_tables=inp, output_table=outp,
                              rows_in=rin, rows_out=rout, columns_used=cols, description=desc, timestamp=ts(60 + no)))

        ok = c[c.quality_flag != "quarantined"].copy()
        step(1, "filter", "clean_trade_data", "clean_trade_data (valid)", len(c), len(ok), "quality_flag",
             "Exclude quarantined rows (missing country or invalid HS code) from the analytical model.")

        dc = ok[["country_iso"]].drop_duplicates().sort_values("country_iso").reset_index(drop=True)
        dc.insert(0, "country_key", range(1, len(dc) + 1))
        dc["country_name"] = dc.country_iso.map(lambda x: COUNTRIES[x][0])
        dc["region"] = dc.country_iso.map(lambda x: COUNTRIES[x][1])
        step(2, "build_dimension", "clean_trade_data (valid)", "Dim_Country", len(ok), len(dc), "country_iso",
             "Build Dim_Country with one surrogate key per ISO country code.")

        dp = ok[["hs6"]].drop_duplicates().sort_values("hs6").reset_index(drop=True)
        dp.insert(0, "product_key", range(1, len(dp) + 1))
        dp["description"] = dp.hs6.map(PRODUCTS)
        dp["hs_chapter"] = dp.hs6.str[:2]
        step(3, "build_dimension", "clean_trade_data (valid)", "Dim_Product", len(ok), len(dp), "hs6",
             "Build Dim_Product with one surrogate key per 6-digit HS code and its description.")

        dd = ok[["date"]].drop_duplicates().sort_values("date").reset_index(drop=True)
        dts = pd.to_datetime(dd["date"])
        dd.insert(0, "date_key", dts.dt.strftime("%Y%m%d").astype(int))
        dd["year"], dd["month"], dd["quarter"] = dts.dt.year, dts.dt.month, dts.dt.quarter
        step(4, "build_dimension", "clean_trade_data (valid)", "Dim_Date", len(ok), len(dd), "date",
             "Build Dim_Date (date_key = YYYYMMDD) with year, month and quarter.")

        f = ok.copy()
        f["date_key"] = pd.to_datetime(f["date"]).dt.strftime("%Y%m%d").astype(int)
        f = f.merge(dc[["country_key", "country_iso"]], on="country_iso").merge(dp[["product_key", "hs6"]], on="hs6")
        f.insert(0, "trade_id", range(1, len(f) + 1))
        f = f[["trade_id", "row_id", "date_key", "country_key", "product_key", "flow", "value_tnd", "quantity", "weight_kg"]]
        step(5, "build_fact", "clean_trade_data (valid) + dimensions", "Fact_Trade", len(ok), len(f),
             "value_tnd|flow|quantity|weight_kg|date_key|country_key|product_key",
             "Build Fact_Trade by joining the cleaned rows to the three dimensions on surrogate keys.")

        assert len(f) == len(ok), "referential integrity broken"
        step(6, "validate", "Fact_Trade", "Fact_Trade", len(f), len(f), "all keys",
             "Referential integrity check: every fact row found its country, product and date key (0 orphan rows).")
        etl = pd.DataFrame(steps)

        base_rules = ["R-DUP-01", "R-CTRY-03", "R-HS-02"]       # these rules decide which rows exist at all

        def cl(col, src, raw_cols, how, e, rules):
            return dict(fact_column=col, source_table="clean_trade_data", source_column=src, raw_columns=raw_cols,
                        transformation=how, etl_step="|".join(["E01"] + e + ["E06"]),
                        cleaning_rules="|".join(base_rules + [r for r in rules if r not in base_rules]))
        col_lineage = pd.DataFrame([
            cl("Fact_Trade[value_tnd]", "value_tnd", "value_raw|currency_raw", "value converted to TND", ["E05"], ["R-CUR-01", "R-VAL-01", "R-FX-01"]),
            cl("Fact_Trade[flow]", "flow", "flow_raw", "copied", ["E05"], ["R-FLOW-01"]),
            cl("Fact_Trade[quantity]", "quantity", "quantity", "copied", ["E05"], []),
            cl("Fact_Trade[weight_kg]", "weight_kg", "weight_kg", "copied", ["E05"], []),
            cl("Fact_Trade[date_key]", "date", "date_raw", "YYYYMMDD surrogate key", ["E04", "E05"], ["R-DATE-01"]),
            cl("Fact_Trade[country_key]", "country_iso", "country_raw", "surrogate key lookup", ["E02", "E05"], ["R-CTRY-01", "R-CTRY-02"]),
            cl("Fact_Trade[product_key]", "hs6", "hs_code_raw", "surrogate key lookup", ["E03", "E05"], ["R-HS-01"]),
            cl("Dim_Date[year]", "date", "date_raw", "year extracted", ["E04"], ["R-DATE-01"]),
            cl("Dim_Date[month]", "date", "date_raw", "month extracted", ["E04"], ["R-DATE-01"]),
            cl("Dim_Country[country_iso]", "country_iso", "country_raw", "distinct ISO code", ["E02"], ["R-CTRY-01", "R-CTRY-02"]),
            cl("Dim_Product[hs6]", "hs6", "hs_code_raw", "distinct 6-digit HS code", ["E03"], ["R-HS-01"]),
        ])
        return dc, dp, dd, f, etl, col_lineage

    # ---- A.5 Agent 4 : Semantic & KPI -------------------------------------------------------------
    def load_model(self):
        f = self.rd("fact_trade.csv", num=["trade_id", "date_key", "country_key", "product_key", "value_tnd", "quantity", "weight_kg"])
        dd = self.rd("dim_date.csv", num=["date_key", "year", "month", "quarter"])
        dc = self.rd("dim_country.csv", num=["country_key"])
        dp = self.rd("dim_product.csv", num=["product_key"])
        return f.merge(dd, on="date_key").merge(dc, on="country_key").merge(dp, on="product_key")

    def run_kpi(self):
        F = self.load_model()
        cat = pd.DataFrame(KPI_DEFS, columns=["kpi_id", "name", "dax", "fact_columns", "unit", "description", "business_objective"])
        vals = []

        def add(kid, st, sv, period, v, n):
            vals.append(dict(kpi_id=kid, scope_type=st, scope_value=sv, period=str(period), value=float(v),
                             rows_used=int(n), computed_at=ts(80)))
        for y in (2023, 2024):
            s = F[F.year == y]
            e, i = s[s.flow == "Export"].value_tnd.sum(), s[s.flow == "Import"].value_tnd.sum()
            add("K01", "all", "All", y, e, (s.flow == "Export").sum())
            add("K02", "all", "All", y, i, (s.flow == "Import").sum())
            add("K03", "all", "All", y, e - i, len(s))
            add("K04", "all", "All", y, e / i, len(s))
            for iso, g in s.groupby("country_iso"):
                ge, gi = g[g.flow == "Export"], g[g.flow == "Import"]
                if len(ge): add("K01", "country", iso, y, ge.value_tnd.sum(), len(ge))
                if len(gi): add("K02", "country", iso, y, gi.value_tnd.sum(), len(gi))
        e23 = F[(F.year == 2023) & (F.flow == "Export")].value_tnd.sum()
        e24 = F[(F.year == 2024) & (F.flow == "Export")].value_tnd.sum()
        add("K05", "all", "All", 2024, (e24 - e23) / e23, ((F.flow == "Export")).sum())
        return cat, pd.DataFrame(vals)

    # ---- A.6 Agents 5-7 : BI Analyst, Dashboard Generator, Orchestrator ------------------------------
    def run_analyst(self):
        F = self.load_model()
        exp, imp = F[F.flow == "Export"], F[F.flow == "Import"]
        out = []

        def add(iid, kid, text, method, flow, years, expr, group_by, rows, mname, mval, thr, rule, trig, rec_yes, rec_no):
            out.append(dict(insight_id=iid, kpi_id=kid, text=text, method=method, flow=flow, years=years, filter_expr=expr,
                            group_by=group_by, rows_used=int(rows), metric_name=mname, metric_value=round(float(mval), 2),
                            threshold=thr, rule_text=rule, rule_triggered=bool(trig),
                            recommendation=rec_yes if trig else rec_no, created_at=ts(90)))

        # I01 - largest export decline by partner country
        a = exp.pivot_table(index="country_iso", columns="year", values="value_tnd", aggfunc="sum").fillna(0)
        a = a[a[2023] >= a[2023].median()]
        g = (a[2024] - a[2023]) / a[2023] * 100
        iso = g.idxmin()
        n = len(exp[(exp.country_iso == iso)])
        add("I01", "K01",
            f"Exports to {COUNTRIES[iso][0]} fell {abs(g[iso]):.1f}% in 2024 versus 2023 ({a.loc[iso, 2023]:,.0f} to {a.loc[iso, 2024]:,.0f} TND).",
            "yoy_by_country", "Export", "2023|2024", f"country_iso == '{iso}'", "country_iso", n,
            "export_yoy_pct", g[iso], -10.0, "Recommend action when a major partner's export YoY change is below -10%.", g[iso] < -10,
            f"Investigate the commercial relationship with {COUNTRIES[iso][0]} and diversify towards growing markets.",
            "No action required; keep monitoring partner trends.")

        # I02 - export product concentration
        p = exp[exp.year == 2024].groupby("hs6").value_tnd.sum()
        hs = p.idxmax(); share = p[hs] / p.sum() * 100
        add("I02", "K01",
            f"{PRODUCTS[hs]} (HS {hs}) is the top export product of 2024 with {share:.1f}% of export value.",
            "top_share_by_product", "Export", "2024", f"hs6 == '{hs}'", "hs6", len(exp[(exp.year == 2024) & (exp.hs6 == hs)]),
            "top_product_share_pct", share, 30.0, "Flag concentration risk when one product exceeds 30% of export value.", share >= 30,
            f"Reduce dependence on {PRODUCTS[hs]} by supporting other export lines.",
            "No concentration risk detected on products; keep monitoring.")

        # I03 - monthly anomaly (z-score)
        m = exp.groupby(["year", "month"]).value_tnd.sum()
        z = (m - m.mean()) / m.std()
        (yy, mm) = z.abs().idxmax()
        add("I03", "K01",
            f"Exports in {yy}-{mm:02d} reached {m[(yy, mm)]:,.0f} TND, a z-score of {z[(yy, mm)]:.2f} against the monthly average.",
            "monthly_zscore", "Export", f"{yy}", f"year == {yy} and month == {mm}", "year,month",
            len(exp[(exp.year == yy) & (exp.month == mm)]), "zscore", z[(yy, mm)], 2.0,
            "Flag a month as an anomaly when its absolute z-score is at least 2.", abs(z[(yy, mm)]) >= 2,
            f"Check the large declarations of {yy}-{mm:02d} for one-off shipments or data errors before reporting.",
            "No monthly anomaly detected.")

        # I04 - import supplier concentration
        s = imp[imp.year == 2024].groupby("country_iso").value_tnd.sum()
        top = s.idxmax(); sh = s[top] / s.sum() * 100
        add("I04", "K02",
            f"{COUNTRIES[top][0]} is the top import supplier of 2024 with {sh:.1f}% of import value.",
            "top_share_by_supplier", "Import", "2024", f"country_iso == '{top}'", "country_iso",
            len(imp[(imp.year == 2024) & (imp.country_iso == top)]), "top_supplier_share_pct", sh, 25.0,
            "Flag supplier concentration when one country exceeds 25% of import value.", sh >= 25,
            f"Diversify import sourcing away from {COUNTRIES[top][0]}.",
            "No supplier concentration risk detected.")
        return pd.DataFrame(out)

    # ---- tout écrire, dans l'ordre du notebook (le hasard est consommé dans le même ordre) ------------------------
    def simulate(self):
        raw_df = self.make_raw()
        self.save(raw_df, "raw_trade_data.csv")
        self.save(pd.DataFrame([dict(currency=c, rate_to_tnd=r, rate_date="2024-12-31", source="simulated reference table")
                                for c, r in FX.items()]), "fx_rates.csv")
        self.save(self.run_profiler(), "profiler_report.csv")

        cleaning_log, clean_df = self.run_cleaning()
        self.save(cleaning_log, "cleaning_log.csv")
        self.save(pd.DataFrame([dict(rule_id=k, description=v[0], method=v[1], profiler_issue_id=v[2]) for k, v in RULES.items()]),
                  "cleaning_rules.csv")
        self.save(clean_df, "clean_trade_data.csv")
        self.save(clean_df[clean_df.quality_flag == "quarantined"], "quarantine.csv")

        dim_country, dim_product, dim_date, fact_trade, etl_log, column_lineage = self.run_etl()
        self.save(dim_country, "dim_country.csv"); self.save(dim_product, "dim_product.csv"); self.save(dim_date, "dim_date.csv")
        self.save(fact_trade, "fact_trade.csv"); self.save(etl_log, "etl_log.csv"); self.save(column_lineage, "column_lineage.csv")

        kpi_catalog, kpi_values = self.run_kpi()
        self.save(kpi_catalog, "kpi_catalog.csv"); self.save(kpi_values, "kpi_values.csv")

        self.save(self.run_analyst(), "analyst_insights.csv")

        dashboard_visuals = pd.DataFrame([
            ("V01", "Overview", "card", "Total Exports", "K01", "none", "", "A single headline number is best read as a card."),
            ("V02", "Overview", "card", "Total Imports", "K02", "none", "", "A single headline number is best read as a card."),
            ("V03", "Overview", "card", "Trade Balance", "K03", "none", "", "A single headline number is best read as a card."),
            ("V04", "Overview", "gauge", "Coverage Ratio", "K04", "none", "", "A ratio with a natural target of 1 suits a gauge."),
            ("V05", "Overview", "card", "Export YoY Growth", "K05", "none", "", "A growth rate is read as one signed number."),
            ("V06", "Markets", "bar", "Exports by partner country", "K01", "country", "I01", "Ranking categories by value suits a bar chart."),
            ("V07", "Products", "donut", "Exports by product", "K01", "product", "I02", "Parts of a whole suit a donut chart."),
            ("V08", "Trends", "line", "Monthly exports", "K01", "month", "I03", "A time series suits a line chart."),
            ("V09", "Markets", "bar", "Imports by supplier country", "K02", "country", "I04", "Ranking categories by value suits a bar chart."),
        ], columns=["visual_id", "page", "visual_type", "title", "kpi_ids", "dimension", "insight_id", "chosen_because"])
        dashboard_visuals["generated_at"] = ts(100)
        self.save(dashboard_visuals, "dashboard_visuals.csv")

        n_h = int(cleaning_log.needs_human_validation.sum())
        n_a = int(((cleaning_log.needs_human_validation) & (cleaning_log.approval_status == "approved")).sum())
        steps = [
            ("Data Profiler", "success", 0, "", "no", "", "profiler_report.csv", 20),
            ("Quality & Cleaning", "success", 0, "", "yes", f"{n_a} of {n_h} approved, {n_h - n_a} pending",
             "cleaning_log.csv; clean_trade_data.csv; quarantine.csv", 40),
            ("ETL / Data Engineering", "success", 1, "TimeoutError while building Dim_Date; retry succeeded", "no", "",
             "dim_*.csv; fact_trade.csv; etl_log.csv; column_lineage.csv", 60),
            ("Semantic & KPI", "success", 0, "", "no", "", "kpi_catalog.csv; kpi_values.csv", 80),
            ("BI Analyst", "success", 0, "", "no", "", "analyst_insights.csv", 90),
            ("Dashboard Generator", "success", 0, "", "no", "", "dashboard_visuals.csv", 100),
        ]
        self.save(pd.DataFrame([dict(
            run_id="RUN-2026-10-28-001", step_no=i + 1, agent=a, status=s, started_at=ts(t), ended_at=ts(t + 12 + 5 * r),
            retries=r, error=err, human_validation_required=hv, human_validation_status=hvs, outputs=o)
            for i, (a, s, r, err, hv, hvs, o, t) in enumerate(steps)]), "orchestrator_runs.csv")
        return self.out
