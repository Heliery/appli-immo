#!/usr/bin/env python3
"""
Injecte les `final_data_<ville>.json` produits par run_pipeline.py dans le
template HTML de la carte.

Usage :
    python inject_into_html.py template.html output/final_data_bordeaux.json output/final_data_dijon.json -o carte.html

Le template attendu contient les jetons __DATA_JSON_BORDEAUX__ et
__DATA_JSON_DIJON__ (voir la variable TOKENS ci-dessous — à étendre si tu
ajoutes une troisième ville, voir README §Ajouter une ville).
"""
from __future__ import annotations
import argparse
import json
import re

TOKENS = {
    "bordeaux": "__DATA_JSON_BORDEAUX__",
    "dijon": "__DATA_JSON_DIJON__",
}


def inject(template_path: str, data_paths: dict[str, str], out_path: str) -> None:
    with open(template_path, encoding="utf-8") as f:
        html = f.read()

    for city, path in data_paths.items():
        token = TOKENS.get(city)
        if not token:
            raise ValueError(
                f"Pas de jeton connu pour '{city}'. Si c'est une nouvelle ville, "
                f"suis les 3 étapes du README (§Ajouter une ville) avant de "
                f"pouvoir l'injecter automatiquement ici."
            )
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if token not in html:
            raise ValueError(f"Jeton {token} introuvable dans le template — a-t-il déjà été injecté ?")
        html = html.replace(token, json.dumps(data, ensure_ascii=False))

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Écrit : {out_path} ({len(html):,} caractères)".replace(",", " "))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("template", help="template.html (avec les jetons non injectés)")
    parser.add_argument("data_files", nargs="+", help="output/final_data_<ville>.json ...")
    parser.add_argument("-o", "--output", default="carte.html")
    args = parser.parse_args()

    data_paths = {}
    for path in args.data_files:
        m = re.search(r"final_data_(\w+)\.json$", path)
        if not m:
            raise ValueError(f"Nom de fichier inattendu (attendu final_data_<ville>.json) : {path}")
        data_paths[m.group(1)] = path

    inject(args.template, data_paths, args.output)


if __name__ == "__main__":
    main()
