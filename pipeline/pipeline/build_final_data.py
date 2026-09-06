"""
Assemble les sorties des autres modules en un seul `final_data_<city>.json`,
au format attendu par le template HTML de la carte (voir build_html.inject).
"""
from __future__ import annotations
import json
from shapely.geometry import shape


def merge_transport_into_zones(zone_stats: dict, zones_shapely: dict,
                                stops: list[dict], travel_times: list[dict]) -> None:
    """Ajoute transport_score, n_stops, temps_centre_min à chaque zone,
    en modifiant zone_stats en place."""
    from pipeline.process_transit import transport_score, nearest_travel_time

    for zid, z in zone_stats.items():
        geom = zones_shapely[zid]["geom"]
        score, n_stops = transport_score(geom, stops)
        z["transport_score"] = score
        z["n_stops"] = n_stops
        centroid = geom.centroid
        z["temps_centre_min"] = nearest_travel_time((centroid.x, centroid.y), travel_times)


def merge_security_into_zones(zone_stats: dict, security_by_insee: dict) -> None:
    """Ajoute securite_taux à chaque zone en cherchant par code INSEE — plus
    fiable qu'un rapprochement par nom de commune (accents, apostrophes...)."""
    for z in zone_stats.values():
        sec = security_by_insee.get(z["insee"])
        z["securite_taux"] = sec["taux_pour_mille"] if sec else None
        z["securite_pop_commune"] = sec["pop"] if sec else None


def build_final_data(zone_stats: dict, transit_lines: list[dict], stops: list[dict],
                      global_median: float) -> dict:
    features = []
    for zid, z in zone_stats.items():
        props = {k: v for k, v in z.items() if k != "geometry"}
        props["id"] = zid
        features.append({"type": "Feature", "properties": props, "geometry": z["geometry"]})
    return {
        "zones": {"type": "FeatureCollection", "features": features},
        "global_median": global_median,
        "transit": transit_lines,
        "stops": stops,
    }


def save(final_data: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False)
