"""
Traitement d'un GTFS déjà téléchargé (voir fetch_gtfs.py) en données prêtes
pour la carte : lignes simplifiées, arrêts, score de desserte par zone, temps
de trajet vers le centre-ville, et filtrage des bus peu fréquents.

Toute la méthode reprend ce qui a été mis au point à la main pour Bordeaux et
Dijon (voir la conversation d'origine) : Connection Scan Algorithm pour le
temps de trajet, comptage de passages en heures creuses/pleines pour la
fréquence, simplification Douglas-Peucker pour les tracés.
"""
from __future__ import annotations
import csv
from collections import defaultdict, Counter
from datetime import date

KIND_MAP = {"0": "tram", "3": "bus", "4": "boat"}
DAY_COLS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _to_seconds(hms: str) -> int:
    h, m, s = hms.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def _active_services(gtfs_dir: str, ref_date: str) -> set[str]:
    """Résout les service_id actifs à une date donnée, en croisant
    calendar.txt (motif hebdomadaire) et calendar_dates.txt (exceptions)."""
    ref_dt = date(int(ref_date[:4]), int(ref_date[4:6]), int(ref_date[6:8]))
    daycol = DAY_COLS[ref_dt.weekday()]

    active = set()
    with open(f"{gtfs_dir}/calendar.txt", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r[daycol] == "1" and r["start_date"] <= ref_date <= r["end_date"]:
                active.add(r["service_id"])

    with open(f"{gtfs_dir}/calendar_dates.txt", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["date"] != ref_date:
                continue
            if r["exception_type"] == "1":
                active.add(r["service_id"])
            elif r["exception_type"] == "2":
                active.discard(r["service_id"])
    return active


def pick_reference_date(gtfs_dir: str) -> str:
    """Choisit une date de référence représentative : un mercredi au milieu
    de la période réellement couverte par le service.

    Beaucoup de GTFS n'utilisent qu'une entrée générique dans calendar.txt
    (valide toute l'année) et placent le vrai détail jour par jour dans
    calendar_dates.txt (souvent sur une fenêtre de quelques mois glissants
    seulement). On regarde donc calendar_dates.txt en priorité pour ne pas
    tomber sur une date où aucun service concret n'est actif."""
    from datetime import timedelta
    import os

    def to_date(s):
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))

    cd_path = f"{gtfs_dir}/calendar_dates.txt"
    dates_seen = []
    if os.path.exists(cd_path):
        with open(cd_path, encoding="utf-8-sig") as f:
            dates_seen = sorted(to_date(r["date"]) for r in csv.DictReader(f))

    if dates_seen:
        mid = dates_seen[len(dates_seen) // 2]
    else:
        with open(f"{gtfs_dir}/calendar.txt", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        start = min(r["start_date"] for r in rows)
        mid = to_date(start) + timedelta(days=14)

    while mid.weekday() != 2:  # 2 = mercredi
        mid += timedelta(days=1)
    return mid.strftime("%Y%m%d")


def _rdp(points, epsilon):
    """Douglas-Peucker : simplifie une polyligne en gardant sa forme générale."""
    if len(points) < 3:
        return points

    def perp_dist(pt, a, b):
        (x, y), (ax, ay), (bx, by) = pt, a, b
        if (ax, ay) == (bx, by):
            return ((x - ax) ** 2 + (y - ay) ** 2) ** 0.5
        num = abs((by - ay) * x - (bx - ax) * y + bx * ay - by * ax)
        den = ((by - ay) ** 2 + (bx - ax) ** 2) ** 0.5
        return num / den

    dmax, idx = 0.0, 0
    for i in range(1, len(points) - 1):
        d = perp_dist(points[i], points[0], points[-1])
        if d > dmax:
            dmax, idx = d, i
    if dmax > epsilon:
        left = _rdp(points[:idx + 1], epsilon)
        right = _rdp(points[idx:], epsilon)
        return left[:-1] + right
    return [points[0], points[-1]]


def compute_bus_frequency(gtfs_dir: str, ref_date: str,
                           window_start="07:00:00", window_end="19:00:00") -> dict[str, float]:
    """Fréquence moyenne (minutes entre deux passages) de chaque ligne de bus
    sur une plage horaire de référence. Renvoie {route_id: headway_minutes}."""
    win_start_s, win_end_s = _to_seconds(window_start), _to_seconds(window_end)
    window_minutes = (win_end_s - win_start_s) / 60
    active = _active_services(gtfs_dir, ref_date)

    with open(f"{gtfs_dir}/routes.txt", encoding="utf-8-sig") as f:
        routes = {r["route_id"]: r for r in csv.DictReader(f)}

    trip_route = {}
    with open(f"{gtfs_dir}/trips.txt", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["service_id"] in active:
                trip_route[r["trip_id"]] = r["route_id"]

    trips_seen = set()
    with open(f"{gtfs_dir}/stop_times.txt", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            tid = r["trip_id"]
            if tid not in trip_route or r["stop_sequence"] != "1":
                continue
            try:
                dep_s = _to_seconds(r["departure_time"])
            except ValueError:
                continue
            if win_start_s <= dep_s <= win_end_s:
                trips_seen.add(tid)

    trip_count = Counter(trip_route[t] for t in trips_seen)
    headways = {}
    for rid, r in routes.items():
        if r["route_type"] != "3":
            continue
        n = trip_count.get(rid, 0)
        headways[rid] = (window_minutes / n) if n > 0 else None
    return headways


def extract_lines(gtfs_dir: str, frequent_bus_ids: set[str] | None = None,
                   simplify_epsilon=0.00006) -> list[dict]:
    """Une entrée par ligne (tram/bus/navette), avec ses tracés simplifiés.
    Si `frequent_bus_ids` est fourni, les bus hors de cette liste sont exclus
    (voir compute_bus_frequency + max_bus_headway_minutes dans la config)."""
    with open(f"{gtfs_dir}/routes.txt", encoding="utf-8-sig") as f:
        routes = {r["route_id"]: r for r in csv.DictReader(f)}

    shape_count = defaultdict(Counter)
    with open(f"{gtfs_dir}/trips.txt", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["route_id"] not in routes or not r.get("shape_id"):
                continue
            key = (r["route_id"], r.get("direction_id", "0"))
            shape_count[key][r["shape_id"]] += 1

    needed_shapes = {}
    for key, counter in shape_count.items():
        top_shape, _ = counter.most_common(1)[0]
        needed_shapes[top_shape] = key

    shape_points = defaultdict(list)
    with open(f"{gtfs_dir}/shapes.txt", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["shape_id"] in needed_shapes:
                shape_points[r["shape_id"]].append(
                    (int(r["shape_pt_sequence"]), float(r["shape_pt_lat"]), float(r["shape_pt_lon"]))
                )
    for sid in shape_points:
        shape_points[sid].sort(key=lambda t: t[0])

    lines_by_route = defaultdict(list)
    for sid, pts in shape_points.items():
        rid, _ = needed_shapes[sid]
        coords = [(lat, lon) for _, lat, lon in pts]
        simplified = _rdp(coords, simplify_epsilon)
        lines_by_route[rid].append([[round(lat, 5), round(lon, 5)] for lat, lon in simplified])

    out = []
    for rid, r in routes.items():
        kind = KIND_MAP.get(r["route_type"], "other")
        if kind == "bus" and frequent_bus_ids is not None and rid not in frequent_bus_ids:
            continue
        if rid not in lines_by_route:
            continue
        out.append({
            "id": rid, "name": r["route_short_name"], "long_name": r["route_long_name"],
            "kind": kind, "color": "#" + (r["route_color"] or "888888"),
            "lines": lines_by_route[rid],
        })
    return out


def extract_stops(gtfs_dir: str, frequent_bus_ids: set[str] | None = None) -> list[dict]:
    """Une entrée par arrêt desservi, avec ses lignes. Un arrêt uniquement
    desservi par un bus peu fréquent (absent de frequent_bus_ids) n'est pas
    marqué comme "bus" pour rester cohérent avec les lignes affichées."""
    with open(f"{gtfs_dir}/routes.txt", encoding="utf-8-sig") as f:
        routes = {r["route_id"]: r for r in csv.DictReader(f)}
    trip_route = {}
    with open(f"{gtfs_dir}/trips.txt", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            trip_route[r["trip_id"]] = r["route_id"]

    stop_routes = defaultdict(set)
    with open(f"{gtfs_dir}/stop_times.txt", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rid = trip_route.get(r["trip_id"])
            if rid:
                stop_routes[r["stop_id"]].add(rid)

    with open(f"{gtfs_dir}/stops.txt", encoding="utf-8-sig") as f:
        stops = list(csv.DictReader(f))

    out = []
    for s in stops:
        if s.get("location_type") not in (None, "", "0"):
            continue
        rids = stop_routes.get(s["stop_id"])
        if not rids:
            continue
        route_objs = [routes[r] for r in rids if r in routes]
        has_tram = any(r["route_type"] == "0" for r in route_objs)
        has_boat = any(r["route_type"] == "4" for r in route_objs)
        has_bus = any(
            r["route_type"] == "3" and (frequent_bus_ids is None or rid in frequent_bus_ids)
            for rid, r in zip(rids, route_objs)
        )
        if not (has_tram or has_boat or has_bus):
            continue
        try:
            lat, lon = float(s["stop_lat"]), float(s["stop_lon"])
        except (ValueError, TypeError):
            continue
        lines = sorted({
            r["route_short_name"] for rid, r in zip(rids, route_objs)
            if r["route_type"] in ("0", "4") or (r["route_type"] == "3" and (frequent_bus_ids is None or rid in frequent_bus_ids))
        }, key=lambda x: (len(x), x))
        out.append({
            "name": s["stop_name"], "lat": round(lat, 6), "lon": round(lon, 6),
            "tram": has_tram, "bus": has_bus, "boat": has_boat, "lines": lines[:12],
        })
    return out


def compute_travel_time_to_centre(
    gtfs_dir: str, ref_date: str, centre_stop_names: list[str], arrival_deadline="08:45:00",
    window_start="06:00:00",
) -> list[dict]:
    """Temps de trajet minimal en TC pour arriver au centre-ville avant
    `arrival_deadline`, pour chaque arrêt, via un balayage de connexions en
    profil inverse (reverse Connection Scan). Renvoie
    [{"lat":.., "lon":.., "travel_time_min":..}, ...]."""
    deadline = _to_seconds(arrival_deadline)
    win_start_s = _to_seconds(window_start)
    active = _active_services(gtfs_dir, ref_date)

    trip_ids = set()
    with open(f"{gtfs_dir}/trips.txt", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["service_id"] in active:
                trip_ids.add(r["trip_id"])

    trip_stops = defaultdict(list)
    with open(f"{gtfs_dir}/stop_times.txt", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["trip_id"] not in trip_ids:
                continue
            try:
                arr_s, dep_s = _to_seconds(r["arrival_time"]), _to_seconds(r["departure_time"])
            except ValueError:
                continue
            if dep_s < win_start_s - 1800 or arr_s > deadline + 1800:
                continue
            trip_stops[r["trip_id"]].append((int(r["stop_sequence"]), r["stop_id"], arr_s, dep_s))

    connections = []
    for tid, stops in trip_stops.items():
        stops.sort(key=lambda x: x[0])
        for i in range(len(stops) - 1):
            _, s1, _, dep_s = stops[i]
            _, s2, arr_s, _ = stops[i + 1]
            if win_start_s <= dep_s <= arr_s:
                connections.append((s1, dep_s, s2, arr_s))

    with open(f"{gtfs_dir}/stops.txt", encoding="utf-8-sig") as f:
        all_stops = list(csv.DictReader(f))
    centre_ids = {s["stop_id"] for s in all_stops
                  if s["stop_name"] in centre_stop_names and s.get("location_type") in (None, "", "0")}

    NEG_INF = -1
    latest_departure = defaultdict(lambda: NEG_INF)
    for sid in centre_ids:
        latest_departure[sid] = deadline

    connections.sort(key=lambda c: c[3], reverse=True)
    for dep_stop, dep_s, arr_stop, arr_s in connections:
        if arr_s > deadline:
            continue
        if arr_s <= latest_departure[arr_stop] and dep_s > latest_departure[dep_stop]:
            latest_departure[dep_stop] = dep_s

    stop_coords = {s["stop_id"]: (float(s["stop_lat"]), float(s["stop_lon"]))
                   for s in all_stops if s.get("location_type") in (None, "", "0")}

    out = []
    for sid, ld in latest_departure.items():
        if ld > NEG_INF and sid in stop_coords:
            lat, lon = stop_coords[sid]
            out.append({"lat": lat, "lon": lon, "travel_time_min": round((deadline - ld) / 60, 1)})
    return out


def nearest_travel_time(zone_centroid_lonlat: tuple[float, float], travel_times: list[dict],
                         n_nearest=8) -> float | None:
    """Temps vers le centre pour une zone = le MEILLEUR temps parmi les
    quelques arrêts les plus proches de son centroïde (un résident choisit
    l'arrêt le mieux desservi à distance de marche comparable, pas
    n'importe lequel)."""
    import math
    lon0, lat0 = zone_centroid_lonlat
    kx = 111.32 * math.cos(math.radians(lat0))

    def d2(t):
        return ((t["lon"] - lon0) * kx) ** 2 + ((t["lat"] - lat0) * 111.32) ** 2

    nearest = sorted(travel_times, key=d2)[:n_nearest]
    return round(min(t["travel_time_min"] for t in nearest), 1) if nearest else None


def transport_score(zone_geom, stops: list[dict]) -> tuple[float, int]:
    """Score de desserte d'une zone = arrêts pondérés (nb de lignes + bonus
    tram) rapportés à la surface (arrêts pondérés / km²)."""
    from shapely.geometry import Point
    from shapely.ops import transform
    import pyproj
    project = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True).transform

    total, n_stops = 0.0, 0
    for s in stops:
        if zone_geom.contains(Point(s["lon"], s["lat"])):
            weight = min(len(s["lines"]), 4) + (5 if s["tram"] else 0)
            total += weight
            n_stops += 1
    area_km2 = transform(project, zone_geom).area / 1_000_000
    score = round(total / area_km2, 1) if area_km2 > 0 else 0.0
    return score, n_stops
