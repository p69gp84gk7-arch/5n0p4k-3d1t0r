"""Cherche sur l'IGN les photos d'une date qui couvrent la zone, choisit les
utiles et les télécharge.

Usage :
    python3 scripts/photos.py liste                  # toutes les dates disponibles
    python3 scripts/photos.py 1958-10-25             # choisit + télécharge cette date
    python3 scripts/photos.py 1958-10-25 complete    # ajoute des photos là où la
                                                     # mosaïque a des trous (après recale.py)
"""
import json
import sys
from collections import defaultdict

import cv2
import numpy as np
from osgeo import gdal
from shapely import affinity
from shapely.geometry import box, shape
from shapely.ops import unary_union

from commun import BRUTS, GEOPF, RECALES, TRAVAIL, telecharge, zone_merc

gdal.UseExceptions()

WFS = GEOPF + "/wfs"
NOYAU = 0.75   # on vise à couvrir la zone avec le centre des photos (moins déformé)


def wfs(type_name, filtre, proprietes=None):
    x0, y0, x1, y1 = zone_merc()
    poly = f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"
    cql = f"INTERSECTS(geom,{poly})" + (f" AND {filtre}" if filtre else "")
    params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature",
              "typeName": type_name, "cql_filter": cql, "outputFormat": "json"}
    if proprietes:
        params["propertyName"] = proprietes
    return telecharge(WFS, params=params).json()["features"]


def missions():
    return wfs("pva:dataset", None, "date_mission,oblique,couleur,resolution,support,dataset_identifier,geom")


def photos_par_date():
    """{date: [photos verticales]} pour toutes les missions de la zone."""
    dates = defaultdict(list)
    for m in missions():
        p = m["properties"]
        if p.get("oblique"):
            continue
        ident = p["dataset_identifier"]
        for f in wfs("pva:image", f"dataset_identifier='{ident}'"):
            q = f["properties"]
            dates[q["date_cliche"].rstrip("Z")].append({
                "id": q["image_identifier"], "mission": ident,
                "couleur": p.get("couleur"), "orientation": q.get("orientation"),
                "centre": [q["x"], q["y"]], "emprise": f["geometry"]})
    return dict(sorted(dates.items()))


def choisir(photos):
    """Choix glouton : à chaque tour, la photo dont le centre couvre le plus de zone restante."""
    zone = box(*zone_merc())
    reste = zone
    choix = []
    candidats = {p["id"]: p for p in photos}
    while reste.area > 0.005 * zone.area and candidats:
        def gain(p):
            noyau = affinity.scale(shape(p["emprise"]), NOYAU, NOYAU)
            return noyau.intersection(reste).area
        meilleur = max(candidats.values(), key=gain)
        if gain(meilleur) < 0.002 * zone.area:
            break
        choix.append(meilleur)
        del candidats[meilleur["id"]]
        reste = reste.difference(affinity.scale(shape(meilleur["emprise"]), NOYAU, NOYAU))
    couvert = unary_union([shape(p["emprise"]) for p in choix]).intersection(zone).area / zone.area
    return choix, couvert


def telecharge_photos(date, photos):
    dossier = BRUTS / date
    dossier.mkdir(parents=True, exist_ok=True)
    for p in photos:
        chemin = dossier / (p["id"] + ".tif")
        if not chemin.exists():
            url = f"{GEOPF}/telechargement/download/pva/{p['mission']}/{p['id']}.tif"
            tmp = chemin.with_suffix(".part")
            tmp.write_bytes(telecharge(url).content)
            tmp.rename(chemin)
        print("  ", p["id"])


def complete(date, toutes):
    """Après un premier recalage : choisit d'autres photos de la même date pour
    boucher les trous (zones non couvertes par une photo bien recalée)."""
    PAS = 20.0
    x0, y0, x1, y1 = zone_merc()
    w, h = int((x1 - x0) / PAS), int((y1 - y0) / PAS)
    couvert = np.zeros((h, w), bool)
    rapports = json.loads((RECALES / date / "rapport.json").read_text())
    for r in rapports.values():
        if r.get("statut") == "ok":
            ds = gdal.Warp("", str(RECALES / date / (r["photo"] + ".tif")), format="MEM",
                           outputBounds=(x0, y0, x1, y1), width=w, height=h, resampleAlg="near")
            couvert |= ds.GetRasterBand(2).ReadAsArray() > 0
    choix = json.loads((BRUTS / date / "choix.json").read_text())
    essayees = {p["id"] for p in choix}
    candidats = [p for p in toutes if p["id"] not in essayees]

    def masque(p):
        m = np.zeros((h, w), np.uint8)
        noyau = affinity.scale(shape(p["emprise"]), NOYAU, NOYAU)
        pts = np.array(noyau.exterior.coords)
        pix = np.column_stack([(pts[:, 0] - x0) / PAS, (y1 - pts[:, 1]) / PAS]).astype(np.int32)
        cv2.fillPoly(m, [pix], 1)
        return m > 0

    trous = ~couvert
    print(f"{date} : zone couverte à {couvert.mean():.0%} avant complément")
    ajout = []
    while candidats:
        gains = [(masque(p) & trous).sum() for p in candidats]
        i = int(np.argmax(gains))
        if gains[i] < 0.003 * trous.size:
            break
        ajout.append(candidats.pop(i))
        trous &= ~masque(ajout[-1])
    print(f"  {len(ajout)} photos ajoutées pour boucher les trous")
    telecharge_photos(date, ajout)
    (BRUTS / date / "choix.json").write_text(json.dumps(choix + ajout, indent=1))


def main():
    TRAVAIL.mkdir(parents=True, exist_ok=True)
    cache = TRAVAIL / "photos_par_date.json"
    if cache.exists():
        dates = json.loads(cache.read_text())
    else:
        dates = photos_par_date()
        cache.write_text(json.dumps(dates))
    if len(sys.argv) < 2 or sys.argv[1] == "liste":
        for d, ph in dates.items():
            print(d, len(ph), "photos", ph[0]["couleur"], ph[0]["mission"])
        return
    date = sys.argv[1]
    if len(sys.argv) > 2 and sys.argv[2] == "complete":
        complete(date, dates[date])
        return
    choix, couvert = choisir(dates[date])
    print(f"{date} : {len(choix)} photos retenues sur {len(dates[date])}, zone couverte à {couvert:.0%}")
    telecharge_photos(date, choix)
    (BRUTS / date / "choix.json").write_text(json.dumps(choix, indent=1))


if __name__ == "__main__":
    main()
