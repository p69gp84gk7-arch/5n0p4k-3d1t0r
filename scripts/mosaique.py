"""Assemble les photos recalées d'une même date en une seule image (mosaïque).

En chaque point, on prend la photo dont le centre est le plus proche (c'est là
qu'elle est la moins déformée). Les raccords sont fondus :
- les détails sur une bande étroite (FONDU_DETAIL),
- la luminosité et le contraste généraux sur une bande large (FONDU_LUMIERE),
  pour éviter les différences de teinte entre deux clichés voisins,
- et chaque photo s'estompe aussi près de ses propres bords (FONDU_BORD).
Seules les photos marquées « ok » par recale.py sont utilisées, et seulement dans
leurs secteurs vérifiés bien placés (carte <photo>_secteurs.tif écrite par le
contrôle grossier). Si les photos sont en couleur, la mosaïque l'est aussi (chaque
couleur est fondue séparément).

Usage : python3 scripts/mosaique.py 1958-10-25
"""
import json
import sys

import cv2
import numpy as np
from osgeo import gdal
from scipy.spatial import cKDTree

from commun import RECALES, bande_alpha, nb_couleurs, points_manuels, zone_merc

gdal.UseExceptions()

RES = 0.6
COS_LAT = 0.734          # 1 m Web Mercator = 0,734 m au sol à cette latitude
BLOC = 2048
FONDU_DETAIL = 40.0     # mètres Web Mercator
FONDU_LUMIERE = 600.0
FONDU_BORD = 120.0      # une photo s'estompe sur 120 m près de ses bords
REDUC = 8               # résolution de calcul de la luminosité générale (8 x 0,6 m)
SIGMA_LUMIERE = 30      # en pixels réduits (≈ 150 m)
RAYON_MANUEL = 1500.0   # autour des points placés à la main, la photo passe devant et n'est pas effacée


class Photo:
    def __init__(self, chemin, centre, nc, manuels=None):
        self.ds = gdal.Open(str(chemin))
        self.nc = nc                     # bandes de la mosaïque (1 ou 3)
        self.bandes = [1, 2, 3] if nb_couleurs(self.ds) == 3 else [1] * nc
        self.alpha = bande_alpha(self.ds)
        gt = self.ds.GetGeoTransform()
        self.x0, self.y1 = gt[0], gt[3]
        self.w, self.h = self.ds.RasterXSize, self.ds.RasterYSize
        self.centre = centre
        self.manuel = bool(manuels)      # placée à la main : prioritaire près de ses points
        # secteurs décalés à retirer (0 = décalé)
        self.secteurs = None
        chemin_secteurs = chemin.with_name(chemin.stem + "_secteurs.tif")
        if chemin_secteurs.exists():
            s = gdal.Open(str(chemin_secteurs))
            self.secteurs, self.s_gt = s.GetRasterBand(1).ReadAsArray(), s.GetGeoTransform()
        # près des points placés à la main (grille réduite) : gardé et prioritaire
        self.proche = None
        if manuels:
            gx0 = self.x0 + (np.arange(self.w // REDUC) + 0.5) * RES * REDUC
            gy0 = self.y1 - (np.arange(self.h // REDUC) + 0.5) * RES * REDUC
            mx, my = np.meshgrid(gx0, gy0)
            dist = cKDTree([[p["x"], p["y"]] for p in manuels]).query(np.column_stack([mx.ravel(), my.ravel()]))[0]
            self.proche = (dist.reshape(mx.shape) * COS_LAT <= RAYON_MANUEL)
        # luminosité générale (moyenne locale, sans tenir compte des zones vides), par couleur
        a = (self.alpha.ReadAsArray(buf_xsize=self.w // REDUC, buf_ysize=self.h // REDUC,
                                    resample_alg=gdal.GRIORA_NearestNeighbour) > 0).astype(np.float32)
        gx = self.x0 + (np.arange(a.shape[1]) + 0.5) * RES * REDUC
        gy = self.y1 - (np.arange(a.shape[0]) + 0.5) * RES * REDUC
        a *= self.garde(gx, gy)
        den = np.maximum(cv2.GaussianBlur(a, (0, 0), SIGMA_LUMIERE), 1e-3)
        self.lumiere, self.contraste = [], []
        for b in self.bandes:
            img = self.ds.GetRasterBand(b).ReadAsArray(buf_xsize=self.w // REDUC, buf_ysize=self.h // REDUC,
                                                       resample_alg=gdal.GRIORA_Average).astype(np.float32)
            lum = (cv2.GaussianBlur(img * a, (0, 0), SIGMA_LUMIERE) / den).astype(np.float32)
            ecart2 = cv2.GaussianBlur(((img - lum) ** 2) * a, (0, 0), SIGMA_LUMIERE) / den
            self.lumiere.append(lum)
            self.contraste.append(np.sqrt(np.maximum(ecart2, 1.0)).astype(np.float32))
        # distance au bord de la partie utile (mètres)
        self.bord = (cv2.distanceTransform((a > 0).astype(np.uint8), cv2.DIST_L2, 5)
                     * REDUC * RES).astype(np.float32)

    def pres_des_points(self, xs, ys):
        """Masque : à moins de RAYON_MANUEL d'un point placé à la main."""
        if self.proche is None:
            return np.zeros((len(ys), len(xs)), bool)
        ix = np.clip(((xs - self.x0) / (RES * REDUC)).astype(int), 0, self.proche.shape[1] - 1)
        iy = np.clip(((self.y1 - ys) / (RES * REDUC)).astype(int), 0, self.proche.shape[0] - 1)
        return self.proche[iy[:, None], ix[None, :]]

    def garde(self, xs, ys):
        """Masque des secteurs à garder, pour une grille (xs, ys) en mètres : secteurs
        vérifiés, et tout ce qui est près des points placés à la main."""
        if self.secteurs is None:
            return np.ones((len(ys), len(xs)), bool)
        g = self.s_gt
        ix = np.clip(((xs - g[0]) / g[1]).astype(int), 0, self.secteurs.shape[1] - 1)
        iy = np.clip(((ys - g[3]) / g[5]).astype(int), 0, self.secteurs.shape[0] - 1)
        return (self.secteurs[iy[:, None], ix[None, :]] == 1) | self.pres_des_points(xs, ys)

    def fenetre(self, bx0, by1, bw, bh):
        """Lit le morceau de la photo qui tombe dans le bloc (ou None)."""
        ox = int(round((bx0 - self.x0) / RES))
        oy = int(round((self.y1 - by1) / RES))
        x0, y0 = max(ox, 0), max(oy, 0)
        x1, y1 = min(ox + bw, self.w), min(oy + bh, self.h)
        if x1 <= x0 or y1 <= y0:
            return None
        img, lum, con = (np.zeros((self.nc, bh, bw), np.float32) for _ in range(3))
        alpha = np.zeros((bh, bw), bool)
        bord = np.zeros((bh, bw), np.float32)
        sl = (slice(y0 - oy, y1 - oy), slice(x0 - ox, x1 - ox))
        for k, b in enumerate(self.bandes):
            img[k][sl] = self.ds.GetRasterBand(b).ReadAsArray(x0, y0, x1 - x0, y1 - y0)
        alpha[sl] = self.alpha.ReadAsArray(x0, y0, x1 - x0, y1 - y0) > 0
        alpha[sl] &= self.garde(self.x0 + (np.arange(x0, x1) + 0.5) * RES,
                                self.y1 - (np.arange(y0, y1) + 0.5) * RES)
        # luminosité, contraste et distance au bord, agrandis à la taille du morceau
        gx = (np.arange(x0, x1) + 0.5) / REDUC - 0.5
        gy = (np.arange(y0, y1) + 0.5) / REDUC - 0.5
        mx, my = np.meshgrid(gx.astype(np.float32), gy.astype(np.float32))
        paires = [(lum[k], self.lumiere[k]) for k in range(self.nc)]
        paires += [(con[k], self.contraste[k]) for k in range(self.nc)] + [(bord, self.bord)]
        for dest, src in paires:
            dest[sl] = cv2.remap(src, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        return img, alpha, lum, con, bord


def main():
    date = sys.argv[1]
    dossier = RECALES / date
    rapports = json.loads((dossier / "rapport.json").read_text())
    ok = [r for r in rapports.values() if r.get("statut") == "ok"]
    print(f"{len(ok)} photos « ok » sur {len(rapports)}")
    # en couleur si au moins une photo l'est (les photos en noir et blanc sont alors recopiées
    # sur les trois couleurs)
    nc = max([nb_couleurs(gdal.Open(str(dossier / (r["photo"] + ".tif")))) for r in ok] or [1])
    photos = [Photo(dossier / (r["photo"] + ".tif"), r["centre"], nc,
                    points_manuels(date, r["photo"]) if r.get("manuel") else None) for r in ok]

    x0, y0, x1, y1 = zone_merc()
    x0, y1 = np.floor(x0 / RES) * RES, np.ceil(y1 / RES) * RES
    largeur, hauteur = int(np.ceil((x1 - x0) / RES)), int(np.ceil((y1 - y0) / RES))
    sortie = dossier / "mosaique.tif"
    out = gdal.GetDriverByName("GTiff").Create(
        str(sortie), largeur, hauteur, nc + 1, gdal.GDT_Byte,
        ["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=2", "BIGTIFF=YES"])
    out.SetGeoTransform((x0, RES, 0, y1, 0, -RES))
    out.SetProjection("EPSG:3857")
    if nc == 3:
        for k, ci in enumerate((gdal.GCI_RedBand, gdal.GCI_GreenBand, gdal.GCI_BlueBand)):
            out.GetRasterBand(k + 1).SetColorInterpretation(ci)
    out.GetRasterBand(nc + 1).SetColorInterpretation(gdal.GCI_AlphaBand)

    for by in range(0, hauteur, BLOC):
        for bx in range(0, largeur, BLOC):
            bw, bh = min(BLOC, largeur - bx), min(BLOC, hauteur - by)
            X0, Y1 = x0 + bx * RES, y1 - by * RES
            xs = X0 + (np.arange(bw) + 0.5) * RES
            ys = Y1 - (np.arange(bh) + 0.5) * RES
            morceaux = []
            for p in photos:
                f = p.fenetre(X0, Y1, bw, bh)
                if f is None or not f[1].any():
                    continue
                d = np.hypot(xs[None, :] - p.centre[0], ys[:, None] - p.centre[1]).astype(np.float32)
                d += 3 * np.clip(FONDU_BORD - f[4], 0, None)   # près de son bord, une photo cède la place
                if p.manuel:   # près de ses points, une photo placée à la main passe devant
                    d -= 1e6 * p.pres_des_points(xs, ys)
                d[~f[1]] = np.inf
                morceaux.append((f, d))
            if not morceaux:
                continue
            dmin = np.min([d for _, d in morceaux], axis=0)
            num_d, num_l, num_c = (np.zeros((nc, bh, bw), np.float32) for _ in range(3))
            den_d, den_l = (np.zeros((bh, bw), np.float32) for _ in range(2))
            for (img, alpha, lum, con, bord), d in morceaux:
                estompe = np.clip(bord / FONDU_BORD, 0.01, 1) * alpha
                wd = np.clip((dmin + FONDU_DETAIL - d) / FONDU_DETAIL, 0, 1) * estompe
                wl = np.clip((dmin + FONDU_LUMIERE - d) / FONDU_LUMIERE, 0, 1) * estompe
                num_d += wd * (img - lum) / np.maximum(con, 1.0)   # détails à contraste égalisé
                den_d += wd
                num_l += wl * lum
                num_c += wl * con
                den_l += wl
            valide = (den_d > 0) & (den_l > 0)
            for k in range(nc):
                res = np.zeros((bh, bw), np.float32)
                res[valide] = (num_d[k][valide] / den_d[valide] * (num_c[k][valide] / den_l[valide])
                               + num_l[k][valide] / den_l[valide])
                out.GetRasterBand(k + 1).WriteArray(np.clip(res, 0, 255).astype(np.uint8), bx, by)
            out.GetRasterBand(nc + 1).WriteArray((valide * 255).astype(np.uint8), bx, by)
        print(f"  ligne {by // BLOC + 1}/{(hauteur + BLOC - 1) // BLOC}", flush=True)
    out.BuildOverviews("AVERAGE", [2, 4, 8, 16, 32])
    out = None
    print("OK :", sortie)


if __name__ == "__main__":
    main()
