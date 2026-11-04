from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field

from src.agents.dashboard.dashboard_planner import DashboardPlan, PagePlan, VisualPlan
from src.agents.dashboard.visualization_agent import SUPPORTED_VISUAL_TYPES, MAX_PAGES, MAX_VISUALS_PER_PAGE

# Canonical semantic model schema â€” single source of truth shared with dashboard_optimizer.py
VALID_TABLES: dict[str, set[str]] = {
    "FactTrade": {"Date", "Country", "Product", "Direction", "Value"},
    "DimDate": {"Date"},
    "DimCountry": {"Country"},
    "DimProduct": {"Product"},
    "BusinessInsights": {"Kind", "Text"},
    "DataQuality": {"Check", "Result"},
}


class CriticReport(BaseModel):
    status: str = Field(description="Audit status: 'ACCEPTED', 'REJECTED', or 'REVISION_REQUESTED'")
    passed_deterministic: bool = Field(default=True)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    invalid_references: list[str] = Field(default_factory=list)
    redundant_visuals: list[str] = Field(default_factory=list)
    quality_feedback: list[str] = Field(default_factory=list)
    revision_suggestions: list[str] = Field(default_factory=list)


class DashboardCritic:
    """Rigorous evidence validator and deterministic critic for proposed dashboard plans."""

    def __init__(self, max_pages: int = MAX_PAGES, max_visuals_per_page: int = MAX_VISUALS_PER_PAGE) -> None:
        self.max_pages = max_pages
        self.max_visuals_per_page = max_visuals_per_page

    def evaluate(self, plan: DashboardPlan, bundle: Any, valid_tables: dict[str, set[str]] | None = None) -> CriticReport:
        errors: list[str] = []
        warnings: list[str] = []
        invalid_references: list[str] = []
        redundant_visuals: list[str] = []
        unsupported_claims: list[str] = []
        quality_feedback: list[str] = []
        revision_suggestions: list[str] = []

        # 1. Page and Visual limits
        if len(plan.pages) > self.max_pages:
            errors.append(f"Plan exceeds maximum allowed pages: {len(plan.pages)} > {self.max_pages}")
        elif len(plan.pages) == 0:
            errors.append("Plan must contain at least one page.")

        # 2. Known model tables and fields
        # Priority: caller-supplied valid_tables > bundle.data_model["tables"] > hardcoded VALID_TABLES
        if valid_tables is not None:
            active_tables = dict(valid_tables)
        else:
            active_tables = {t: set(cols) for t, cols in VALID_TABLES.items()}

        # Merge any table schema provided by Agent 3 in bundle.data_model
        dm_tables: dict = {}
        if hasattr(bundle, "data_model") and isinstance(bundle.data_model, dict):
            dm_tables = bundle.data_model.get("tables") or {}
        for tbl_name, tbl_cols in dm_tables.items():
            if isinstance(tbl_cols, (list, set, tuple)):
                if tbl_name not in active_tables:
                    active_tables[tbl_name] = set(tbl_cols)
                else:
                    active_tables[tbl_name] = active_tables[tbl_name] | set(tbl_cols)
            elif isinstance(tbl_cols, dict):
                cols_set = set(tbl_cols.keys())
                if tbl_name not in active_tables:
                    active_tables[tbl_name] = cols_set
                else:
                    active_tables[tbl_name] = active_tables[tbl_name] | cols_set

        # Register fact table columns from bundle.data and bundle.data_model
        fact_name = bundle.data_model.get("fact_table") if hasattr(bundle, "data_model") and isinstance(bundle.data_model, dict) else None
        fact_names = [fact_name, "FactTrade"] if fact_name else ["FactTrade"]

        fact_cols: set[str] = set()
        if hasattr(bundle, "data") and hasattr(bundle.data, "columns"):
            fact_cols.update(str(c) for c in bundle.data.columns)
            fact_cols.update(str(c)[0].upper() + str(c)[1:] for c in bundle.data.columns)
        if hasattr(bundle, "data_model") and isinstance(bundle.data_model, dict):
            dm_cols = bundle.data_model.get("columns") or {}
            if isinstance(dm_cols, dict):
                fact_cols.update(str(c) for c in dm_cols.keys())
                fact_cols.update(str(c)[0].upper() + str(c)[1:] for c in dm_cols.keys())

        for fn in fact_names:
            if fn:
                if fn not in active_tables:
                    active_tables[fn] = set(fact_cols)
                else:
                    active_tables[fn] = active_tables[fn] | fact_cols

        # Register any dimension tables from bundle.dimension_data
        dim_data = getattr(bundle, "dimension_data", {})
        if isinstance(dim_data, dict):
            for d_name, d_df in dim_data.items():
                if hasattr(d_df, "columns"):
                    d_cols = {str(c) for c in d_df.columns} | {str(c)[0].upper() + str(c)[1:] for c in d_df.columns}
                    active_tables[d_name] = active_tables.get(d_name, set()) | d_cols
        known_kpi_ids = {kpi.id for kpi in bundle.kpis}
        known_kpi_names = {kpi.name for kpi in bundle.kpis}
        known_finding_ids = {
            f.get("id") for f in bundle.findings.get("findings", []) if isinstance(f, dict) and "id" in f
        }

        # 3. Duplicate visual ID detection across all pages
        seen_visual_ids: set[str] = set()
        seen_visual_signatures: set[str] = set()

        for page in plan.pages:
            if len(page.visuals) > self.max_visuals_per_page:
                errors.append(f"Page '{page.display_name}' exceeds maximum visuals per page: {len(page.visuals)} > {self.max_visuals_per_page}")

            for visual in page.visuals:
                # Check visual ID uniqueness
                if visual.id in seen_visual_ids:
                    errors.append(f"Duplicate visual identifier found across plan: '{visual.id}'")
                    redundant_visuals.append(visual.id)
                seen_visual_ids.add(visual.id)

                # Check visual type support
                if visual.visual_type not in SUPPORTED_VISUAL_TYPES:
                    errors.append(f"Visual '{visual.id}' uses unsupported visualType: '{visual.visual_type}'")

                # Check KPI reference validity
                if visual.kpi_reference and visual.kpi_reference not in known_kpi_ids:
                    msg = f"Visual '{visual.id}' references unknown KPI ID '{visual.kpi_reference}'"
                    errors.append(msg)
                    invalid_references.append(f"kpi:{visual.kpi_reference}")

                # Check insight reference validity
                if visual.insight_reference and visual.insight_reference not in known_finding_ids:
                    msg = f"Visual '{visual.id}' references unknown finding ID '{visual.insight_reference}'"
                    warnings.append(msg)
                    invalid_references.append(f"insight:{visual.insight_reference}")

                # Validate roles and field bindings
                for role_name, projections in visual.roles.items():
                    for proj in projections:
                        table = proj.get("table")
                        name = proj.get("name")
                        kind = proj.get("kind")
                        if not table or not name:
                            errors.append(f"Visual '{visual.id}' has malformed projection in role '{role_name}': {proj}")
                            continue

                        if kind == "Measure":
                            if name not in known_kpi_names:
                                errors.append(f"Visual '{visual.id}' references unknown Measure '{name}'")
                                invalid_references.append(f"Measure:{name}")
                        elif kind in ("Column", "Sum"):
                            if table not in active_tables or name not in active_tables[table]:
                                errors.append(f"Visual '{visual.id}' references non-existent column '{table}.{name}'")
                                invalid_references.append(f"Column:{table}.{name}")

                # Detect duplicate visual content signature (type + roles)
                sig = f"{visual.visual_type}_{sorted(visual.roles.items())}"
                if sig in seen_visual_signatures and visual.visual_type != "card":
                    warnings.append(f"Visual '{visual.id}' has duplicate visual signature to an existing visual.")
                    redundant_visuals.append(visual.id)
                seen_visual_signatures.add(sig)

        # 4. Check global references in plan
        for kpi_ref in plan.kpi_references:
            if kpi_ref not in known_kpi_ids:
                errors.append(f"Plan references unknown KPI '{kpi_ref}' in top-level kpi_references")
                invalid_references.append(kpi_ref)

        for ins_ref in plan.insight_references:
            if known_finding_ids and ins_ref not in known_finding_ids:
                warnings.append(f"Plan references unknown insight '{ins_ref}' in top-level insight_references")

        # 5. Qualitative assessment
        if not any(page.name == "ExecutiveOverview" for page in plan.pages):
            warnings.append("Best practice recommendation: Include an 'ExecutiveOverview' landing page.")
            revision_suggestions.append("Add an ExecutiveOverview page with headline KPI cards.")

        if not any(page.name == "DataQualityTraceability" for page in plan.pages):
            warnings.append("Governance requirement: Include a Data Quality & Traceability page.")
            revision_suggestions.append("Add DataQualityTraceability page to preserve upstream quality evidence.")

        passed = len(errors) == 0
        if not passed:
            status = "REJECTED"
        elif len(warnings) > 0:
            status = "ACCEPTED_WITH_WARNINGS"
        else:
            status = "ACCEPTED"

        quality_feedback.append(f"Evaluated {len(plan.pages)} pages and {len(seen_visual_ids)} visual containers.")
        if passed:
            quality_feedback.append("All field, measure, table, and KPI references successfully verified against data contract.")

        return CriticReport(
            status=status,
            passed_deterministic=passed,
            errors=errors,
            warnings=warnings,
            unsupported_claims=unsupported_claims,
            invalid_references=invalid_references,
            redundant_visuals=redundant_visuals,
            quality_feedback=quality_feedback,
            revision_suggestions=revision_suggestions,
        )

