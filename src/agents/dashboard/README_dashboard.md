# Agent 5 : Dashboard (Hashem Omri)

Le code du générateur (`dashboard_generator.py`, `dashboard_planner.py`, `dashboard_critic.py`, ...) vient de la branche
`hashem-omri` ; les modifications sont listées ci-dessous (l'aperçu HTML `html_renderer.py` et `src/dashboard/` ont été supprimés). Ses ressources sont dans `resources/`
(thème Power BI, base de connaissances) et ses 13 fichiers de tests dans `tests/` (avec son `tests/conftest.py`).

## Ce qui a été ajouté pour le brancher
| Fichier | Rôle |
|---|---|
| `prep.py` | Adaptateur d'entrée : transforme la sortie de l'Agent 4 (table à plat, catalogue, valeurs, constats) en dossier d'entrée du générateur (`cleaned_trade.csv` ou `Fact_Trade.csv`, `data_model.json`, `kpi_catalog.json`, `bi_analyst_findings.json`, `data_quality_report.json`, `xai_lineage.json`) |
| `star_schema.py` | Schéma en étoile du modèle Power BI reconstruit à partir des tables nettoyées (clés détectées, en-tête fusionné dans les faits, flocons aplatis, place de chaque colonne de la table à plat) |
| `role_report.py` | Rapport par rôle sur l'étoile (Directeur commercial, Administrateur, Directeur financier, Directeur logistique, Qualité) : plan, panneau de filtres synchronisés, boutons de navigation, thème bleu nuit |
| `generic_report.py` | Rapport générique (sans étoile) : plan, table de dates, formats, mise en page (bandeau, segment Année synchronisé, thème), contrôle pandas des formules DAX |
| `step.py` | Étape du pipeline : `prep` -> `dashboard_node` -> archive `dashboard_project.zip` -> section `dashboard` du state |
| `tests/test_dashboard_prep.py` | 13 tests de l'adaptateur et de l'étape |

## Deux contrats, un seul générateur
Le générateur a été conçu pour une table import/export (`date, country, product, direction, value`).
- Si l'Agent 4 a compris les rôles `date`, `country`, `product`, `flow`, `value` et que le générateur accepte la table : **mode import/export**
  (les 4 KPI import/export ont leur formule intégrée au générateur).
- Sinon (ex. ventes B2B Northwind) : **mode générique**, construit par `generic_report.py` à partir des colonnes réelles de la table
  de faits, des rôles de l'Agent 4 (`data_model.json > roles`) et des mesures transmises : 6 pages au plus, 6 visuels au plus par page
  (Vue d'ensemble, Évolution, Par pays, Par produit, Clients et opérations, Constats et qualité) et 11 types de visuels
  (cartes, courbes, aires, colonnes, barres, combiné colonnes + courbe, anneau, treemap, tableaux triés, segment Année).
  Le PBIP et `dashboard_plan.json` sont produits à partir de ce même plan. Avec le schéma en étoile, `role_report.py` le remplace
  par les tableaux de bord par rôle (voir plus bas).
  Le modèle reçoit une table de dates continue `DimDate` (Year, Quarter, Month, MonthNumber) reliée à la date principale.

### Corrections du mode générique (novembre 2026)
| Constat sur un run Northwind | Cause | Correction |
|---|---|---|
| Plan et HTML à 6 pages « trade » (FactTrade.Direction, DimDate.Date...) mais PBIP à 3 pages ; onglets HTML vides | `create_deterministic_plan` ignorait le mode générique | plan générique unique (`build_generic_plan`) |
| `critic_report` REJECTED : 8 visuels > 6 sur la page d'accueil | toutes les mesures en cartes + la courbe trade | ≤ 5 cartes + 1 courbe ; mesures de part déplacées dans les tableaux |
| Cartes « Top Countries / Top Products » sans sens (100 % sans filtre, « N/A » dans le HTML) | mesure de part `DIVIDE(..., ALL(col))` posée en carte | colonne « Part du total » des tableaux par pays / par produit |
| Actualisation qui peut échouer : `DimShippedDate` contient des vides (commandes non expédiées) | une dimension par colonne de date, vides compris | une seule table de dates `DimDate` ; dimensions texte sans vides et sans doublons de casse |
| `country` traité comme mesure (contient « count ») | heuristique par nom | une colonne texte reste une dimension |
| `discount = 0` signalé comme anomalie sur 1 317 lignes | contrôle « ≤ 0 » sur toutes les colonnes numériques | « < 0 » partout, « ≤ 0 » seulement pour les montants |
| Mode `ai_assisted` en `VALIDATION_FAILED` | le critique validait les champs trade sur un jeu générique | le critique ne connaît plus que les champs réels ; repli sur le plan générique |
| Pas de format d'affichage | mesures sans `formatString` | %, jours, entiers et montants formatés |
| Placement en ligne fixe (cartes 300×150, graphiques 580×260) | — | grille : segments, cartes, puis graphiques sur 2 colonnes, tout dans 1280×720 |

### Schéma en étoile et mise en page (novembre 2026)
| Constat | Cause | Correction |
|---|---|---|
| Colonnes effacées (identifiants, contact et ville des clients, titre des employés, conditionnement des produits, description des catégories...) | le modèle Power BI était la table à plat de l'Agent 4 (15 colonnes) | `star_schema.py` reconstruit l'étoile à partir des tables nettoyées, avec toutes leurs colonnes |
| Dimensions à une colonne (`DimCountry`, `DimProductName`, `DimCompanyName`...) | une « dimension » par colonne texte de la table à plat | une dimension par table parente (`DimProduct`, `DimCustomer`, `DimEmployee`, `DimShipper`) + `DimDate`, reliées par leurs clés |
| Mise en page en grille simple, sans titre de page ni thème | — | bandeau titre + description, segment Année synchronisé sur toutes les pages, tableaux à droite sur toute la hauteur, thème `BIFlowTheme.json` |

Le plan, les formules du catalogue et le HTML restent écrits sur la table à plat de l'Agent 4 (celle de ses KPI) ; au moment d'écrire
le PBIP, chaque colonne est envoyée vers la table de l'étoile qui la porte (`data_model.json > star > locations`, ex.
`shipperName -> DimShipper[CompanyName]`, affichée « ShipperName »). L'alignement ligne à ligne avec la table de l'Agent 4 est vérifié
avant de reprendre ses colonnes calculées (`lineTotal`) ; sinon repli sur le modèle à plat (tests : `tests/test_star_schema.py`).

Les fichiers `visual.json`, `page.json`, `report.json`, `pages.json` sont validés contre les schémas officiels PBIR de Microsoft
(copie hors ligne : `resources/pbir_schemas/`, test `tests/test_generic_report.py`).

## Lien avec Power BI Desktop
Le projet généré (`dashboard/`, aussi livré en `dashboard_project.zip`) se compose de `dashboard_generator.pbip`, du modèle sémantique (`model.bim`),
du rapport et de **ses données** : `dashboard_generator.source/` (`FactTrade.csv` = table de faits de l'étoile, `DimProduct.csv`, `DimCustomer.csv`,
`DimEmployee.csv`, `DimShipper.csv`, `BusinessInsights.csv`, `DataQuality.csv` ; à plat sans étoile : `FactTrade.csv` = table de l'Agent 4).
Le modèle les lit via le paramètre Power Query `SourceFolder`, que le générateur remplit avec le chemin absolu de la machine qui a lancé le pipeline.
Déplacé ou dézippé ailleurs, le projet doit être relié : `python relier_power_bi.py` (copié dans chaque projet avec `LIRE_MOI_POWER_BI.md`, bibliothèque
standard seule) remet `SourceFolder` sur le dossier `*.source` voisin ; `--ouvrir` lance ensuite le `.pbip`. Sans Python : *Transformer les données >
Modifier les paramètres > SourceFolder*, chemin complet avec `\` final. Non vérifié dans Power BI Desktop (voir « Limites connues »).

## Règle d'intégrité
Une formule DAX n'est transmise que si toutes ses références (table, colonne, mesure) existent dans le modèle généré. Sinon le KPI est
écarté et le dit (`dashboard.kpis_skipped`, avertissements) : ex. la croissance annuelle de l'Agent 4 utilise `Dim_Date[Date]`, absente du modèle.
Un KPI qui dépend d'un KPI écarté est écarté aussi. Les cartes de l'aperçu HTML affichent les valeurs publiées par l'Agent 4 ; ses graphiques
recalculent les mêmes formules DAX avec pandas (`generic_report.evaluate`, motifs SUM, DISTINCTCOUNT, DIVIDE, part ALL(...), délai et taux
d'expédition), avec les mêmes résultats que l'Agent 4 sur Northwind.
La croissance annuelle reste écartée même avec `DimDate` : sa formule (`SAMEPERIODLASTYEAR`) compare des années incomplètes et afficherait
-28,6 % pour 2015 (5 mois contre 12) au lieu des +79,8 % « mêmes mois » de l'Agent 4. La page Évolution montre le CA par année et par mois à la place.

## Limites connues
- Statut `PARTIAL_SUCCESS` normal : Python ne peut pas ouvrir Power BI Desktop, donc rafraîchissement, évaluation DAX et rendu ne sont pas vérifiés.
- Mode `ai_assisted` : sur un jeu générique, le copilote LLM ne connaît que le schéma import/export ; son plan est refusé par le critique et le
  générateur se rabat sur le plan générique déterministe (statut `deterministic_fallback`). Le mode déterministe reste le réglage conseillé.
  Aucun appel réel à un LLM n'a pu être testé (réseau bloqué).
- Le schéma en étoile de l'Agent 3 n'est pas consommé (il demande un LLM) : l'Agent 5 reconstruit le sien à partir des tables nettoyées.
- Thème `BIFlowTheme.json` : propriétés vérifiées contre le schéma officiel des thèmes (`reportThemeSchema-2.156.json`, non copié dans le dépôt),
  rendu non vérifié dans Power BI Desktop.

### Rapport par rôle, sorties allégées (10 novembre 2026)
- **Rapport par rôle** (`role_report.py`), produit dès que l'étoile existe : une page par lecteur, sur le modèle de la maquette de
  référence. Cartes KPI (comptées comme une seule rangée par le critique), graphiques en grille 2 × 2, panneau de filtres à gauche
  (Année, Période, Pays, Catégorie, Client, Transporteur, `syncGroup` par champ), boutons `actionButton` + `visualLink PageNavigation`,
  thème `BIFlowRoleTheme.json` (validé contre `reportThemeSchema-2.156.json`). Un visuel n'est créé que si ses mesures et colonnes existent.
- **Nouveaux KPI de l'Agent 4** utilisés : clients actifs, CA par client, produits vendus, unités, unités par commande, meilleur employé,
  CA brut, remises, taux de remise, fret total / moyen / rapporté au CA, commandes expédiées, non expédiées, en retard.
  `physical_dax` restreint un `DISTINCTCOUNT` sur une dimension aux lignes de faits (89 clients et non 91).
- **Bénéfice net et COGS** de la maquette : non calculables (pas de coût d'achat), donc absents.
- **Plus de HTML** ; `BI_FLOW_DEBUG_OUTPUTS=false` (défaut) : seuls le projet Power BI, `dashboard_plan.json` et
  `dashboard_generation_report.json` sont écrits (avant : + 7 fichiers de diagnostic et un journal JSONL).
- **Mode IA ignoré sur l'étoile** (le planificateur ne connaît que la table à plat) : plus d'appels LLM lents pour un plan rejeté.
- `relier_power_bi.py` n'est plus copié dans `outputs/runs/` (il faisait redémarrer `uvicorn --reload`) : il n'est que dans le zip.

