#!/usr/bin/env python3
"""
Pipeline complet : à partir d'un fichier de config (config/<ville>.yaml),
télécharge et traite tout ce qu'il faut pour produire `final_data_<ville>.json`.

Usage :
    python run_pipeline.py config/dijon.yaml
    python run_pipeline.py config/bordeaux.yaml --skip-transit   # + rapide en test

Étapes :
    1. Contours des communes (geo.api.gouv.fr)
    2. Ventes DVF (files.data.gouv.fr/geo-dvf)
    3. Statistiques de prix par zone (quartier ou commune)
    4. GTFS (transport.data.gouv.fr) + lignes/arrêts/fréquence/temps de trajet
    5. Sécurité SSMSI (data.gouv.fr)
    6. Assemblage final -> output/final_data_<ville>.json
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(__file__))
from pipeline import fetch_communes, fetch_dvf, fetch_gtfs, fetch_security
from pipeline import process_dvf, process_transit, build_final_data

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("pipeline")


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_quartiers(cfg: dict) -> dict[str, list[dict]]:
    """Charge un fichier de quartiers officiels s'il est renseigné dans la
    config. Format attendu : GeoJSON avec un champ nom + un champ code INSEE
    (ou un mapping manuel si le fichier ne les a pas directement, comme pour
    Dijon où le champ CODCOMM n'est pas un code INSEE standard — voir
    `_fixup_dijon_codcomm` à adapter/retirer selon ton fichier réel)."""
    path = cfg.get("quartiers_geojson")
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        geojson = json.load(f)

    name_field = cfg["quartiers_name_field"]
    insee_field = cfg.get("quartiers_insee_field")

    out: dict[str, list[dict]] = {}
    for feat in geojson["features"]:
        props = feat["properties"]
        if insee_field:
            insee = props[insee_field]
        else:
            # Cas Dijon : un seul champ CODCOMM (dept + numéro commune sur 4
            # chiffres) pour toutes les features -> à mapper manuellement à un
            # insee de ta config si ton fichier n'a qu'une seule commune, ou
            # écrire ta propre conversion sinon.
            insee = cfg["insee_codes"][0]
        out.setdefault(insee, []).append({"name": props[name_field], "geometry": feat["geometry"]})
    return out


def run(cfg: dict, skip_transit: bool = False, skip_security: bool = False) -> dict:
    city = cfg["city_key"]
    codes = cfg["insee_codes"]

    log.info("=== %s : contours des communes ===", city)
    communes_fc = fetch_communes.fetch_communes_boundaries(codes)
    commune_names = {f["properties"]["insee"]: f["properties"]["nom"] for f in communes_fc["features"]}
    commune_geoms = {f["properties"]["insee"]: f["geometry"] for f in communes_fc["features"]}

    log.info("=== %s : téléchargement DVF ===", city)
    raw_rows = fetch_dvf.download_all(codes, cfg["years"])

    log.info("=== %s : nettoyage des mutations ===", city)
    clean = process_dvf.clean_mutations(raw_rows, cfg["years"])
    log.info("%d ventes exploitables", len(clean))

    quartiers = load_quartiers(cfg)
    zones = process_dvf.build_zones(codes, commune_names, commune_geoms, quartiers)
    log.info("%d zones construites", len(zones))

    zone_stats = process_dvf.compute_zone_stats(
        clean, zones,
        min_samples=cfg.get("low_sample_threshold", 3),
        min_samples_to_display=cfg.get("min_sample_to_display", 3),
    )
    log.info("%d zones avec au moins une vente affichable", len(zone_stats))

    transit_lines, stops, global_travel_times = [], [], []
    if not skip_transit:
        log.info("=== %s : GTFS ===", city)
        gtfs_dir = os.path.join("output", city, "gtfs")
        os.makedirs(gtfs_dir, exist_ok=True)
        fetch_gtfs.download_and_extract_gtfs(cfg["gtfs_search_term"], gtfs_dir)

        ref_date = process_transit.pick_reference_date(gtfs_dir)
        log.info("date de référence choisie : %s", ref_date)

        headways = process_transit.compute_bus_frequency(gtfs_dir, ref_date)
        max_headway = cfg.get("max_bus_headway_minutes", 20)
        frequent_bus_ids = {rid for rid, h in headways.items() if h is not None and h <= max_headway}
        log.info("%d/%d lignes de bus retenues (<= %d min)", len(frequent_bus_ids),
                  sum(1 for h in headways), max_headway)

        transit_lines = process_transit.extract_lines(gtfs_dir, frequent_bus_ids)
        stops = process_transit.extract_stops(gtfs_dir, frequent_bus_ids)
        global_travel_times = process_transit.compute_travel_time_to_centre(
            gtfs_dir, ref_date, cfg["centre_stop_names"], cfg.get("arrival_deadline", "08:45:00"),
        )
        build_final_data.merge_transport_into_zones(zone_stats, zones, stops, global_travel_times)
    else:
        for z in zone_stats.values():
            z["transport_score"], z["n_stops"], z["temps_centre_min"] = 0.0, 0, None

    if not skip_security:
        log.info("=== %s : sécurité SSMSI ===", city)
        security = fetch_security.download_security_data(set(codes))
        process_dvf.merge_security_into_zones = build_final_data.merge_security_into_zones
        build_final_data.merge_security_into_zones(zone_stats, security)
    else:
        for z in zone_stats.values():
            z["securite_taux"], z["securite_pop_commune"] = None, None

    import statistics
    all_prices = [m["prix_m2"] for m in clean if m.get("zone_id") in zone_stats]
    global_median = round(statistics.median(all_prices)) if all_prices else None

    final = build_final_data.build_final_data(zone_stats, transit_lines, stops, global_median)

    os.makedirs("output", exist_ok=True)
    out_path = f"output/final_data_{city}.json"
    build_final_data.save(final, out_path)
    log.info("=== %s : écrit dans %s ===", city, out_path)
    return final


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", help="chemin vers config/<ville>.yaml")
    parser.add_argument("--skip-transit", action="store_true", help="ignore GTFS (itération rapide)")
    parser.add_argument("--skip-security", action="store_true", help="ignore SSMSI (itération rapide)")
    args = parser.parse_args()

    cfg = load_config(args.config)
    run(cfg, skip_transit=args.skip_transit, skip_security=args.skip_security)


if __name__ == "__main__":
    main()
