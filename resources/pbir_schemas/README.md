# Schémas officiels PBIR (copie hors ligne)

Copie des schémas JSON publiés par Microsoft pour le format de rapport Power BI « PBIR »
(`https://developer.microsoft.com/json-schemas/fabric/item/report/definition/...`), récupérés depuis
https://github.com/microsoft/json-schemas (`fabric/item/report/definition`, licence MIT, voir `LICENSE`).

Ils servent au test `tests/test_generic_report.py`, qui valide chaque `visual.json`, `page.json`,
`report.json`, `pages.json` et `version.json` produit par le générateur, sans accès réseau.
Les chemins reprennent ceux des URL `$schema` (ex. `visualContainer/2.12.0/schema.json`).
