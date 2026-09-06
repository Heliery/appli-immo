"""
Téléchargement des ventes DVF (Demandes de Valeurs Foncières).

Source : "geo-dvf" (Etalab / Christian Quest), qui republie chaque année les
données DGFiP au format CSV géolocalisé, y compris pré-découpées PAR COMMUNE.
C'est ce qui alimente app.dvf.etalab.gouv.fr — on va directement à la source
plutôt que de cliquer sur le site.

Page d'index (pour vérifier que les URLs n'ont pas changé) :
  https://files.data.gouv.fr/geo-dvf/latest/csv/

Pattern d'URL stable, un fichier par commune et par année :
  https://files.data.gouv.fr/geo-dvf/latest/csv/{annee}/communes/{dept}/{insee}.csv.gz

où {dept} est le code département sur 2 ou 3 caractères (ex: "21", "33", "971").

⚠️ Avant une première utilisation en production, vérifie une fois à la main
qu'une URL répond bien (ex: colle-la dans un navigateur) : ces projets
communautaires changent rarement de structure mais ça arrive.
"""
from __future__ import annotations
import csv
import gzip
import io
import logging
from collections import defaultdict

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://files.data.gouv.fr/geo-dvf/latest/csv"


def _dept_from_insee(insee: str) -> str:
    """2A/2B pour la Corse, sinon les 2 premiers chiffres (3 pour les DROM)."""
    if insee.startswith("97"):
        return insee[:3]
    if insee.startswith("2A") or insee.startswith("2B"):
        return insee[:2]
    return insee[:2]


def download_commune_year(insee: str, year: str) -> list[dict]:
    """Télécharge et parse le CSV DVF d'une commune pour une année donnée.
    Retourne la liste des lignes brutes (dict), une ligne par lot/local comme
    dans les exports habituels de app.dvf.etalab.gouv.fr (id_mutation à
    regrouper ensuite, voir pipeline/process_dvf.py)."""
    dept = _dept_from_insee(insee)
    url = f"{BASE_URL}/{year}/communes/{dept}/{insee}.csv.gz"
    r = requests.get(url, timeout=60)
    if r.status_code == 404:
        logger.warning("Pas de données DVF pour %s en %s (404, normal si aucune vente)", insee, year)
        return []
    r.raise_for_status()
    with gzip.open(io.BytesIO(r.content), mode="rt", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def download_all(insee_codes: list[str], years: list[str]) -> dict[str, list[dict]]:
    """Télécharge tout, regroupé par commune. Renvoie {insee: [lignes...]}."""
    out: dict[str, list[dict]] = defaultdict(list)
    total = len(insee_codes) * len(years)
    done = 0
    for insee in insee_codes:
        for year in years:
            rows = download_commune_year(insee, year)
            out[insee].extend(rows)
            done += 1
            logger.info("[%d/%d] %s %s : %d lignes", done, total, insee, year, len(rows))
    return dict(out)


if __name__ == "__main__":
    import sys, json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(sys.argv) < 3:
        print("Usage: python fetch_dvf.py 2024,2025 21442 33063 ...")
        sys.exit(1)
    years = sys.argv[1].split(",")
    codes = sys.argv[2:]
    data = download_all(codes, years)
    for insee, rows in data.items():
        print(insee, "->", len(rows), "lignes brutes")
