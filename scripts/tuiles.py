"""Découpe la mosaïque d'une date en tuiles web (zooms 13 à 18, WebP) dans tiles/<date>/,
écrit tiles/<date>/info.json et ajoute la date à annees.json (la liste lue par l'appli,
qui n'affiche que les dates dont le dossier de tuiles existe).

Le zoom maximal dépend de la finesse des photos : 18 si elles montrent des détails
d'au moins 45 cm, sinon 17 (au-delà, l'appli agrandit le niveau 17 : même rendu,
3 fois moins de place).

Le contraste est réglé automatiquement : les 1 % de pixels les plus sombres
deviennent noirs et les 1 % les plus clairs blancs, et les tons moyens sont un peu
éclaircis (certains scans sont très sombres).

Usage : python3 scripts/tuiles.py 1958-10-25
"""
import json
import shutil
import subprocess
import sys

import numpy as np
from osgeo import gdal

from shapely.geometry import shape

from commun import BRUTS, DEPOT, RECALES, TUILES

gdal.UseExceptions()

ZOOM_MIN = 13
FINESSE_ZOOM_18 = 0.45   # m Web Mercator par pixel de photo
QUALITE = 70
ETIREMENT = (1, 99)   # percentiles ramenés au noir et au blanc
GAMMA = 0.8           # < 1 : éclaircit un peu les tons moyens (forêts très sombres)
MOIS = ["janv.", "févr.", "mars", "avril", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def taille(dossier):
    return sum(f.stat().st_size for f in dossier.rglob("*") if f.is_file())


def info(date, zoom_max=18):
    a, m, j = date.split("-")
    return {"date": date, "annee": a, "description": f"Photos IGN du {int(j)} {MOIS[int(m) - 1]} {a}",
            "max": zoom_max}


def zoom_max(date):
    """Zoom maximal utile : d'après la taille au sol d'un pixel des photos de la date."""
    finesses = []
    choix = BRUTS / date / "choix.json"
    for p in json.loads(choix.read_text()) if choix.exists() else []:
        chemin = BRUTS / date / (p["id"] + ".tif")
        if chemin.exists():
            ds = gdal.Open(str(chemin))
            x0, y0, x1, y1 = shape(p["emprise"]).bounds
            finesses.append(max(x1 - x0, y1 - y0) / max(ds.RasterXSize, ds.RasterYSize))
    if not finesses:   # photos brutes déjà effacées : la finesse est notée dans les rapports
        finesses = [json.loads(f.read_text()).get("finesse_m") for f in (RECALES / date).glob("IGNF_*.json")]
        finesses = [v for v in finesses if v]
    return 18 if not finesses or np.median(finesses) <= FINESSE_ZOOM_18 else 17


def ajoute_a_la_liste(date):
    fichier = DEPOT / "annees.json"
    annees = json.loads(fichier.read_text()) if fichier.exists() else []
    if not any(a["date"] == date for a in annees):
        annees.append(info(date))
        annees.sort(key=lambda x: x["date"])
        fichier.write_text(json.dumps(annees, indent=1, ensure_ascii=False) + "\n")


def main():
    date = sys.argv[1]
    mosaique = RECALES / date / "mosaique.tif"
    sortie = TUILES / date
    if sortie.exists():
        shutil.rmtree(sortie)
    # réglage du contraste, mesuré sur la partie utile de la mosaïque
    ds = gdal.Open(str(mosaique))
    w = min(2000, ds.RasterXSize)
    h = int(w * ds.RasterYSize / ds.RasterXSize)
    gris = ds.GetRasterBand(1).ReadAsArray(buf_xsize=w, buf_ysize=h)
    utile = ds.GetRasterBand(2).ReadAsArray(buf_xsize=w, buf_ysize=h) > 0
    bas, haut = (float(v) for v in np.percentile(gris[utile], ETIREMENT))
    ds = None
    # le WebP veut du rouge-vert-bleu + transparence : le gris est recopié 3 fois
    rgba = RECALES / date / "mosaique_rgba.vrt"
    etire = [bas, max(haut, bas + 1), 0, 255]
    gdal.Translate(str(rgba), str(mosaique), format="VRT", bandList=[1, 1, 1, 2],
                   scaleParams=[etire, etire, etire, [0, 255, 0, 255]],
                   exponents=[GAMMA, GAMMA, GAMMA, 1])
    gdal2tiles = shutil.which("gdal2tiles.py") or shutil.which("gdal2tiles")
    zmax = zoom_max(date)
    subprocess.run([sys.executable, gdal2tiles, "--xyz", "-z", f"{ZOOM_MIN}-{zmax}", "-r", "average",
                    "--tiledriver=WEBP", f"--webp-quality={QUALITE}", "--processes=4",
                    "-w", "none", "--exclude", str(rgba), str(sortie)], check=True)
    # fichiers annexes inutiles pour le site
    for f in sortie.glob("*.xml"):
        f.unlink()
    (sortie / "info.json").write_text(json.dumps(info(date, zmax), ensure_ascii=False) + "\n")
    ajoute_a_la_liste(date)
    print(f"{date} : {taille(sortie) / 1e6:.1f} Mo de tuiles, zooms {ZOOM_MIN} à {zmax} "
          f"(contraste : {bas:.0f}-{haut:.0f} -> 0-255)")
    print(f"Total du dossier tiles/ : {taille(TUILES) / 1e6:.1f} Mo")


if __name__ == "__main__":
    main()
