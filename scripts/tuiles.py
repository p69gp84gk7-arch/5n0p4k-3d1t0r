"""Découpe la mosaïque d'une date en tuiles web (zooms 13 à 18, WebP) dans tiles/<date>/
et ajoute la date à tiles/annees.json (la liste lue par l'appli).

Usage : python3 scripts/tuiles.py 1958-10-25
"""
import json
import shutil
import subprocess
import sys

from osgeo import gdal

from commun import RECALES, TUILES

gdal.UseExceptions()

ZOOMS = "13-18"
QUALITE = 70
MOIS = ["janv.", "févr.", "mars", "avril", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def taille(dossier):
    return sum(f.stat().st_size for f in dossier.rglob("*") if f.is_file())


def ajoute_a_la_liste(date):
    fichier = TUILES / "annees.json"
    annees = json.loads(fichier.read_text()) if fichier.exists() else []
    annees = [a for a in annees if a["date"] != date]
    a, m, j = date.split("-")
    annees.append({"date": date, "annee": a,
                   "description": f"Photos IGN du {int(j)} {MOIS[int(m) - 1]} {a}"})
    annees.sort(key=lambda x: x["date"])
    fichier.write_text(json.dumps(annees, indent=1, ensure_ascii=False) + "\n")


def main():
    date = sys.argv[1]
    mosaique = RECALES / date / "mosaique.tif"
    sortie = TUILES / date
    if sortie.exists():
        shutil.rmtree(sortie)
    # le WebP veut du rouge-vert-bleu + transparence : le gris est recopié 3 fois
    rgba = RECALES / date / "mosaique_rgba.vrt"
    gdal.Translate(str(rgba), str(mosaique), format="VRT", bandList=[1, 1, 1, 2])
    gdal2tiles = shutil.which("gdal2tiles.py") or shutil.which("gdal2tiles")
    subprocess.run([sys.executable, gdal2tiles, "--xyz", "-z", ZOOMS, "-r", "average",
                    "--tiledriver=WEBP", f"--webp-quality={QUALITE}", "--processes=4",
                    "-w", "none", "--exclude", str(rgba), str(sortie)], check=True)
    # fichiers annexes inutiles pour le site
    for f in sortie.glob("*.xml"):
        f.unlink()
    ajoute_a_la_liste(date)
    print(f"{date} : {taille(sortie) / 1e6:.1f} Mo de tuiles")
    print(f"Total du dossier tiles/ : {taille(TUILES) / 1e6:.1f} Mo")


if __name__ == "__main__":
    main()
