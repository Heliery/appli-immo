"""
Sécurité : base communale de la délinquance (SSMSI, Ministère de l'Intérieur).

Plutôt qu'une URL de fichier codée en dur (qui casse à chaque nouveau
millésime), on passe par l'API data.gouv.fr pour retrouver la ressource CSV
actuelle du jeu de données, à chaque exécution.

Jeu de données : https://www.data.gouv.fr/fr/datasets/621df2954fa5a3b5a023e23c/
"""
from __future__ import annotations
import csv
import gzip
import io
import requests

DATASET_ID = "621df2954fa5a3b5a023e23c"
DATAGOUV_API = f"https://www.data.gouv.fr/api/1/datasets/{DATASET_ID}/"

# Indicateurs retenus pour un score "sécurité du quartier" pertinent à un
# choix de logement. Les violences intrafamiliales sont volontairement
# exclues : elles ne reflètent pas un risque de rue/voisinage.
DEFAULT_INDICATORS = {
    "Cambriolages de logement", "Vols avec armes", "Vols violents sans arme",
    "Vols sans violence contre des personnes", "Vols de véhicule",
    "Vols dans les véhicules", "Vols d'accessoires sur véhicules",
    "Destructions et dégradations volontaires",
    "Violences physiques hors cadre familial", "Violences sexuelles",
}


def find_communal_csv_url() -> str:
    """Retrouve dynamiquement l'URL de la ressource "base communale" la plus
    récente du jeu de données SSMSI (le nom de fichier change à chaque
    publication, d'où l'appel API plutôt qu'une URL fixe)."""
    r = requests.get(DATAGOUV_API, timeout=30)
    r.raise_for_status()
    resources = r.json()["resources"]
    candidates = [
        res for res in resources
        if "communale" in res["title"].lower() and res["format"] in ("csv", "gz")
    ]
    if not candidates:
        raise ValueError(
            "Ressource 'base communale' introuvable — vérifie manuellement sur "
            f"https://www.data.gouv.fr/fr/datasets/{DATASET_ID}/"
        )
    # la plus récemment publiée en tête
    candidates.sort(key=lambda r: r.get("last_modified", ""), reverse=True)
    return candidates[0]["url"]


def download_security_data(insee_codes: set[str], year: str = "2025",
                            indicators: set[str] | None = None) -> dict[str, dict]:
    """Télécharge et filtre la base communale, calcule un taux pour 1000
    habitants par commune, seulement pour les indicateurs retenus. Renvoie
    {insee: {"taux_pour_mille": float, "pop": int, "n_indics": int}}."""
    indicators = indicators or DEFAULT_INDICATORS
    url = find_communal_csv_url()
    r = requests.get(url, timeout=120)
    r.raise_for_status()

    content = r.content
    text_stream = (
        io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(content)), encoding="utf-8")
        if url.endswith(".gz") else io.StringIO(content.decode("utf-8"))
    )

    totals: dict[str, dict] = {}

    def to_float(s):
        return float(s.replace(",", ".")) if s not in ("NA", "", None) else None

    reader = csv.DictReader(text_stream, delimiter=";")
    for row in reader:
        code = row.get("CODGEO_2026") or row.get("CODGEO")
        if code not in insee_codes or row.get("annee") != year or row.get("indicateur") not in indicators:
            continue
        nb = to_float(row.get("nombre")) or to_float(row.get("complement_info_nombre"))
        pop = to_float(row.get("insee_pop"))
        entry = totals.setdefault(code, {"total": 0.0, "pop": pop, "n_indics": 0})
        if nb is not None:
            entry["total"] += nb
            entry["n_indics"] += 1
        if pop is not None:
            entry["pop"] = pop

    out = {}
    for code, e in totals.items():
        if e["pop"]:
            out[code] = {
                "taux_pour_mille": round(e["total"] / e["pop"] * 1000, 2),
                "pop": int(e["pop"]),
                "n_indics": e["n_indics"],
            }
    return out


if __name__ == "__main__":
    import sys, json
    codes = set(sys.argv[1:]) or {"21442", "33063"}
    print("URL résolue :", find_communal_csv_url())
    data = download_security_data(codes)
    print(json.dumps(data, indent=1, ensure_ascii=False))
