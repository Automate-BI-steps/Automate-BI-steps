# BI Flow Dashboard Generator Agent

This repository contains `docs/notebooks/Dashboard_Generator_Agent.ipynb`, a deterministic
Dashboard Generator Agent for BI Flow. It generates a Microsoft Power BI
Project (PBIP) layout with a TMSL (`model.bim`) semantic model, DAX measures, PBIR report
definition files, KPI lineage, and machine-readable generation/test reports.


## Project structure

- `src/agents/dashboard/` — dashboard generator source modules
- `src/dashboard/` — compatibility package exports
- `resources/` — themes and knowledge assets
- `input/` — validated upstream inputs
- `generated/` — generated dashboard artifacts
- `scripts/` — executable build and server scripts
- `tests/` — automated tests
- `docs/` — documentation and notebooks
## Run

1. Install `requirements.txt` in Python 3.11 or newer.
2. Open `docs/notebooks/Dashboard_Generator_Agent.ipynb` in Jupyter or VS Code.
3. Set `INPUT_ROOT` to the directory containing the validated upstream outputs.
4. Set `OUTPUT_ROOT` to a writable output directory.
5. Set `DEMO_MODE = False` for production generation and run all cells.
6. Open the generated `.pbip` file in Power BI Desktop.

Expected input filenames are configurable in the notebook and default to:

- `cleaned_trade.csv` or `cleaned_trade.parquet`
- `data_model.json`
- `kpi_catalog.json`
- `bi_analyst_findings.json`
- `data_quality_report.json`
- optional `xai_lineage.json`

The generator fails when production mandatory inputs are missing or invalid.
`DEMO_MODE` is only for deterministic notebook tests and is explicitly marked
in the output metadata; it is not an upstream BI result.

## Project walkthrough

[`docs/notebooks/BI_Flow_Project_Walkthrough.ipynb`](./docs/notebooks/BI_Flow_Project_Walkthrough.ipynb) explains and runs every step of the agent (inputs, KPIs and DAX, RAG, LLM copilot, candidate planning, scoring, critic, optimizer, PBIP build, HTML dashboard, tests). Open it with the "BI Flow (.venv)" kernel.

## Execution and validation

Run the generator directly from the repository root:

```powershell
.venv\Scripts\python.exe -c "from src.agents.dashboard.dashboard_generator import main; main()"
```

The generator produces `dashboard_generation_report.json` and renders the PBIP layout.
The machine-readable status remains `PARTIAL_SUCCESS` until the external Power BI Desktop
procedure in [`docs/DASHBOARD_GENERATOR_AUDIT.md`](./docs/DASHBOARD_GENERATOR_AUDIT.md) is completed.

For reproducible notebook execution, register and select the project kernel:

```powershell
.venv\Scripts\python.exe -m ipykernel install --user --name bi-flow-venv --display-name "BI Flow (.venv)"
.venv\Scripts\python.exe -m jupyter nbconvert --to notebook --execute --ExecutePreprocessor.kernel_name=bi-flow-venv --ExecutePreprocessor.timeout=120 --stdout docs/notebooks/Dashboard_Generator_Agent.ipynb
```

## Phase 1 â€” Autonomous AI Dashboard Designer

BI Flow extends the dashboard generator into an intelligent, self-criticizing design system:

1. **Conversational Dashboard Copilot (`dashboard_copilot.py`)**:
   - Parses natural language business requests or structured input specifications.
   - Strictly maps requested metrics and dimensions against upstream catalogs (`kpi_catalog.json`, `data_model.json`, `bi_analyst_findings.json`).
   - Flags unsupported metrics under `unresolved_questions` without fabricating data.

2. **Multi-Candidate Plan Generation (`dashboard_planner.py`)**:
   - Generates three meaningfully distinct candidate design strategies:
     - **Executive Strategy**: Headline KPI cards, macroeconomic trends, and governance verification.
     - **Analytical Strategy**: Granular country and product distributions with detailed audit grids.
     - **Decision Strategy**: Actionable analyst findings-first cockpit with bilateral risk drills.

3. **Dashboard Quality Scoring (`dashboard_evaluator.py`)**:
   - Scores candidates across 8 weighted criteria (Objective Alignment, KPI Relevance, Question Coverage, Evidence Traceability, Visualization Suitability, Redundancy Penalty, Page Organization, and Compiler Compliance).
   - Hard deterministic gating: invalid tables, unknown columns, or duplicate IDs immediately disqualify candidates from selection.

4. **Evidence-Grounded Explanations (`VisualEvidence`)**:
   - Integrates `KnowledgeAgent` (`resources/knowledge/*.md`).
   - Tags every visual with structured evidence taxonomy (`UPSTREAM_FACT`, `DOMAIN_GUIDANCE`, `AI_INTERPRETATION`, `RECOMMENDATION`, `UNKNOWN`).

5. **Self-Criticizing Selection Workflow (`dashboard_critic.py`)**:
   - Multi-pass deterministic validation and bounded revision loop.
   - Evaluates, identifies structural omissions, applies deterministic corrections, and re-validates.

## Operating Modes

Configured via `config.py` (`GeneratorConfig`) or environment variables:

- **Deterministic Mode (`OperatingMode.DETERMINISTIC`)**:
  Generates standard verified 6-page dashboard without LLM calls.
  ```powershell
  .venv\Scripts\python.exe -c "from src.agents.dashboard.dashboard_generator import main; main()"
  ```

- **AI-Assisted Mode (`OperatingMode.AI_ASSISTED`)**:
  Orchestrates copilot interpretation, multi-candidate planning, 8-criteria scoring, critic validation, and compilation.
  ```powershell
  .venv\Scripts\python.exe -c "from src.agents.dashboard.config import GeneratorConfig, OperatingMode; from src.agents.dashboard.dashboard_generator import main; cfg = GeneratorConfig(mode=OperatingMode.AI_ASSISTED, llm_provider='mock'); main(config=cfg, user_context='Executive dashboard for export managers comparing partner countries and analyst findings')"
  ```

- **AI-Assisted Without RAG (`rag_enabled=False`)**:
  Runs AI planning and multi-candidate generation without domain knowledge chunk retrieval.

## Automated Testing

Run the full pytest suite (78 offline tests):

```powershell
.venv\Scripts\python.exe -m pytest -v
```

## Power BI validation boundary

The generator writes PBIP/TMSL/PBIR artifacts but does not claim that Power BI
Desktop has opened or rendered them. Power BI Desktop must be used as an
external integration test to validate the selected Desktop release, PBIR
schema compatibility, model refresh, DAX evaluation, visual rendering, and
interactions. The generated `dashboard_generation_report.json` records this
boundary and uses `partial_success` until that test is completed.



