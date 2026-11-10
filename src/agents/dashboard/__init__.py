"""Dashboard Generator â€“ bi-flow Agent 5."""
from __future__ import annotations

try:
    from .dashboard_generator import run_dashboard
    from .config import GeneratorConfig
except (ImportError, ValueError):
    from src.agents.dashboard.dashboard_generator import run_dashboard
    from src.agents.dashboard.config import GeneratorConfig

__all__ = ["run_dashboard", "GeneratorConfig"]


