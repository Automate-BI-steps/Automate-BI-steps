from __future__ import annotations

import re
from typing import Any

from src.agents.dashboard.dashboard_generator import Bundle, visual
from src.agents.dashboard.dashboard_planner import DashboardPlan, PagePlan, VisualPlan


def convert_visual_plan_to_container(v_plan: VisualPlan) -> dict[str, Any]:
    """Convert a high-level VisualPlan into a PBIR visualContainer descriptor."""
    # Convert role projections from dict format to (kind, table, name) tuples
    roles_spec: dict[str, list[tuple[str, str, str]]] = {}
    for role_name, projections in v_plan.roles.items():
        specs: list[tuple[str, str, str]] = []
        for p in projections:
            kind = p.get("kind", "Column")
            table = p.get("table", "FactTrade")
            name = p.get("name", "")
            if name:
                specs.append((kind, table, name))
        if specs:
            roles_spec[role_name] = specs

    return visual(v_plan.id, v_plan.visual_type, roles_spec, v_plan.title)


def compile_plan_to_report_pages(plan: DashboardPlan, bundle: Bundle) -> list[dict[str, Any]]:
    """Compile a validated DashboardPlan into the report pages structure expected by the PBIP generator."""
    report_pages: list[dict[str, Any]] = []

    for page in plan.pages:
        slug = re.sub(r"[^A-Za-z0-9]", "", page.name)
        visual_containers = [convert_visual_plan_to_container(v) for v in page.visuals]
        report_pages.append({
            "name": slug,
            "displayName": page.display_name,
            "visualContainers": visual_containers,
        })

    return report_pages

