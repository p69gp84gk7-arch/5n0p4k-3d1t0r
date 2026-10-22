"""Aperçus des photos IGN brutes pour la page de placement manuel (placer.html).

Pour chaque date de annees.json, les photos (version préférée : couleur, puis noir
et blanc, puis infrarouge) qui couvrent la zone sont lues directement chez l'IGN,
en version réduite seulement (les TIFF IGN en contiennent), et enregistrées en JPEG
dans placer/apercus/<date>/. La liste est écrite dans placer/photos.json.

Usage : python3 scripts/apercus.py              # toutes les dates
        python3 scripts/apercus.py 1993-07-28   # une date
"""
import json
import os
import sys

import numpy as np
from osgeo import gdal
from PIL import Image
from shapely.geometry import box, shape

from commun import DEPOT, GEOPF, TRAVAIL, lire_gris, nb_couleurs, zone_merc
from photos import meilleure_version

gdal.UseExceptions()
for cle, val in {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "GDAL_HTTP_MAX_RETRY": "20",
                 "GDAL_HTTP_RETRY_DELAY": "2", "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif"}.items():
    gdal.SetConfigOption(cle, val)

TAILLE = 1500      # pixels du grand côté de l'aperçu
QUALITE = 70
DOSSIER = DEPOT / "placer"

# lieux qui aident à choisir une photo
LIEUX = {"station": box(59500, 5275000, 65500, 5278200), "luchon": box(65200, 5278600, 67200, 5281400)}


def apercu(date, p, sortie):
    url = f"/vsicurl/{GEOPF}/chunk/telechargement/download/pva/{p['mission']}/{p['id']}.tif"
    ds = gdal.Open(url)
    W, H = ds.RasterXSize, ds.RasterYSize
    f = TAILLE / max(W, H)
    w, h = int(W * f), int(H * f)
    if nb_couleurs(ds) == 3:
        a = np.dstack([ds.GetRasterBand(b).ReadAsArray(buf_xsize=w, buf_ysize=h,
                                                       resample_alg=gdal.GRIORA_Average) for b in (1, 2, 3)])
    else:
        a = lire_gris(ds, buf_xsize=w, buf_ysize=h, resample_alg=gdal.GRIORA_Average)
    sortie.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(a).save(sortie, quality=QUALITE, optimize=True)
    return W, H


def main():
    dates = json.loads((TRAVAIL / "photos_par_date.json").read_text())
    annees = [a["date"] for a in json.loads((DEPOT / "annees.json").read_text())]
    liste_f = DOSSIER / "photos.json"
    liste = json.loads(liste_f.read_text()) if liste_f.exists() else {}
    zone = box(*zone_merc())
    for date in sys.argv[1:] or annees:
        version = meilleure_version(dates[date])
        retenues = []
        for p in version:
            emp = shape(p["emprise"])
            if emp.intersection(zone).area < 0.05 * emp.area:
                continue
            sortie = DOSSIER / "apercus" / date / f"{p['id']}.jpg"
            deja = {q["id"]: q for q in liste.get(date, [])}.get(p["id"])
            try:
                W, H = (deja["largeur"], deja["hauteur"]) if deja and sortie.exists() else apercu(date, p, sortie)
            except Exception as e:   # photo illisible : on passe
                print("  échec", p["id"], str(e)[:100], flush=True)
                continue
            retenues.append({
                "id": p["id"], "mission": p["mission"], "numero": p["id"].split("_")[-1],
                "couleur": p["couleur"], "orientation": p.get("orientation"), "centre": p["centre"],
                "emprise": [[round(x), round(y)] for x, y in emp.exterior.coords],
                "largeur": W, "hauteur": H, "apercu": f"apercus/{date}/{p['id']}.jpg",
                "couvre": [nom for nom, lieu in LIEUX.items() if emp.contains(lieu.centroid)]})
            print(f"  {date} {retenues[-1]['numero']}", flush=True)
        liste[date] = sorted(retenues, key=lambda q: q["numero"])
        liste_f.parent.mkdir(parents=True, exist_ok=True)
        liste_f.write_text(json.dumps(liste, ensure_ascii=False, separators=(",", ":")))
        print(f"{date} : {len(retenues)} aperçus", flush=True)


if __name__ == "__main__":
    main()
