# Change Summary

- [`dashboard_generator.py`](./dashboard_generator.py): extracted the generator
  into an importable implementation; added structured input validation,
  independent KPI calculations, consistent model generation, visual bindings,
  lineage, and PBIP/PBIR structural checks.
- [`Dashboard_Generator_Agent.ipynb`](./notebooks/Dashboard_Generator_Agent.ipynb):
  retained the notebook entry point and changed it to execute the tested module.
- [`tests/test_dashboard_generator.py`](./tests/test_dashboard_generator.py):
  added seven focused tests for KPIs, invalid inputs, warnings, and generated
  semantic/report artifacts.
- [`DASHBOARD_GENERATOR_AUDIT.md`](./DASHBOARD_GENERATOR_AUDIT.md): recorded
  baseline evidence, findings, policies, limitations, and Desktop procedure.
- [`DASHBOARD_GENERATOR_TEST_REPORT.md`](./DASHBOARD_GENERATOR_TEST_REPORT.md):
  recorded actual commands, outcomes, environment, and unexecuted tests.
- [`validation_summary.json`](./validation_summary.json): machine-readable
  validation status and evidence.

## Corrections du 2026-10-13 (suite Ã  l'analyse du projet)

- Chemin source : le CSV est lu via le paramÃ¨tre Power Query `SourceFolder`
  (modifiable dans Transform data > Edit parameters) au lieu d'un chemin codÃ©
  en dur dans chaque partition.
- Chargement CSV : colonnes sÃ©lectionnÃ©es par nom (plus de `Columns=5`) et
  typÃ©es avec `Table.TransformColumnTypes`. Le CSV source ne contient que les
  cinq colonnes du modÃ¨le.
- Un seul format de modÃ¨le : `model.bim` ; le dossier TMDL incomplet n'est plus Ã©crit.
- Visuels : mesures DAX et `Sum(Value)` dans les rÃ´les Y, axes sur
  DimDate/DimCountry/DimProduct, type `tableEx` pour les tableaux.
- Catalogue de KPI : une carte par KPI supportÃ© ; champ optionnel `dax` pour un
  KPI sans formule intÃ©grÃ©e ; les KPI non visualisables sont listÃ©s dans
  `not_visualised` avec un avertissement `KPI_NOT_VISUALISED` au lieu d'Ãªtre
  ignorÃ©s ou de faire Ã©chouer la gÃ©nÃ©ration.
- Pages Business Insights et Data Quality : tables `BusinessInsights` et
  `DataQuality` chargÃ©es depuis les constats et le rapport qualitÃ©.
- ContrÃ´le KPI : recalculÃ© depuis le CSV rÃ©ellement chargÃ© par Power BI (et non
  plus comparÃ© Ã  lui-mÃªme).
- DÃ©terminisme : `run_id` dÃ©rivÃ© des entrÃ©es ; seuls les rapports JSON gardent
  un horodatage.
- Avertissements d'entrÃ©e plus dupliquÃ©s ; lignÃ©e par KPI limitÃ©e aux pages et
  visuels qui l'utilisent rÃ©ellement.
- Tests : 14 (6 nouveaux).

## Correction des pages blanches (2026-10-13)

Comparaison avec un rapport PBIR enregistrÃ© par Power BI Desktop (modÃ¨le
`_bpareporttemplate` du paquet MIT `semantic-link-labs`) :

- `pages.json` est Ã©crit dans `definition/pages/pages.json` (il Ã©tait dans
  `definition/`, oÃ¹ Desktop ne le lit pas).
- `definition/version.json` passe Ã  `2.0.0`, la valeur Ã©crite par Desktop.
- `report.json` dÃ©clare le thÃ¨me de base `CY24SU06` et son package de
  ressources ; le fichier de thÃ¨me est copiÃ© depuis `resources/CY24SU06.json`
  vers `StaticResources/SharedResources/BaseThemes/`.
- Les projections des visuels portent `nativeQueryRef`, l'axe est marquÃ©
  `active` et chaque visuel a `drillFilterOtherVisuals`.
- Les fichiers gÃ©nÃ©rÃ©s sont validÃ©s contre les schÃ©mas JSON officiels de
  Microsoft (0 erreur). Tests : 15.

