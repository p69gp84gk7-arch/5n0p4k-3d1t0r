"""Vérification finale et indépendante de la mosaïque d'une date.

La mosaïque terminée est comparée à l'ortho 2022 par carrés de 250 m (recherche
jusqu'à 320 m). Un carré est déclaré décalé si son écart dépasse 60 m et que c'est
sûr : au moins deux carrés voisins (distincts) décalés de la même façon, ou un seul
si la ressemblance est forte. Un carré isolé est le plus souvent une fausse
ressemblance (bandes de rochers, lisières qui se répètent).
Ce filet de sécurité vise les grosses erreurs (un secteur parti à 100 m ou plus) ;
entre 20 et 60 m, la mesure est trompée par les changements du paysage (lisières de
forêt qui ont avancé) et c'est le contrôle de chaque photo qui fait foi.
Les carrés décalés (avec une marge) sont effacés de la mosaïque : ils ne seront pas
publiés. Le bilan est écrit dans recales/<date>/verification.json.

Sur l'ortho historique de l'IGN (bien placée), ce test efface à tort moins de 1 %
de la surface : c'est le prix à payer pour ne pas laisser passer de grosse erreur.

Usage : python3 scripts/verifie.py 1958-10-25
"""
import json
import sys

import cv2
import numpy as np
from osgeo import gdal
from scipy.spatial import cKDTree

from commun import RECALES, REFERENCE

gdal.UseExceptions()

RES = 4.0            # m par pixel pour la comparaison
CARRE = 250.0        # taille des carrés comparés (m)
PAS = 125.0          # un carré tous les 125 m (ils se chevauchent)
RECHERCHE = 320.0    # décalage maximal cherché (m)
DECALAGE_MAX = 60.0  # au-delà, le carré est décalé (voir plus haut)
SCORE_MIN = 0.3      # ressemblance minimale pour mesurer un écart
SCORE_SUR = 0.5      # ressemblance forte : un seul voisin confirmé suffit
MARGE = 60.0         # marge effacée autour d'un carré décalé (m)


def lire(fichier, bornes, gris=True):
    x0, y0, x1, y1 = bornes
    ds = gdal.Warp("", str(fichier), format="MEM", outputBounds=bornes,
                   xRes=RES, yRes=RES, resampleAlg="average")
    a = ds.ReadAsArray().astype(np.float32)
    if a.shape[0] == 2:
        return a[0], a[1] > 0
    return 0.3 * a[0] + 0.59 * a[1] + 0.11 * a[2], None


def detail(a):
    """Garde les formes (routes, crêtes, bâtiments), pas l'éclairage."""
    a = cv2.GaussianBlur(a, (0, 0), 1.0)
    return a - cv2.GaussianBlur(a, (0, 0), 8)


def mesure(mos, ortho):
    """Écart de chaque carré de la mosaïque avec l'ortho 2022."""
    ds = gdal.Open(str(mos))
    gt = ds.GetGeoTransform()
    bornes = (gt[0], gt[3] + ds.RasterYSize * gt[5], gt[0] + ds.RasterXSize * gt[1], gt[3])
    ds = None
    m, alpha = lire(mos, bornes)
    o, _ = lire(ortho, bornes)
    mn, on = detail(m), detail(o)
    p, s, pas = int(CARRE / RES), int(RECHERCHE / RES), int(PAS / RES)
    res = []
    for r in range(s, m.shape[0] - p - s, pas):
        for c in range(s, m.shape[1] - p - s, pas):
            if alpha[r:r + p, c:c + p].mean() < 0.9:
                continue
            t = mn[r:r + p, c:c + p]
            if t.std() < 1:
                continue
            cc = cv2.matchTemplate(on[r - s:r + p + s, c - s:c + p + s], t, cv2.TM_CCOEFF_NORMED)
            _, sc, _, loc = cv2.minMaxLoc(cc)
            x = bornes[0] + (c + p / 2) * RES
            y = bornes[3] - (r + p / 2) * RES
            res.append((x, y, (loc[0] - s) * RES, (loc[1] - s) * RES, sc))
    return np.array(res).reshape(-1, 5), alpha


def decales(res):
    """Indices des carrés sûrement décalés."""
    xy, v, sc = res[:, :2], res[:, 2:4], res[:, 4]
    d = np.hypot(v[:, 0], v[:, 1])
    arbre = cKDTree(xy) if len(xy) else None
    out = []
    for k in range(len(res)):
        if d[k] <= DECALAGE_MAX or sc[k] < SCORE_MIN:
            continue
        # voisins qui ne chevauchent pas ce carré (sinon ils voient la même image)
        voisins = [j for j in arbre.query_ball_point(xy[k], 400)
                   if np.abs(xy[j] - xy[k]).max() >= CARRE
                   and sc[j] >= SCORE_MIN and np.hypot(*(v[j] - v[k])) < 40]
        if len(voisins) >= 2 or (sc[k] >= SCORE_SUR and len(voisins) >= 1):
            out.append(k)
    return out


def efface(mos, centres):
    """Rend transparents les carrés décalés (et leur marge) dans la mosaïque."""
    ds = gdal.Open(str(mos), gdal.GA_Update)
    gt = ds.GetGeoTransform()
    bande = ds.GetRasterBand(2)
    e = CARRE / 2 + MARGE
    for x, y in centres:
        c0 = max(0, int((x - e - gt[0]) / gt[1]))
        c1 = min(ds.RasterXSize, int((x + e - gt[0]) / gt[1]))
        r0 = max(0, int((y + e - gt[3]) / gt[5]))
        r1 = min(ds.RasterYSize, int((y - e - gt[3]) / gt[5]))
        if c1 > c0 and r1 > r0:
            bande.WriteArray(np.zeros((r1 - r0, c1 - c0), np.uint8), c0, r0)
    if centres:
        ds.BuildOverviews("AVERAGE", [2, 4, 8, 16, 32])   # les vues réduites aussi
    ds = None


def main():
    date = sys.argv[1]
    mos = RECALES / date / "mosaique.tif"
    res, alpha = mesure(mos, REFERENCE / "ortho2022.tif")
    mauvais = decales(res)
    mesurables = int((res[:, 4] >= SCORE_MIN).sum()) if len(res) else 0
    efface(mos, [tuple(res[k, :2]) for k in mauvais])
    ds = gdal.Open(str(mos))
    w = 1000
    h = int(w * ds.RasterYSize / ds.RasterXSize)
    apres = ds.GetRasterBand(2).ReadAsArray(buf_xsize=w, buf_ysize=h) > 0
    avant = cv2.resize(alpha.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST) > 0
    ds = None
    bilan = {"carres": int(len(res)), "mesurables": mesurables, "decales": len(mauvais),
             "part_retiree": round(float(1 - apres.sum() / max(1, avant.sum())), 3),
             "liste": [{"x": round(float(res[k, 0])), "y": round(float(res[k, 1])),
                        "ecart_m": round(float(np.hypot(res[k, 2], res[k, 3]))),
                        "score": round(float(res[k, 4]), 2)} for k in mauvais]}
    (RECALES / date / "verification.json").write_text(json.dumps(bilan, indent=1))
    print(f"{date} : {len(res)} carrés, {mesurables} mesurables, {len(mauvais)} décalés effacés "
          f"({bilan['part_retiree']:.0%} de la mosaïque retirée)")


if __name__ == "__main__":
    main()
