"""
Nettoyage des ventes DVF brutes et calcul des statistiques par zone.

Reprend exactement la méthode mise au point à la main pour Bordeaux et Dijon :
- une "vente simple" = une mutation avec EXACTEMENT un local Maison ou
  Appartement (élimine les immeubles / lots multiples qui faussent le prix/m²)
- filtre sur nature_mutation == "Vente", surface >= 9 m², prix/m² dans
  [500, 15000] pour écarter les erreurs de saisie évidentes
- affectation à une zone (quartier officiel si dispo, sinon commune entière)
  par test point-in-polygon, avec repli sur le centroïde le plus proche
- prix médian par filtre (type de bien x nombre de pièces), dynamisme du
  marché (ventes/an/km²), évolution année sur année si assez de volume
"""
from __future__ import annotations
import statistics
from collections import defaultdict
from typing import Optional

from shapely.geometry import shape, Point, mapping
from shapely.ops import transform
import pyproj

MIN_PRIX_M2 = 500
MAX_PRIX_M2 = 15000
MIN_SURFACE = 9
MIN_SAMPLES_EVOL = 6
N_YEARS_DEFAULT = 2

_project_to_l93 = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True).transform


def clean_mutations(raw_rows_by_insee: dict[str, list[dict]], years: list[str]) -> list[dict]:
    """Regroupe par id_mutation et ne garde que les ventes simples d'un bien
    principal (Maison ou Appartement), dans la fenêtre d'années demandée."""
    clean = []
    for insee, rows in raw_rows_by_insee.items():
        mutations = defaultdict(list)
        for r in rows:
            mutations[r["id_mutation"]].append(r)

        for mid, mrows in mutations.items():
            dwelling_rows = [r for r in mrows if r.get("type_local") in ("Maison", "Appartement")]
            if len(dwelling_rows) != 1:
                continue
            r = dwelling_rows[0]
            if r.get("nature_mutation") != "Vente":
                continue
            try:
                valeur = float(r["valeur_fonciere"])
                surface = float(r["surface_reelle_bati"])
                lon = float(r["longitude"])
                lat = float(r["latitude"])
            except (ValueError, TypeError, KeyError):
                continue
            if surface < MIN_SURFACE or valeur <= 0:
                continue
            prix_m2 = valeur / surface
            if prix_m2 < MIN_PRIX_M2 or prix_m2 > MAX_PRIX_M2:
                continue
            year = r["date_mutation"][:4]
            if year not in years:
                continue
            try:
                pieces = int(float(r["nombre_pieces_principales"])) if r.get("nombre_pieces_principales") else None
            except (ValueError, TypeError):
                pieces = None
            clean.append({
                "type_local": r["type_local"], "prix_m2": prix_m2, "lon": lon, "lat": lat,
                "year": year, "pieces": pieces,
                "commune": r.get("nom_commune", ""), "insee": r.get("code_commune", insee),
            })
    return clean


def build_zones(
    insee_codes: list[str],
    commune_names: dict[str, str],
    commune_geoms: dict[str, dict],
    quartiers_geoms: Optional[dict[str, list[dict]]] = None,
) -> dict[str, dict]:
    """Construit le dict des zones (quartier ou commune entière) avec leur
    géométrie shapely et leur surface en km².

    quartiers_geoms, si fourni : {insee: [ {"name":..., "geometry": geojson}, ... ]}
    pour les communes qui ont un découpage infra-communal.
    """
    quartiers_geoms = quartiers_geoms or {}
    zones: dict[str, dict] = {}

    for insee in insee_codes:
        name = commune_names.get(insee, insee)
        if insee in quartiers_geoms and quartiers_geoms[insee]:
            for q in quartiers_geoms[insee]:
                zid = f"Q-{insee}-{q['name']}"
                zones[zid] = {
                    "name": q["name"], "commune": name, "insee": insee,
                    "geom": shape(q["geometry"]), "kind": "quartier",
                }
        else:
            geom = commune_geoms.get(insee)
            if geom is None:
                continue
            zid = f"C-{insee}"
            zones[zid] = {
                "name": name, "commune": name, "insee": insee,
                "geom": shape(geom), "kind": "commune",
            }

    for zid, z in zones.items():
        area_m2 = transform(_project_to_l93, z["geom"]).area
        z["area_km2"] = area_m2 / 1_000_000

    return zones


def assign_zone(mutation: dict, zones: dict[str, dict]) -> Optional[str]:
    """Trouve la zone d'une mutation par point-in-polygon, avec repli sur le
    centroïde le plus proche parmi les zones de la même commune."""
    candidates = [zid for zid, z in zones.items() if z["insee"] == mutation["insee"]]
    if not candidates:
        return None
    pt = Point(mutation["lon"], mutation["lat"])
    for zid in candidates:
        if zones[zid]["geom"].contains(pt) or zones[zid]["geom"].intersects(pt):
            return zid
    return min(candidates, key=lambda zid: zones[zid]["geom"].centroid.distance(pt))


def _pieces_bucket(p):
    if p is None or p <= 0:
        return None
    return "4p" if p >= 4 else f"{p}p"


def compute_zone_stats(
    clean: list[dict],
    zones: dict[str, dict],
    min_samples: int = 3,
    min_samples_to_display: int = 1,
) -> dict:
    """Calcule les statistiques (prix par filtre, dynamisme, évolution) pour
    chaque zone. Une zone avec moins de `min_samples` ventes reçoit le
    drapeau `low_sample: true` plutôt que d'être masquée, à condition
    d'avoir au moins `min_samples_to_display` vente (sinon elle n'apparaît
    pas du tout, faute de la moindre donnée)."""
    for m in clean:
        m["zone_id"] = assign_zone(m, zones)

    by_zone = defaultdict(list)
    for m in clean:
        if m["zone_id"]:
            by_zone[m["zone_id"]].append(m)

    def median(rows):
        return statistics.median(r["prix_m2"] for r in rows) if rows else None

    stats = {}
    for zid, z in zones.items():
        rows = by_zone.get(zid, [])
        if len(rows) < min_samples_to_display:
            continue

        appart = [r for r in rows if r["type_local"] == "Appartement"]
        maison = [r for r in rows if r["type_local"] == "Maison"]
        r2024_style = defaultdict(list)
        for r in rows:
            r2024_style[r["year"]].append(r)
        years_sorted = sorted(r2024_style)
        evolution = None
        if len(years_sorted) >= 2:
            y0, y1 = years_sorted[0], years_sorted[-1]
            if len(r2024_style[y0]) >= MIN_SAMPLES_EVOL and len(r2024_style[y1]) >= MIN_SAMPLES_EVOL:
                p0, p1 = median(r2024_style[y0]), median(r2024_style[y1])
                if p0:
                    evolution = round((p1 - p0) / p0 * 100, 1)

        def subset(type_filter, pieces_filter):
            s = rows
            if type_filter != "tous":
                want = "Appartement" if type_filter == "appartement" else "Maison"
                s = [r for r in s if r["type_local"] == want]
            if pieces_filter != "tous":
                s = [r for r in s if _pieces_bucket(r["pieces"]) == pieces_filter]
            return s

        prix_par_filtre = {}
        for type_filter in ("tous", "appartement", "maison"):
            for pieces_filter in ("tous", "1p", "2p", "3p", "4p"):
                s = subset(type_filter, pieces_filter)
                key = f"{type_filter}|{pieces_filter}"
                prix_par_filtre[key] = (
                    {"prix": round(median(s)), "n": len(s)} if len(s) >= min_samples
                    else {"prix": None, "n": len(s)}
                )

        area = z["area_km2"]
        dynamisme = round((len(rows) / N_YEARS_DEFAULT) / area, 1) if area > 0 else None

        stats[zid] = {
            "name": z["name"], "commune": z["commune"], "insee": z["insee"], "kind": z["kind"],
            "n": len(rows), "low_sample": len(rows) < min_samples,
            "prix_tous": round(median(rows)) if rows else None,
            "prix_appartement": round(median(appart)) if len(appart) >= min_samples else None,
            "n_appartement": len(appart),
            "prix_maison": round(median(maison)) if len(maison) >= min_samples else None,
            "n_maison": len(maison),
            "evolution": evolution,
            "prix_par_filtre": prix_par_filtre,
            "area_km2": round(area, 3),
            "dynamisme": dynamisme,
            "geometry": mapping(z["geom"].simplify(0.0001, preserve_topology=True)),
        }

    return stats
