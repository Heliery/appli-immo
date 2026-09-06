"""
Téléchargement des ventes DVF (Demandes de Valeurs Foncières).

⚠️ Historique : la première version de ce module devinait des URLs de type
`files.data.gouv.fr/geo-dvf/.../communes/{dept}/{insee}.csv.gz`, qui se sont
révélées ne plus être le bon chemin (404 systématique en production). Cette
version interroge à la place le fichier Parquet national officiel via DuckDB
avec un simple SELECT, colonne par colonne identiques aux champs DVF
habituels (id_mutation, valeur_fonciere, code_commune, nombre_pieces_...) —
donc rien d'autre à changer dans le reste du pipeline.

DuckDB sait lire un Parquet distant par HTTP Range-Requests : il ne
télécharge que les morceaux du fichier réellement nécessaires à la requête,
pas le fichier entier (plusieurs Go pour la France entière).
"""
from __future__ import annotations
import logging
from collections import defaultdict

import duckdb

logger = logging.getLogger(__name__)

PARQUET_URL = "https://object.data.gouv.fr/dataeng-open/dvf.parquet"

COLUMNS = [
    "id_mutation", "date_mutation", "nature_mutation", "valeur_fonciere",
    "code_commune", "nom_commune", "type_local", "surface_reelle_bati",
    "nombre_pieces_principales", "longitude", "latitude",
]


def _connection():
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    return con


def download_all(insee_codes: list[str], years: list[str]) -> dict[str, list[dict]]:
    """Télécharge toutes les ventes des communes/années demandées en UNE
    requête SQL, et les regroupe par commune : {insee: [lignes...]}.

    Chaque colonne est explicitement castée en VARCHAR pour renvoyer des
    lignes dont les valeurs sont des chaînes de caractères, exactement comme
    le faisait l'ancien export CSV — process_dvf.clean_mutations() n'a donc
    besoin d'aucune adaptation."""
    con = _connection()
    codes_sql = ", ".join(f"'{c}'" for c in insee_codes)
    years_sql = ", ".join(str(int(y)) for y in years)
    select_cols = ", ".join(f"CAST({c} AS VARCHAR) AS {c}" for c in COLUMNS)

    query = f"""
        SELECT {select_cols}
        FROM read_parquet('{PARQUET_URL}')
        WHERE code_commune IN ({codes_sql})
          AND EXTRACT(year FROM date_mutation) IN ({years_sql})
    """
    logger.info("Requête DuckDB sur le Parquet national DVF (%d communes, années %s)...",
                len(insee_codes), years)
    rows = con.execute(query).fetchdf().to_dict("records")
    logger.info("%d lignes DVF récupérées au total.", len(rows))

    out: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        out[row["code_commune"]].append(row)

    for insee in insee_codes:
        if insee not in out:
            logger.warning("Aucune vente DVF trouvée pour %s sur %s (normal si aucune vente).",
                            insee, years)
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
        print(insee, "->", len(rows), "lignes")
