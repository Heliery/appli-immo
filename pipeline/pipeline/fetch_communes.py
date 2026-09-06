"""
Récupération des contours de communes.

Source : geo.api.gouv.fr (Etalab / DINUM), aucune clé requise.
Doc officielle : https://geo.api.gouv.fr/decoupage-administratif/communes
"""
from __future__ import annotations
import requests

GEO_API_BASE = "https://geo.api.gouv.fr"


def fetch_commune_boundary(insee_code: str) -> dict:
    """Retourne un Feature GeoJSON (properties + geometry) pour une commune.

    Exemple d'appel réel :
      GET https://geo.api.gouv.fr/communes/21442?fields=nom,code,contour&format=geojson&geometry=contour
    """
    url = f"{GEO_API_BASE}/communes/{insee_code}"
    params = {"fields": "nom,code,contour", "format": "geojson", "geometry": "contour"}
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    feature = r.json()
    # L'API renvoie directement un Feature (pas une FeatureCollection) pour /communes/{code}
    return {
        "type": "Feature",
        "properties": {"nom": feature["properties"]["nom"], "insee": feature["properties"]["code"]},
        "geometry": feature["geometry"],
    }


def fetch_communes_boundaries(insee_codes: list[str]) -> dict:
    """FeatureCollection pour une liste de codes INSEE (une requête par commune —
    l'API ne propose pas de filtre "liste de codes" pour /communes, donc on boucle;
    c'est rapide, une centaine de communes se récupère en quelques secondes)."""
    features = [fetch_commune_boundary(code) for code in insee_codes]
    return {"type": "FeatureCollection", "features": features}


def communes_from_epci(siren_epci: str) -> list[str]:
    """Récupère automatiquement la liste des codes INSEE d'un EPCI (métropole,
    communauté de communes...) à partir de son code SIREN. Pratique pour ne pas
    avoir à recopier une liste de communes à la main.

    Trouver le SIREN d'un EPCI : https://www.banatic.interieur.gouv.fr
    (ex: Bordeaux Métropole = 243300316, Dijon Métropole = 200069865)
    """
    url = f"{GEO_API_BASE}/epcis/{siren_epci}/communes"
    params = {"fields": "nom,code"}
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return [c["code"] for c in r.json()]


if __name__ == "__main__":
    import sys, json
    if len(sys.argv) < 2:
        print("Usage: python fetch_communes.py 21442 21231 33063 ...")
        sys.exit(1)
    fc = fetch_communes_boundaries(sys.argv[1:])
    print(json.dumps(fc, ensure_ascii=False)[:500], "...")
    print(f"\n{len(fc['features'])} commune(s) récupérée(s).")
