# Pipeline automatisé — carte immobilière multi-villes

Reprend en code tout ce qu'on a fait à la main au fil de la conversation :
ventes DVF, réseau de transport, sécurité SSMSI, calcul des zones — pour
n'importe quelle ville française, sans re-télécharger des CSV à la main.

## Ce qui est automatisé (vraies API, pas de scraping HTML)

| Donnée | Source | Fichier |
|---|---|---|
| Ventes DVF | `files.data.gouv.fr/geo-dvf` (par commune/année) | `pipeline/fetch_dvf.py` |
| Contours communes | `geo.api.gouv.fr` | `pipeline/fetch_communes.py` |
| Réseau de transport | Catalogue `transport.data.gouv.fr` (GTFS à jour) | `pipeline/fetch_gtfs.py` |
| Sécurité | Base communale SSMSI via l'API `data.gouv.fr` | `pipeline/fetch_security.py` |

## Ce qui reste manuel (et pourquoi)

- **Liste des communes** d'une ville : à remplir une fois dans `config/<ville>.yaml`
  (ou récupérer via `fetch_communes.communes_from_epci("<siren>")` si tu as le
  SIREN de la métropole — voir banatic.interieur.gouv.fr).
- **Arrêts "centre-ville"** pour le calcul du temps de trajet : aucune API ne
  devine où se trouve le centre historique d'une ville. Regarde une fois
  `stops.txt` du GTFS et choisis 5-7 noms d'arrêts au cœur de la ville.
- **Contours de quartiers officiels** : chaque métropole publie ça
  différemment sur son portail open data (ou pas du tout). Pas d'API
  nationale unifiée pour ça — à chercher manuellement une fois par ville.
- **Risques naturels** : pas encore intégrés à ce pipeline (l'API Géorisques
  existe — `georisques.gouv.fr/api-catalogue` — mais n'a pas encore été
  branchée, voir §Prochaines étapes).

## Installation

```bash
git clone <ton-repo>
cd <ton-repo>
python -m venv venv && source venv/bin/activate   # ou l'équivalent Windows
pip install -r requirements.txt
```

## Utilisation manuelle (en local)

```bash
# Génère output/final_data_dijon.json
python run_pipeline.py config/dijon.yaml

# Génère la carte finale à partir du template + des données
python inject_into_html.py template.html \
    output/final_data_bordeaux.json \
    output/final_data_dijon.json \
    -o carte.html
```

Options utiles pendant que tu mets au point une nouvelle config :
```bash
python run_pipeline.py config/dijon.yaml --skip-transit --skip-security
```
(le plus long, c'est le GTFS — utile de le sauter pendant que tu débogues la
partie DVF).

## Ajouter une nouvelle ville

1. Crée `config/<ville>.yaml` en copiant `config/dijon.yaml` comme modèle
   (liste des codes INSEE, terme de recherche GTFS, arrêts centre-ville).
2. `python run_pipeline.py config/<ville>.yaml`
3. Dans `template.html`, trois ajouts à la main (cherche `bordeaux:` et
   `dijon:` dans le fichier pour voir le patron à copier) :
   - un bouton dans `#citySwitcher` (`<button class="city-btn" data-city="...">`)
   - une entrée dans l'objet JS `CITIES` (mêmes champs que `dijon`)
   - une entrée dans l'objet JS `CITY_TEXT` (titre, description, méthodologie)
   - le jeton `__DATA_JSON_<VILLE>__` à ajouter dans `TOKENS` de `inject_into_html.py`

   C'est la seule étape non automatisée : elle prend 10 minutes, pas plus.
   Un jour, on pourra généraliser le JS pour lire une liste de villes
   dynamique plutôt que 2 blocs nommés en dur — je ne l'ai pas fait ici
   pour ne pas risquer de casser la carte qui tourne déjà en production.

## Déploiement automatique (GitHub Actions)

Le fichier `.github/workflows/update-map.yml` est déjà prêt : il tourne le
1er de chaque mois, régénère les données, reconstruit `index.html` et le
commit automatiquement si quelque chose a changé.

**Pour l'activer :**
1. Pousse tout ce dossier dans ton repo GitHub (celui qui héberge déjà ta
   carte via GitHub Pages).
2. Dans **Settings → Actions → General → Workflow permissions**, coche
   **"Read and write permissions"** (sinon le commit automatique échouera —
   c'est désactivé par défaut sur un repo neuf).
3. Va dans l'onglet **Actions** de ton repo, tu devrais voir le workflow
   "Mise à jour de la carte immobilière" — clique sur **"Run workflow"**
   pour le tester tout de suite sans attendre le 1er du mois.
4. Si tout se passe bien, un commit automatique apparaît avec le nouveau
   `index.html`. GitHub Pages se met à jour tout seul dans la foulée.

Pour changer la fréquence, modifie la ligne `cron:` (format standard cron,
[crontab.guru](https://crontab.guru) aide à le composer).

## Limites connues / points à vérifier toi-même une fois

Je n'ai pas pu exécuter ce pipeline dans mon environnement (accès réseau
restreint aux domaines gouvernementaux depuis là où je travaille), donc je
n'ai pas pu le tester de bout en bout contre les vraies API. Le code suit
exactement les formats de données qu'on a manipulés ensemble tout du long,
mais vérifie ces points au premier run :

- **URLs `files.data.gouv.fr/geo-dvf`** : le pattern `.../csv/{annee}/communes/{dept}/{insee}.csv.gz`
  est documenté et stable depuis plusieurs années, mais teste une URL à la
  main dans un navigateur avant un premier run complet.
- **Catalogue transport.data.gouv.fr** : le terme de recherche
  (`gtfs_search_term`) doit correspondre à un mot du *titre* du jeu de
  données côté transport.data.gouv.fr — si `fetch_gtfs.py` ne trouve rien,
  va sur https://transport.data.gouv.fr/datasets et copie le nom exact.
- **Ressource SSMSI** : le nom de la ressource pourrait changer de
  formulation d'un millésime à l'autre (le filtre cherche "communale" dans
  le titre) — si ça casse, va vérifier le nom exact sur la page du jeu de
  données et ajuste le filtre dans `fetch_security.py`.
- **Dijon et son fichier de quartiers** : le fichier qu'on a utilisé n'a
  qu'un champ `CODCOMM` (pas un code INSEE standard), donc
  `load_quartiers()` dans `run_pipeline.py` a un cas particulier à adapter
  si jamais tu regénères ce fichier de zéro pour une autre commune multi-
  quartiers.

## Prochaines étapes possibles

- Brancher l'API Géorisques (`georisques.gouv.fr/api-catalogue`) pour
  automatiser risques naturels comme le reste.
- Généraliser le template pour un nombre de villes illustré par une liste
  plutôt que deux blocs nommés en dur (`bordeaux`/`dijon`).
- Ajouter un test de non-régression automatique (comme ceux que j'ai faits
  à la main avec Playwright dans la conversation) au job GitHub Actions,
  pour bloquer le commit si la carte générée a une erreur JS.
