# Dashboard Generator Audit

## Scope and evidence

Audit date: 2026-10-13. The actual entry point is
[`Dashboard_Generator_Agent.ipynb`](./notebooks/Dashboard_Generator_Agent.ipynb), which
now delegates to [`dashboard_generator.py`](./dashboard_generator.py).
The supplied inputs are under [`input/`](./input/) and the generated PBIP
project is under [`generated/`](./generated/).

Baseline evidence:

- The current suite contains 8 tests and passes in the configured Python
  environment.
- Baseline notebook execution succeeded, but validation only checked file
  existence.
- Baseline `model.bim` contained only `FactTrade`, while TMDL declared
  `DimDate`, `DimCountry`, `DimProduct`, and three relationships.
- Baseline visual JSON retained only `visualType` and position; query and
  projection bindings were discarded.
- Power BI Desktop is installed (`2.147.1085.0`), but opening, refresh, DAX
  execution, and rendering were not run by this audit.

## Findings

| Severity | Finding | Evidence | Status |
|---|---|---|---|
| High | Semantic model representations contradicted each other. | `generated/dashboard_generator.SemanticModel/model.bim` versus TMDL files and `relationships.tmdl` | Fixed by emitting the four tables and relationships in `model.bim` and testing them. |
| High | PBIR visual files lost query and projection bindings during generation. | Generated `visual.json` files contained only type and position. | Fixed by preserving visual configuration, query, and filters. |
| High | Input validation raised plain errors and did not return structured issue evidence. | Original `normalize` function. | Fixed with coded issues, severity, policy, row numbers, and continuation status. |
| Medium | Required six-page contract was conditional for Business Insights and therefore could produce four or five pages. | Original `pages` function. | Fixed; empty findings now produce an explicit no-findings page. |
| Medium | Automated checks did not validate model consistency, page set, visual bindings, or JSON structure. | Original `test_generation`. | Fixed with structural artifact validation and tests. |
| Medium | Duplicate, non-positive, and currency risks were not addressed. | Original `normalize` function and `data_model.json`. | Fixed with explicit warnings and rejection of multiple currencies when a currency column is present. Currency conversion remains unsupported because the documented contract has no conversion policy. |
| Medium | Generated visual selections encoded KPI measures as columns. | Original `visual()` output used `Column` for `Total Imports`, `Total Exports`, and `Trade Balance`. | Fixed by emitting `Measure` selections and validating measure/column references. |
| Medium | Dimension tables had no partitions and relationships lacked explicit semantics. | Original `model.bim`. | Fixed with distinct dimension partitions and active many-to-one, single-direction relationship metadata. |
| Low | Failed input validation returned only a message to callers. | Original `normalize` and `main`. | Fixed with `InputValidationError` carrying structured issues into the failure report. |
| Low | Notebook had no stable importable source module and had a missing cell id warning. | Original notebook. | Logic moved to a testable module; notebook remains executable. The warning is emitted by the installed nbformat version and does not prevent execution. |

## Implemented versus unavailable

Implemented: strict required-field/type/domain checks; explicit handling of
additional columns; duplicate and non-positive warnings; multiple-currency
protection; independent KPI calculations; six-page generation; model/table/
relationship consistency checks; visual query preservation; lineage output;
machine-readable test output.

Not implemented or not provable in this audit: Power BI Desktop opening, data
refresh, DAX execution, visual rendering, slicer interaction, and business
acceptance. Power BI Desktop is installed, but no supported automated Desktop
test was run; these remain manual integration checks covered by the procedure
below.

## Validation policy

- Missing columns, empty data, invalid dates/numbers/directions, and missing
  dimension values: **reject**.
- Additional columns: **accept with warning and preserve**.
- Duplicate full rows and zero/negative values: **accept with warning** because
  the documented contract has no transaction identifier and does not define a
  positivity rule.
- Multiple currencies: **reject** unless a documented conversion policy and
  currency column are supplied.
- Unsupported external Power BI operations: **do not claim success**.

## Power BI Desktop procedure

1. Open `generated/dashboard_generator.pbip` in the target Power BI Desktop
   release.
2. Confirm all six pages appear with their expected names.
3. Refresh the model and confirm the source CSV resolves without path errors.
4. Evaluate all four measures in a table visual and compare with
   `generated/dashboard_generation_report.json`.
5. Test date, country, product, and direction filters.
6. Confirm every visual has a rendered result or an intentional empty-data
   state.
7. Save a copy of the Desktop validation evidence and record the Desktop
   version, refresh result, DAX result, and rendering result.

Until those steps are completed, the report status must remain
`PARTIAL_SUCCESS`, not `DESKTOP_VALIDATED` or `PUBLISHED`.

