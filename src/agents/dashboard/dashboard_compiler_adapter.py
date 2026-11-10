from __future__ import annotations

import re
from typing import Any

from src.agents.dashboard.dashboard_generator import Bundle, visual
from src.agents.dashboard.dashboard_planner import DashboardPlan, PagePlan, VisualPlan


def _fact_alias(bundle: Bundle | None) -> dict[str, str]:
    """Generic datasets name their fact table after the data model (e.g. Fact_Trade); a plan written
    against the trade name FactTrade must point at that table, or Power BI finds no such entity."""
    if bundle is None or getattr(bundle, "trade_mode", True):
        return {}
    fact = (bundle.data_model or {}).get("fact_table") or "FactTrade"
    return {} if fact == "FactTrade" else {"FactTrade": fact}


def _star_locations(bundle: Bundle | None) -> tuple[str, dict[str, Any]]:
    """(table de faits, place de chaque colonne à plat dans le schéma en étoile) ; vide sans étoile."""
    star = getattr(bundle, "star", None) if bundle is not None and not getattr(bundle, "trade_mode", True) else None
    if not star:
        return "", {}
    return star["fact"]["name"], star.get("locations") or {}


def convert_visual_plan_to_container(v_plan: VisualPlan, aliases: dict[str, str] | None = None,
                                     star: tuple[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Convert a high-level VisualPlan into a PBIR visualContainer descriptor.

    The plan names the columns of the flat fact table; with a star schema each column is sent to the table
    that holds it (e.g. Fact_Trade[Country] -> DimCustomer[Country]). Measures stay on the fact table."""
    aliases = aliases or {}
    fact_table, locations = star or ("", {})

    def place(kind: str, table: str, name: str) -> tuple[str, str]:
        table = aliases.get(table, table)
        if kind != "Measure" and locations:
            from src.agents.dashboard.star_schema import physical_field

            return physical_field(table, name, fact_table, locations)
        return table, name

    # Convert role projections from dict format to (kind, table, name[, label]) tuples
    roles_spec: dict[str, list[tuple[str, ...]]] = {}
    for role_name, projections in v_plan.roles.items():
        specs: list[tuple[str, ...]] = []
        for p in projections:
            kind = p.get("kind", "Column")
            logical = p.get("name", "")
            table, name = place(kind, p.get("table", "FactTrade"), logical)
            # A column renamed by the star schema keeps its flat-table name on screen
            # (DimShipper[CompanyName] is shown as "ShipperName").
            label = p.get("label") or (logical if name != logical else None)
            if name:
                specs.append((kind, table, name, label) if label else (kind, table, name))
        if specs:
            roles_spec[role_name] = specs

    sort = [
        (s.get("kind", "Column"), *place(s.get("kind", "Column"), s.get("table", "FactTrade"), s["name"]), s.get("direction", "Descending"))
        for s in (getattr(v_plan, "sort", None) or []) if s.get("name")
    ]
    return visual(v_plan.id, v_plan.visual_type, roles_spec, v_plan.title, sort=sort or None,
                  display=getattr(v_plan, "display", None) or None)


def compile_plan_to_report_pages(plan: DashboardPlan, bundle: Bundle) -> list[dict[str, Any]]:
    """Compile a validated DashboardPlan into the report pages structure expected by the PBIP generator."""
    report_pages: list[dict[str, Any]] = []
    aliases = _fact_alias(bundle)
    star = _star_locations(bundle)

    for page in plan.pages:
        slug = re.sub(r"[^A-Za-z0-9]", "", page.name)
        visual_containers = [convert_visual_plan_to_container(v, aliases, star) for v in page.visuals]
        report_pages.append({
            "name": slug,
            "displayName": page.display_name,
            "description": getattr(page, "description", "") or "",
            "visualContainers": visual_containers,
            # rapport par rôle (role_report) : l'ossature de page (filtres, bandeau, navigation) est ajoutée par generate()
            "plan_metadata": dict(getattr(plan, "metadata", None) or {}),
        })

    return report_pages
