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
    récente du jeu de données SSMSI.

    Les noms de fichiers ne contiennent PAS le mot "communale" (ce sont des
    noms horodatés du type `donnee-data_gouv-2026-...csv.gz`), donc on ne
    peut pas filtrer sur le titre. En revanche, sur les 3 bases publiées
    (communale / départementale / régionale), seule la base communale est
    assez volumineuse (~35 000 communes) pour être distribuée en `.csv.gz` —
    les bases départementale et régionale, bien plus petites, sont
    distribuées en `.csv` brut. C'est ce critère de format qu'on utilise.
    """
    r = requests.get(DATAGOUV_API, timeout=30)
    r.raise_for_status()
    resources = r.json()["resources"]

    gz_candidates = [res for res in resources if res["format"] == "csv.gz"]
    if gz_candidates:
        gz_candidates.sort(key=lambda r: r.get("last_modified", ""), reverse=True)
        return gz_candidates[0]["url"]

    # Repli : si le format a changé (plus de gzip), on prend le plus gros des
    # fichiers csv, en supposant que c'est toujours la base communale qui
    # domine largement les deux autres en volume.
    csv_candidates = [res for res in resources if res["format"] == "csv"]
    if not csv_candidates:
        raise ValueError(
            "Aucune ressource csv/csv.gz trouvée — le format a peut-être "
            f"changé, vérifie manuellement sur https://www.data.gouv.fr/fr/datasets/{DATASET_ID}/"
        )
    csv_candidates.sort(key=lambda r: r.get("filesize") or 0, reverse=True)
    return csv_candidates[0]["url"]


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
    is_gzip = content[:2] == b"\x1f\x8b"  # magic bytes gzip, plus fiable que l'URL
    text_stream = (
        io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(content)), encoding="utf-8")
        if is_gzip else io.StringIO(content.decode("utf-8"))
    )

    totals: dict[str, dict] = {}

    def to_float(s):
        return float(s.replace(",", ".")) if s not in ("NA", "", None) else None

    reader = csv.DictReader(text_stream, delimiter=";")
    codgeo_field = next((f for f in (reader.fieldnames or []) if f.startswith("CODGEO")), None)
    if codgeo_field is None:
        raise ValueError(
            f"Colonne CODGEO_xxxx introuvable dans l'en-tête ({reader.fieldnames}) — "
            "le format du fichier SSMSI a peut-être changé, vérifie à la main."
        )
    for row in reader:
        code = row.get(codgeo_field)
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
