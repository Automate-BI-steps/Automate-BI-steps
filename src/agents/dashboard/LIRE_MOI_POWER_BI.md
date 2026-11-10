# Ouvrir ce tableau de bord dans Power BI Desktop

Ce dossier est un **projet Power BI (PBIP)** produit par le pipeline BI Flow.

| Élément | Rôle |
|---|---|
| `dashboard_generator.pbip` | fichier à ouvrir dans Power BI Desktop |
| `dashboard_generator.SemanticModel/` | modèle : tables, mesures DAX, requêtes Power Query |
| `dashboard_generator.Report/` | pages et visuels |
| `dashboard_generator.source/` | **les données** : `FactTrade.csv` (table de faits), `BusinessInsights.csv`, `DataQuality.csv` |
| `dashboard_plan.json`, `dashboard_generation_report.json` | plan des pages et rapport de génération (valeurs attendues des KPI) |
| `relier_power_bi.py` | remet le chemin des données au bon endroit (étape 2) |

## 1. Prérequis
- Power BI Desktop (Windows), version récente.
- Si le `.pbip` est refusé : *Fichier > Options et paramètres > Options > Fonctionnalités en préversion*, cocher
  « Option d'enregistrement Power BI Project (.pbip) » et le format de rapport amélioré (PBIR), puis relancer Power BI Desktop.

## 2. Relier le projet à ses données (si le dossier a été déplacé ou dézippé ailleurs)
Le modèle lit les CSV via le paramètre `SourceFolder`, qui contient le chemin **de la machine où le pipeline a tourné**.
Depuis ce dossier :

```
python relier_power_bi.py              # met SourceFolder sur le dossier dashboard_generator.source d'ici
python relier_power_bi.py --ouvrir     # idem, puis ouvre le .pbip
```

Sans Python, à la main : dans Power BI Desktop, *Accueil > Transformer les données > Modifier les paramètres*, mettre le chemin complet du
dossier `dashboard_generator.source`, **avec un `\` final** (ex. `C:\projets\run_xxx\dashboard\dashboard_generator.source\`), puis OK et *Appliquer*.

## 3. Charger et vérifier
1. Ouvrir `dashboard_generator.pbip`, puis **Accueil > Actualiser** (obligatoire : un projet PBIP est livré sans données,
   tant qu'on n'a pas actualisé, toutes les cartes affichent **« (Vide) »**).
2. Vérifier que les tables `Fact_Trade`, `DimDate`, les `Dim...`, `BusinessInsights` et `DataQuality` se chargent sans erreur.
   Vue *Modèle* : un schéma en étoile, `Fact_Trade` au centre reliée à chaque dimension (voir § 5).
3. Contrôler les cartes de la page *Directeur commercial* contre les valeurs de l'onglet KPI de l'appli (ou `expected_kpi_values`
   dans `dashboard_generation_report.json`).

### Les cartes restent « (Vide) » après Actualiser
- Une fenêtre d'erreur s'est ouverte pendant l'actualisation : noter le nom de la table et le message.
  « Impossible de trouver le chemin » / « Could not find file » = étape 2 (chemin `SourceFolder`).
- Aucune erreur mais toujours vide : *Accueil > Transformer les données*, cliquer sur la requête `TradeData` et regarder
  l'aperçu. S'il est vide, le fichier `Fact_Trade.csv` (ou `FactTrade.csv`) du dossier `.source` est vide ou a d'autres colonnes.

## 4. Les pages du rapport (schéma en étoile, ex. Northwind)
Un tableau de bord par lecteur, sur le modèle de la maquette de référence (fond bleu nuit, cartes gris-bleu, chiffres rouge foncé) :

| Page | Cartes KPI | Graphiques |
|---|---|---|
| Directeur commercial | CA, clients actifs, commandes, panier moyen | CA par ville, évolution mensuelle (aires), treemap catégorie → produit, part des catégories |
| Administrateur | commandes, CA, meilleur employé, unités vendues | CA par mois, CA par employé, expédiées / non expédiées, commandes par pays |
| Directeur financier | CA brut, remises, taux de remise, fret / CA | CA brut et net par mois, remises par catégorie, CA par client, tableau des produits |
| Directeur logistique | expédiées, taux à l'heure, non expédiées, en retard, délai moyen | délai et fret par transporteur, expédiées et retards par employé, jauge expédiées / commandes |
| Qualité des données | - | constats de l'Agent 4 et contrôles de qualité |

Sur chaque page : à gauche, le panneau de **filtres** (Année, Période, Pays, Catégorie, Client, Transporteur), synchronisé sur toutes
les pages ; en haut, le titre « Dashboard <rôle> » et des **boutons** pour passer d'une page à l'autre (dans Power BI Desktop :
Ctrl + clic sur un bouton ; dans le service Power BI : simple clic). Le thème `BIFlowRoleTheme.json` se modifie dans
*Affichage > Thèmes > Personnaliser le thème actuel*.

**Bénéfice net** et **coût des marchandises vendues** (cartes de la maquette) ne sont pas affichés : les données n'ont pas de coût
d'achat, ces chiffres seraient inventés. Le fret est montré à part (*Fret / CA*). Sans schéma en étoile, le rapport générique
(Vue d'ensemble, Évolution, Par pays, Par produit, Clients et opérations, Constats et qualité) est produit à la place.

## 5. Le modèle (schéma en étoile)
Construit à partir des **tables nettoyées** (toutes leurs colonnes), pas de la table à plat de l'Agent 4 :

| Table | Contenu (Northwind) | Relation |
|---|---|---|
| `Fact_Trade` | une ligne par produit commandé : `order_details` + en-tête `orders` (dates, fret, clés) + `LineTotal` de l'Agent 4 ; mesures DAX | centre |
| `DimProduct` | `products` (conditionnement, prix catalogue, arrêté...) + `categories` (nom, description) | `Fact_Trade[ProductID]` → `DimProduct[ProductID]` |
| `DimCustomer` | `customers` (société, contact, fonction, ville, pays) | `Fact_Trade[CustomerID]` → `DimCustomer[CustomerID]` |
| `DimEmployee` | `employees` (nom, titre, ville, pays, responsable) | `Fact_Trade[EmployeeID]` → `DimEmployee[EmployeeID]` |
| `DimShipper` | `shippers` | `Fact_Trade[ShipperID]` → `DimShipper[ShipperID]` |
| `DimDate` | calendrier continu (Year, Quarter, Month, MonthNumber) | `Fact_Trade[OrderDate]` → `DimDate[Date]` |

Les clés étrangères sont masquées dans `Fact_Trade` : on filtre par les colonnes des dimensions (ex. `DimCustomer[Country]`).
Les mesures de l'Agent 4 sont traduites vers l'étoile (ex. part par pays : `ALL(DimCustomer[Country])`).
Si l'étoile ne peut pas être construite (pas de clés, lignes non alignées avec l'Agent 4), le modèle reste à plat et
`dashboard_generation_report.json` le dit (`semantic_model.schema = "flat"`).

## À savoir
- Ce projet a été **généré mais jamais ouvert dans Power BI Desktop** : le pipeline ne peut pas le faire. Le statut `PARTIAL_SUCCESS`
  signifie exactement cela. Les mesures DAX et le rendu des visuels sont à valider à l'ouverture.
- Les données sont un **instantané** du run. Nouvelle exécution du pipeline = nouveau dossier de run = nouveau projet à ouvrir (ou
  `python relier_power_bi.py --source <nouveau dossier .source>` pour réutiliser ce modèle avec les nouvelles données, si les colonnes sont les mêmes).
- Le schéma en étoile est reconstruit par l'Agent 5 à partir des tables nettoyées ; celui de l'Agent 3 (ETL) n'est pas utilisé.
- `Freight` (fret) est un montant par commande répété sur chaque ligne de produit, comme dans la table de l'Agent 4 : ne pas l'additionner tel quel.
- La publication vers le service Power BI (app.powerbi.com) n'est pas automatisée : *Accueil > Publier* depuis Power BI Desktop.
