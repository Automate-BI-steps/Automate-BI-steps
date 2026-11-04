from __future__ import annotations

import html
import re
from pathlib import Path
import pytest

from src.agents.dashboard.dashboard_planner import create_deterministic_plan
from src.agents.dashboard.html_renderer import render_html_dashboard
from tests.conftest import bundle_for, frame


def test_html_dashboard_generation(sample_bundle, tmp_path: Path):
    plan = create_deterministic_plan(sample_bundle)
    kpi_values = {"total_imports": 1800.0, "total_exports": 3700.0, "trade_balance": 1900.0, "transaction_count": 4.0}
    html_file = tmp_path / "dashboard.html"

    rendered_path = render_html_dashboard(
        plan=plan,
        bundle=sample_bundle,
        kpi_values=kpi_values,
        output_path=html_file,
        run_id="test_run_123",
    )

    assert rendered_path.exists()
    content = rendered_path.read_text(encoding="utf-8")

    # 1. Zero external network references
    external_urls = [u for u in re.findall(r"https?://[^\s\"\'<>]+", content) if "json-schemas" not in u]
    assert len(external_urls) == 0, f"Found external URLs in HTML: {external_urls}"

    # 2. Contains all 6 page navigation tabs
    for page in plan.pages:
        assert f"switchTab('{page.name}')" in content
        assert html.escape(page.display_name) in content

    # 3. Contains KPI card values
    assert "1,800" in content
    assert "3,700" in content
    assert "1,900" in content

    # 4. Contains upstream findings and recommendations
    assert "Exports exceed imports." in content
    assert "Expand exports." in content

    # 5. Contains data quality audit checks
    assert "Upstream Quality Status" in content
    assert "passed" in content

    # 6. Embedded CSS and JS included locally
    assert "<style>" in content
    assert "<script>" in content
    assert "function switchTab" in content

