"""
Recherche et téléchargement automatique d'un GTFS via le catalogue national.

Source : transport.data.gouv.fr, qui référence le GTFS à jour de chaque
réseau de transport français (mis à jour par les opérateurs eux-mêmes —
on n'a donc jamais un fichier périmé si on repasse par cette API).

Doc : https://transport.data.gouv.fr/api-docs
"""
from __future__ import annotations
import io
import zipfile
import requests

CATALOG_URL = "https://transport.data.gouv.fr/api/datasets"


def search_gtfs_dataset(search_term: str) -> dict:
    """Cherche un jeu de données GTFS par nom d'opérateur/réseau dans le
    catalogue. Renvoie le premier résultat correspondant ; si plusieurs
    réseaux portent un nom proche, affine `search_term` (ex: "Divia" plutôt
    que "Dijon", pour éviter de tomber sur un autre opérateur de la ville)."""
    r = requests.get(CATALOG_URL, timeout=30)
    r.raise_for_status()
    datasets = r.json()

    term = search_term.lower()
    matches = [
        d for d in datasets
        if d.get("type") == "public-transit"
        and term in d.get("title", "").lower()
    ]
    if not matches:
        raise ValueError(
            f"Aucun jeu de données GTFS trouvé pour '{search_term}'. "
            f"Cherche manuellement sur https://transport.data.gouv.fr/datasets "
            f"pour trouver le nom exact du réseau."
        )
    return matches[0]


def gtfs_download_url(dataset: dict) -> str:
    """Extrait l'URL de téléchargement du GTFS le plus récent d'un jeu de
    données du catalogue (le format change parfois selon l'opérateur, donc
    on prend la première ressource de format 'GTFS')."""
    for resource in dataset.get("resources", []):
        if resource.get("format", "").upper() == "GTFS":
            return resource["url"]
    raise ValueError(f"Pas de ressource GTFS trouvée dans le jeu de données '{dataset.get('title')}'.")


def download_and_extract_gtfs(search_term: str, dest_dir: str) -> str:
    """Cherche, télécharge et décompresse le GTFS le plus récent. Renvoie le
    chemin du dossier contenant les .txt (stops.txt, routes.txt, etc.)."""
    dataset = search_gtfs_dataset(search_term)
    url = gtfs_download_url(dataset)
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        zf.extractall(dest_dir)
    return dest_dir


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("Usage: python fetch_gtfs.py 'DiviaMobilites' ./gtfs_dijon")
        sys.exit(1)
    path = download_and_extract_gtfs(sys.argv[1], sys.argv[2])
    print("GTFS extrait dans", path)
