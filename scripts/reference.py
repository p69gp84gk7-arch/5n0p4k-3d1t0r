"""Télécharge une ortho IGN sur la zone (élargie) : c'est la référence du recalage.

Usage :
    python3 scripts/reference.py                  # ortho 2022 (référence principale)
    python3 scripts/reference.py 1950-1965        # ortho historique IGN (aide au placement grossier)
"""
import io
import sys

import numpy as np
from osgeo import gdal
from PIL import Image

gdal.UseExceptions()

from commun import COUCHE_ORTHO, REFERENCE, WMS_ORTHO, telecharge, zone_merc

RESOLUTION = 0.6   # mètres Web Mercator par pixel (≈ zoom 18)
MARGE = 2500       # on déborde de la zone, car les photos dépassent
BLOC = 2048        # le WMS accepte au plus 5010 px ; plus petit = moins de coupures


def main():
    REFERENCE.mkdir(parents=True, exist_ok=True)
    if len(sys.argv) > 1:
        periode = sys.argv[1]
        couche, fmt, sortie = f"ORTHOIMAGERY.ORTHOPHOTOS.{periode}", "image/png", REFERENCE / f"histo{periode}.tif"
    else:
        couche, fmt, sortie = COUCHE_ORTHO, "image/jpeg", REFERENCE / "ortho2022.tif"
    if sortie.exists():
        print("Déjà présent :", sortie)
        return
    x0, y0, x1, y1 = zone_merc()
    x0, y0, x1, y1 = x0 - MARGE, y0 - MARGE, x1 + MARGE, y1 + MARGE
    largeur = int((x1 - x0) / RESOLUTION)
    hauteur = int((y1 - y0) / RESOLUTION)
    tmp = sortie.with_suffix(".part.tif")
    ds = gdal.GetDriverByName("GTiff").Create(
        str(tmp), largeur, hauteur, 3, gdal.GDT_Byte,
        ["TILED=YES", "COMPRESS=JPEG", "JPEG_QUALITY=90", "PHOTOMETRIC=YCBCR", "BIGTIFF=YES"])
    ds.SetGeoTransform((x0, RESOLUTION, 0, y1, 0, -RESOLUTION))
    ds.SetProjection("EPSG:3857")
    n = 0
    for py in range(0, hauteur, BLOC):
        for px in range(0, largeur, BLOC):
            w, h = min(BLOC, largeur - px), min(BLOC, hauteur - py)
            bx0 = x0 + px * RESOLUTION
            by1 = y1 - py * RESOLUTION
            bbox = f"{bx0},{by1 - h * RESOLUTION},{bx0 + w * RESOLUTION},{by1}"
            for essai in range(5):   # une image peut arriver tronquée : on recommence
                r = telecharge(WMS_ORTHO, params={
                    "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap",
                    "LAYERS": couche, "STYLES": "", "CRS": "EPSG:3857",
                    "BBOX": bbox, "WIDTH": w, "HEIGHT": h, "FORMAT": fmt})
                try:
                    img = np.array(Image.open(io.BytesIO(r.content)).convert("RGB"))
                    break
                except OSError:
                    if essai == 4:
                        raise
            for b in range(3):
                ds.GetRasterBand(b + 1).WriteArray(img[:, :, b], px, py)
            n += 1
            print(f"  bloc {n} ({px},{py})", flush=True)
    ds.BuildOverviews("AVERAGE", [2, 4, 8, 16, 32])
    ds = None
    tmp.rename(sortie)
    print("OK :", sortie, largeur, "x", hauteur)


if __name__ == "__main__":
    main()
