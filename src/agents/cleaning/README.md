# Agent Quality & Cleaning + agent de traçage (BI Flow, DSO2)

Trois briques, alignées sur le rapport BI Flow (ch. 5.3 et 6.1) :

| Brique | Fichier | Rôle |
|---|---|---|
| Agent de nettoyage | `agent.py` + `tools.py` | lit plusieurs CSV et Excel, traite **table par table**, choisit lui-même les outils à appliquer |
| Outil LLM (optionnel) | `llm_planner.py` | analyse chaque table avec un LLM Groq et accepte ou refuse chaque outil proposé |
| Agent de traçage | `tracer.py` | historise chaque décision et chaque version des données, sans jamais rien écraser |
| Comparaison | `compare.py` | rapport HTML avant / après par table, et comparaison libre de deux fichiers |

```
fichiers CSV / Excel ─► Agent Cleaning ──► tables nettoyées (CSV)
                           │  (observe, décide, agit)
                           ▼
                        Agent de traçage ─► history/  (journal, versions, trace, comparaison)
```

## Utilisation

```bash
cd cleaning_agent
python agent.py                                   # nettoie data/raw -> data/clean
python agent.py a.csv b.xlsx dossier/ --output ../data/clean
python agent.py fichier.xlsx --impute             # autorise l'imputation (médiane / mode)
python agent.py fichier.csv --monthfirst          # dates américaines 03/04/2024 = 4 mars
python compare.py avant.csv apres.xlsx --key orderID --out comparaison.html
python agent.py fichier.xlsx --planner llm       # analyse et choix des outils par le LLM Groq
python -m pytest tests                            # tests (dont un faux serveur Groq local)
```

Dans un orchestrateur LangGraph : `cleaning_node(state)` lit `state["input_paths"]` et renseigne `state["clean_data_dir"]`.
En ligne de commande, `--outliers ask` (défaut dans un terminal) demande pour chaque colonne ayant des valeurs extrêmes : vider, ramener à la limite ou garder. `advice` suit le conseil, `empty` vide tout, `cap` ramène tout, `keep` ne touche à rien.

## Comment l'agent choisit ses outils

Chaque CSV devient une table (encodage et séparateur `,` `;` tabulation `|` détectés), chaque feuille Excel aussi.
Pour chaque table, l'agent passe les outils dans cet ordre. Chaque outil **observe** d'abord la table ou la colonne, et ne s'applique que si le problème est présent. La raison est écrite dans la trace. Le but : des tables **prêtes pour un ETL**.

| # | Outil | S'applique quand… | Action |
|---|---|---|---|
| 1 | Noms de colonnes compatibles ETL | espaces, accents, symboles, en-tête vide ou en double (`Prix unitaire (€)`) | `prix_unitaire` (les noms déjà propres ne bougent pas) |
| 2 | Uniformiser les valeurs manquantes | `N/A`, `null`, `-`, `?`, `inconnu`… | vraie valeur vide |
| 3 | Supprimer les lignes / colonnes vides | ligne ou colonne entièrement vide | suppression |
| 4 | Espaces et caractères invisibles | espaces en trop, retours à la ligne, tabulations, espaces insécables, caractères de largeur nulle | nettoyage |
| 5 | Corriger l'encodage | `Ã©` au lieu de `é` | réparation |
| 6 | Harmoniser les clés de jointure | colonne `…ID` / `…code` en majuscules sauf quelques valeurs (`vinet`) | majuscules, pour que les jointures de l'ETL fonctionnent |
| 7 | Convertir en nombre | ≥ 90 % de valeurs numériques (`32,38 €`, `1 234,5`, `15 %`) | nombre ; **les codes (postal, téléphone, SIRET, zéro initial) restent en texte** |
| 8 | Convertir en date | ≥ 90 % de dates, formats mélangés | `AAAA-MM-JJ` |
| 9 | Convertir en booléen | oui/non, yes/no, vrai/faux, ou `0`/`1` dans une colonne `discontinued`, `isActive`… | `True` / `False` |
| 10 | Taux entre 0 et 1 | colonne `discount`, `remise`, `taux`… avec des valeurs comme `15` | `0,15` |
| 11 | Montants au centime | prix, montant, fret avec plus de 2 décimales | arrondi |
| 12 | Téléphones | colonne `phone`, `tel`, `fax`… aux formats mélangés | chiffres groupés par des espaces |
| 13 | Standardiser les pays | colonne `country` / `pays` avec alias | `USA`, `UK`, `Germany`… |
| 14 | Unifier les variantes | même catégorie écrite avec une casse ou des accents différents | forme la plus fréquente |
| 15 | Textes en MAJUSCULES | quelques valeurs criées au milieu de textes normaux (`MARIE CURIE`) | `Marie Curie` |
| 16 | **Décomposer les conditionnements** | texte composite du type `24 - 12 oz bottles`, `10 boxes x 20 bags`, `1 kg pkg.`, `750 cc per bottle` | 6 nouvelles colonnes : `nb_unites`, `taille_unite`, `unite_mesure`, `contenant`, `quantite_totale` (convertie en g ou en ml), `unite_base` ; la colonne d'origine est gardée |
| 17 | Supprimer les doublons exacts | lignes identiques | suppression |
| 18 | Imputer (option `--impute`) | < 30 % de vides, hors clés et dates | médiane ou mode |
| 19 | Signaler clés en double, négatifs, manquants | | **noté dans la trace seulement** |
| 20 | Valeurs extrêmes | hors écart interquartile × 3 (sur l'ordre de grandeur pour les prix, montants et quantités) | **seulement après accord de l'utilisateur** : mettre à vide, ramener à la limite, ou garder |

Pour les valeurs extrêmes, l'agent montre chaque ligne concernée (nom du produit ou clés), la valeur habituelle et un diagnostic : valeur de remplissage (`9999`) ou valeur 50 fois plus grande que d'habitude = erreur de saisie probable (conseil : vider) ; sinon rare mais possible (conseil : garder).
Pour `quantityPerUnit` de Northwind, les 77 valeurs sont reconnues ; une once (`oz`) est liquide pour les bouteilles et canettes (29,57 ml), en poids sinon (28,35 g).
Pour ajouter un outil, il suffit d'écrire une fonction `detect` et une fonction `apply` dans `tools.py`, de l'inscrire dans `TOOLS` et d'ajouter sa phrase dans `compare.PLAIN`.

## Contrôles « prêt pour l'ETL »

Après le nettoyage, chaque table est contrôlée (`compare.etl_checks`) : noms de colonnes utilisables, bon type pour chaque colonne, clé unique (simple ou composée), aucune ligne en double, liens entre tables (ex. chaque `customerID` des commandes existe dans la table clients) et cohérence des dates (`shippedDate` après `orderDate`). Une table qui échoue un contrôle est affichée comme « à reprendre ».

## Outil LLM (Groq) : analyse et choix des outils

Avec `--planner llm`, l'agent ajoute une étape d'analyse par un LLM pour chaque table (`llm_planner.py`) :

1. les règles simulent le nettoyage et listent les candidats (outil, colonne, raison) ;
2. le LLM reçoit le profil de la table (colonnes, types, vides, valeurs distinctes, exemples) et ces candidats ;
3. il rédige une **analyse** (rôle de la table, clés, qualité, risques), **accepte ou refuse** chaque candidat avec une justification, et propose des recommandations pour un humain ;
4. l'agent n'applique que les outils acceptés. Le LLM choisit seulement : il ne peut ni inventer un outil ni injecter une valeur.

Tout est tracé : l'analyse et le modèle utilisé figurent dans `trace.md`, `trace.json` et `comparaison.html`. Les refus apparaissent avec le statut `rejected`.

Configuration : copier `.env.example` en `.env` à la racine et y mettre `GROQ_API_KEY`.
- Modèle par défaut : `openai/gpt-oss-120b`, modifiable avec `GROQ_MODEL` ou `--model`.
- Au démarrage, l'agent interroge `GET /models` et vérifie que le modèle existe sur le compte. Sinon, il se replie dans l'ordre sur `llama-3.3-70b-versatile`, `openai/gpt-oss-20b` puis `llama-3.1-8b-instant`.
- Sans clé, ou si l'API échoue, l'agent continue en mode règles et le note dans la trace.

```bash
python agent.py ../data/raw --planner llm
python agent.py fichier.xlsx --planner llm --model llama-3.3-70b-versatile
```

## Traçage et historisation (`history/`)

| Fichier | Contenu |
|---|---|
| `cleaning_history.jsonl` | une ligne par étape, pour tous les runs (outil, colonne, raison, paramètres, lignes avant/après, nb de valeurs modifiées, exemples) |
| `runs_index.csv` | une ligne par run : date, fichiers, nb de corrections, points signalés, statut |
| `runs/<run_id>/before/` | les tables telles que lues |
| `runs/<run_id>/after/` | les tables nettoyées |
| `runs/<run_id>/trace.md` | la trace lisible par un humain |
| `runs/<run_id>/trace.json` | la même trace pour l'agent BI Auditor / XAI, avec le sha256 de chaque fichier source |
| `runs/<run_id>/comparaison.html` | le rapport avant / après |

## Comparaison avant / après

`comparaison.html` (et l'application web) n'affiche que les tables à traiter. Pour chacune :
- ce qui a été corrigé, en phrases courtes (« 77 conditionnements décomposés en colonnes exploitables ») ;
- les nouvelles colonnes, avec des exemples variés ;
- les contrôles « prêt pour l'ETL » ;
- les seules lignes modifiées, ancienne valeur barrée et nouvelle valeur en gras, puis les lignes supprimées et leur raison.

## Jeu de démonstration

`samples/make_dirty_samples.py` fabrique `clients_sale.csv` (séparateur `;`, encodage Windows) et `ventes_sale.xlsx` (3 feuilles) à partir de Northwind, avec des erreurs injectées : pays écrits de plusieurs façons, noms en majuscules, clés en minuscules, remises en `15 %`, en-tête `Prix unitaire (€)`, prix `9999`… Sur ce jeu, l'agent traite les 4 tables, décompose `quantityPerUnit` et pose 2 questions sur des valeurs extrêmes (le `9999` est diagnostiqué comme erreur de saisie).

Sur le dataset réel (`data/raw`), l'agent décompose `quantityPerUnit`, convertit `discontinued` (0/1) en booléen et corrige 2 espaces en double. Les 7 tables passent les contrôles ETL, liens entre tables compris.

## Intégration avec l'agent de profilage
Dans ce dépôt, l'agent est un paquet (`src/agents/cleaning`). Il reçoit le rapport de profilage (`state["profiling"]`) :
`node.py` (nœuds LangGraph et étapes `start_cleaning` / `finish_cleaning` / `verify_cleaning`), `profiling_link.py`
(problème du profilage -> traitement -> état final). Voir le README à la racine.
