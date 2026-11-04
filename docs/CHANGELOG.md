# CHANGELOG â€” Dashboard Generator (Agent 5)

## [Unreleased] â€” Generic Pipeline Integration

### Added
- **Generic Schema Support**:
  - `classify_data_model(data_model: dict) -> dict[str, str]`: Classifies columns from `data_model.json` star schema into `measure`, `dimension`, or `date`.
  - `is_trade_preset(data_model: dict, frame_cols: list[str]) -> bool`: Automatically detects if dataset is trade data (has direction column with Import/Export semantics) or generic data.
  - Non-trade datasets (e.g., sales, marketing, telemetry) are fully supported without requiring `country`, `product`, or `direction` columns.
  - Multi-table star schema support: Reads dimension tables from disk matching `data_model.json` dimension definitions.
- **Generic KPI DAX Translation**:
  - `_translate_expression(expr: str, fact_table: str) -> str | None`: Automatically translates standard aggregator expressions (`SUM(col)`, `AVG(col)`, `AVERAGE(col)`, `COUNT(col)`, `DISTINCTCOUNT(col)`, `COUNTROWS()`) into DAX formulas targeting the active fact table.
  - Catalog `dax` field is prioritized when provided.
  - Non-translatable/unsupported KPIs are skipped with a warning and recorded in `warnings` and `not_visualised` without crashing.
- **Dynamic Critic Schema Validation**:
  - `DashboardCritic.evaluate(plan, bundle, valid_tables=None)`: Dynamically merges tables and columns from `bundle.data_model` and `bundle.data.columns` into `active_tables`, validating any dataset's tables, columns, and measures while retaining strict validation against hallucinated fields.
- **Optional Upstream Inputs & Graceful Fallbacks**:
  - `bi_analyst_findings.json` is now optional: When missing, a fallback (`findings: []`, `recommendations: []`, `reporting_period: "unavailable"`, `confidence: "unavailable"`) is used, and `MISSING_FINDINGS` warning is issued.
  - `data_quality_report.json` is now optional: When missing, a basic quality report is computed on the fly from the fact table (row count, null counts per column) with a `MISSING_QUALITY_REPORT` warning.
  - `xai_lineage.json` is optional: When missing, recorded with a `MISSING_LINEAGE` warning.
- **Programmatic Entry Point**:
  - `run_dashboard(input_dir: Path, output_dir: Path, config: GeneratorConfig | None = None) -> dict[str, Any]`:
    Returns a JSON-serializable dictionary with:
    `{status, mode, selected_plan, score, candidates, artifacts: {pbip_dir, html_path, plan_json, critic_report}, warnings, errors}`.
    Catches all internal exceptions and records them into `errors` list instead of raising.
- **LangGraph Integration Node**:
  - `node.py` and `dashboard/node.py`: Implements `dashboard_node(state) -> dict[str, Any]` compatible with LangGraph state containing `state["dashboard_prep"]["input_dir"]` and `state["run"]["run_dir"]`. Catches exceptions into `errors`.
- **Package Exports**:
  - `__init__.py` exporting `run_dashboard` and `GeneratorConfig`.
- **Groq LLM Support**:
  - Added `groq` provider option via `GROQ_API_KEY` (using OpenAI-compatible endpoint `https://api.groq.com/openai/v1` and `llama-3.3-70b-versatile`) with deterministic fallback.
- **Comprehensive Tests**:
  - Added `tests/test_generic_schema.py` verifying generic sales dataset, missing optional inputs, unsupported KPI handling, JSON serialization, and LangGraph `dashboard_node`.

### Changed
- `validate_frame(frame, file_name, trade_mode=True)`: Added `trade_mode` parameter. When `trade_mode=False`, direction and trade-specific dimension checks are bypassed.
- `_fact_csv(data)`: Preserves all dataset columns with proper title-casing rather than filtering to fixed trade columns.
- `model_bim()` and PBIR generation: Fact table name and column types are derived dynamically from `data_model.json` and fact table data when not running under trade preset.
- `../scripts/generate_clean.py`: Refactored to a thin CLI wrapper around `run_dashboard()` using `logging` instead of `print()`.
- `html_renderer.py`: Safeguarded chart and filter generation to only render trade-specific visuals when trade columns exist.

### Backward Compatibility
- All 79 baseline tests remain 100% green. Total test count is now 86.
- The existing trade schema functions as an automatic preset whenever trade columns are present.


