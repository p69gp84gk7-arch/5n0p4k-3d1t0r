"""Réglages communs à tous les scripts (zone, services IGN, dossiers)."""
import math
import os
import time
from pathlib import Path

import requests

# Zone couverte : station de Superbagnères + ville de Luchon (degrés WGS84)
ZONE_LON = (0.515, 0.615)
ZONE_LAT = (42.745, 42.810)

GEOPF = "https://data.geopf.fr"
WMS_ORTHO = GEOPF + "/wms-r"
COUCHE_ORTHO = "ORTHOIMAGERY.ORTHOPHOTOS2022"

# Dossiers : le dépôt, et un dossier de travail hors dépôt (fichiers lourds)
DEPOT = Path(os.environ.get("SB_DEPOT", Path(__file__).resolve().parent.parent))
TRAVAIL = Path(os.environ.get("SB_TRAVAIL", DEPOT / "travail"))
BRUTS = TRAVAIL / "bruts"          # TIFF IGN téléchargés
REFERENCE = TRAVAIL / "reference"  # ortho 2022 de référence
RECALES = TRAVAIL / "recales"      # photos géoréférencées
TUILES = DEPOT / "tiles"
CONTROLE = DEPOT / "controle"


def merc(lon, lat):
    """Degrés -> mètres Web Mercator (EPSG:3857)."""
    x = lon * 20037508.342789244 / 180
    y = math.log(math.tan((90 + lat) * math.pi / 360)) * 6378137
    return x, y


def zone_merc():
    x0, y0 = merc(ZONE_LON[0], ZONE_LAT[0])
    x1, y1 = merc(ZONE_LON[1], ZONE_LAT[1])
    return x0, y0, x1, y1


_session = requests.Session()


def telecharge(url, params=None, essais=5, **kw):
    """GET avec quelques nouvelles tentatives (le service IGN limite le débit)."""
    for i in range(essais):
        try:
            r = _session.get(url, params=params, timeout=300, **kw)
            if r.status_code == 200:
                return r
            if r.status_code not in (429, 500, 502, 503, 504):
                r.raise_for_status()
        except requests.RequestException:
            if i == essais - 1:
                raise
        time.sleep(2 ** i)
    raise RuntimeError(f"Échec du téléchargement : {url}")
