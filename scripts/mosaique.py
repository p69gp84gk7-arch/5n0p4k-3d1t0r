"""Assemble les photos recalées d'une même date en une seule image (mosaïque).

Peu de photos, les meilleures, et une seule teinte :
1. Choix des photos : seules comptent la station de Superbagnères (tout le plateau)
   et Luchon. On prend le plus petit nombre de photos qui les couvrent, en
   préférant les plus détaillées (pixels les plus fins). Passent en premier :
   les photos placées à la main, puis celles choisies dans placer/priorites.json.
2. Une seule photo par endroit : là où plusieurs photos se recouvrent, la mieux
   classée l'emporte ; seul un léger fondu adoucit le bord de chaque photo.
   Là où le contrôle n'a pas vérifié qu'une photo est bien placée (carte
   <photo>_secteurs.tif), une autre photo vérifiée passe devant ; sinon elle reste
   affichée (rien n'est découpé). Le choix des photos en tient compte de la même façon.
3. Une seule teinte : la luminosité et le contraste de chaque photo (par couleur)
   sont accordés sur ceux des photos déjà posées, là où elles se recouvrent.
Une photo placée à la main ne passe devant les autres que dans la zone couverte par
ses points (enveloppe + 500 m) : loin de ses points, rien ne garantit sa position.

Le choix est noté dans recales/<date>/mosaique.json.

Usage : python3 scripts/mosaique.py 1958-10-25
"""
import json
import sys

import cv2
import numpy as np
from osgeo import gdal

from commun import DEPOT, RECALES, bande_alpha, nb_couleurs, points_manuels, zone_merc

gdal.UseExceptions()

RES = 0.6
BLOC = 2048
FONDU_BORD = 120.0      # une photo s'estompe sur 120 m près de ses bords
REDUC = 8               # grille réduite (8 x 0,6 m) pour le choix et l'accord des teintes
# Zones qui comptent (m Web Mercator)
LIEUX = {"station": (59500, 5275000, 65500, 5278200),   # plateau de Superbagnères et station
         "luchon": (65200, 5278600, 67200, 5281400)}
COUVERTURE_VISEE = 0.98  # on arrête d'ajouter des photos quand les lieux sont couverts à 98 %
GAIN_MIN = 0.01          # ... ou quand une photo n'apporte plus que 1 % des lieux
MARGE_MANUELLE = 500.0   # m au sol autour de l'enveloppe des points placés à la main


class Photo:
    def __init__(self, chemin, rapport, nc, manuels=None):
        self.id = rapport["photo"]
        self.numero = self.id.split("_")[-1]
        self.finesse = rapport.get("finesse_m") or 1.0
        self.ds = gdal.Open(str(chemin))
        self.nc = nc
        self.bandes = [1, 2, 3] if nb_couleurs(self.ds) == 3 else [1] * nc
        self.alpha = bande_alpha(self.ds)
        gt = self.ds.GetGeoTransform()
        self.x0, self.y1 = gt[0], gt[3]
        self.w, self.h = self.ds.RasterXSize, self.ds.RasterYSize
        self.manuel = bool(manuels)
        # secteurs non vérifiés par le contrôle (0 = décalé, 2 = sans repère) : moins prioritaires
        self.secteurs = None
        chemin_secteurs = chemin.with_name(chemin.stem + "_secteurs.tif")
        if chemin_secteurs.exists() and not self.manuel:
            s = gdal.Open(str(chemin_secteurs))
            self.secteurs, self.s_gt = s.GetRasterBand(1).ReadAsArray(), s.GetGeoTransform()
        self.gain = np.ones(nc, np.float32)     # accord des teintes : valeur * gain + decalage
        self.decalage = np.zeros(nc, np.float32)
        a = self.alpha.ReadAsArray(buf_xsize=self.w // REDUC, buf_ysize=self.h // REDUC,
                                   resample_alg=gdal.GRIORA_NearestNeighbour) > 0
        # distance au bord de la partie utile (mètres), pour le fondu
        self.bord = (cv2.distanceTransform(a.astype(np.uint8), cv2.DIST_L2, 5) * REDUC * RES).astype(np.float32)
        # zone couverte par les points placés à la main (grille réduite)
        self.zone_manuelle = None
        if manuels:
            from shapely.geometry import MultiPoint
            zone = MultiPoint([(p["x"], p["y"]) for p in manuels]).convex_hull.buffer(MARGE_MANUELLE / 0.734)
            masque = np.zeros(a.shape, np.uint8)
            pix = [[(x - self.x0) / (RES * REDUC), (self.y1 - y) / (RES * REDUC)] for x, y in zone.exterior.coords]
            cv2.fillPoly(masque, [np.round(pix).astype(np.int32)], 1)
            self.zone_manuelle = masque > 0

    def reduite(self, bornes, w, h):
        """La photo sur la grille réduite de la mosaïque : (image [nc, h, w], masque)."""
        ds = gdal.Warp("", self.ds, format="MEM", outputBounds=bornes, width=w, height=h,
                       resampleAlg="average", dstAlpha=False)
        a = ds.ReadAsArray().astype(np.float32)
        a = a[None] if a.ndim == 2 else a
        img = np.stack([a[b - 1] for b in self.bandes])
        m = a[-1] > 0
        return img, m

    def fiable(self, xs, ys):
        """Masque : là où le contrôle a vérifié que la photo est bien placée (1) ; ni les
        secteurs décalés (0) ni ceux sans repère vérifiable (2, souvent au bord, où la
        photo peut être très mal placée : Luchon en 1996). Version nette (choix des photos)."""
        if self.secteurs is None:
            return np.ones((len(ys), len(xs)), bool)
        g = self.s_gt
        ix = np.clip(((xs - g[0]) / g[1]).astype(int), 0, self.secteurs.shape[1] - 1)
        iy = np.clip(((ys - g[3]) / g[5]).astype(int), 0, self.secteurs.shape[0] - 1)
        return self.secteurs[iy[:, None], ix[None, :]] == 1

    def _doux(self, masque):
        """Version adoucie d'un masque de la grille réduite (fondu de FONDU_BORD)."""
        return cv2.GaussianBlur(masque.astype(np.float32), (0, 0), FONDU_BORD / (RES * REDUC) / 2)

    def masques_doux(self):
        """Prépare, sur la grille réduite de la photo, les masques adoucis « bien placé » et
        « près des points placés à la main » : les raccords entre photos sont fondus."""
        gx0 = self.x0 + (np.arange(self.bord.shape[1]) + 0.5) * RES * REDUC
        gy0 = self.y1 - (np.arange(self.bord.shape[0]) + 0.5) * RES * REDUC
        self.fiable_doux = self._doux(self.fiable(gx0, gy0))
        self.zone_doux = self._doux(self.zone_manuelle) if self.zone_manuelle is not None else None

    def fenetre(self, bx0, by1, bw, bh):
        """Le morceau de la photo qui tombe dans le bloc, teinte accordée (ou None)."""
        ox = int(round((bx0 - self.x0) / RES))
        oy = int(round((self.y1 - by1) / RES))
        x0, y0 = max(ox, 0), max(oy, 0)
        x1, y1 = min(ox + bw, self.w), min(oy + bh, self.h)
        if x1 <= x0 or y1 <= y0:
            return None
        img = np.zeros((self.nc, bh, bw), np.float32)
        alpha = np.zeros((bh, bw), bool)
        bord, fiable, zone = (np.zeros((bh, bw), np.float32) for _ in range(3))
        sl = (slice(y0 - oy, y1 - oy), slice(x0 - ox, x1 - ox))
        for k, b in enumerate(self.bandes):
            img[k][sl] = self.ds.GetRasterBand(b).ReadAsArray(x0, y0, x1 - x0, y1 - y0) * self.gain[k] + self.decalage[k]
        alpha[sl] = self.alpha.ReadAsArray(x0, y0, x1 - x0, y1 - y0) > 0
        gx = (np.arange(x0, x1) + 0.5) / REDUC - 0.5
        gy = (np.arange(y0, y1) + 0.5) / REDUC - 0.5
        mx, my = np.meshgrid(gx.astype(np.float32), gy.astype(np.float32))
        bord[sl] = cv2.remap(self.bord, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        fiable[sl] = cv2.remap(self.fiable_doux, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        if self.zone_doux is not None:
            zone[sl] = cv2.remap(self.zone_doux, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        return img, alpha, bord, fiable, zone


def priorites(date):
    """Numéros de photos choisis à la main pour cette date (placer/priorites.json)."""
    f = DEPOT / "placer" / "priorites.json"
    return [str(n) for n in json.loads(f.read_text()).get(date, [])] if f.exists() else []


def choisit(photos, masques, cibles, imposees):
    """Choix glouton : à chaque tour, la photo qui couvre le plus de lieux encore vides,
    pondéré par sa finesse (plus de détails). Les photos imposées passent d'abord."""
    reste = cibles.copy()
    ordre = []
    fin_min = min(p.finesse for p in photos)
    for p in [p for p in photos if p.manuel] + [p for p in photos if p.numero in imposees and not p.manuel]:
        ordre.append(p)
        reste &= ~masques[p.id]
    candidats = [p for p in photos if p not in ordre]
    while candidats and reste.sum() > (1 - COUVERTURE_VISEE) * cibles.sum():
        gains = [(masques[p.id] & reste).sum() / max(1, cibles.sum()) for p in candidats]
        notes = [g * (fin_min / p.finesse) for g, p in zip(gains, candidats)]
        i = int(np.argmax(notes))
        if gains[i] < GAIN_MIN:
            break
        ordre.append(candidats.pop(i))
        reste &= ~masques[ordre[-1].id]
    return ordre, 1 - reste.sum() / max(1, cibles.sum())


def accorde_teintes(ordre, images):
    """Accorde chaque photo sur celles déjà posées, là où elles se recouvrent (moyenne et
    écart-type par couleur) : une seule teinte pour toute l'année."""
    pose, masque = None, None
    for p in ordre:
        img, m = images[p.id]
        if pose is None:
            pose, masque = img.copy(), m.copy()
            continue
        commun = m & masque
        if commun.sum() > 500:
            for k in range(p.nc):
                a, b = img[k][commun], pose[k][commun]
                p.gain[k] = np.clip(b.std() / max(a.std(), 1.0), 0.5, 2.0)
                p.decalage[k] = b.mean() - p.gain[k] * a.mean()
        corr = img * p.gain[:, None, None] + p.decalage[:, None, None]
        neuf = m & ~masque
        pose[:, neuf] = corr[:, neuf]
        masque |= m


def main():
    date = sys.argv[1]
    dossier = RECALES / date
    rapports = json.loads((dossier / "rapport.json").read_text())
    ok = [r for r in rapports.values() if r.get("statut") == "ok"]
    print(f"{len(ok)} photos « ok » sur {len(rapports)}")
    # après un premier passage, seules les photos retenues sont gardées sur le disque
    ok = [r for r in ok if (dossier / (r["photo"] + ".tif")).exists()]
    nc = max([nb_couleurs(gdal.Open(str(dossier / (r["photo"] + ".tif")))) for r in ok] or [1])
    photos = [Photo(dossier / (r["photo"] + ".tif"), r, nc,
                    points_manuels(date, r["photo"]) if r.get("manuel") else None) for r in ok]

    x0, y0, x1, y1 = zone_merc()
    x0, y1 = np.floor(x0 / RES) * RES, np.ceil(y1 / RES) * RES
    largeur, hauteur = int(np.ceil((x1 - x0) / RES)), int(np.ceil((y1 - y0) / RES))

    # --- choix des photos et accord des teintes, sur une grille réduite ---
    wr, hr = largeur // REDUC, hauteur // REDUC
    bornes = (x0, y1 - hr * REDUC * RES, x0 + wr * REDUC * RES, y1)
    gx = bornes[0] + (np.arange(wr) + 0.5) * REDUC * RES
    gy = bornes[3] - (np.arange(hr) + 0.5) * REDUC * RES
    cibles = np.zeros((hr, wr), bool)
    for a, b, c, d in LIEUX.values():
        cibles |= (gx[None, :] >= a) & (gx[None, :] <= c) & (gy[:, None] >= b) & (gy[:, None] <= d)
    images = {p.id: p.reduite(bornes, wr, hr) for p in photos}
    masques = {p.id: images[p.id][1] & p.fiable(gx, gy) for p in photos}   # bien placé seulement
    imposees = priorites(date)
    ordre, couverture = choisit(photos, masques, cibles, imposees)
    accorde_teintes(ordre, images)
    print(f"{len(ordre)} photos retenues (station et Luchon couverts à {couverture:.0%}) :",
          ", ".join(p.numero + (" (à la main)" if p.manuel else " (choisie)" if p.numero in imposees else "")
                    for p in ordre))
    (dossier / "mosaique.json").write_text(json.dumps({
        "photos": [p.numero for p in ordre], "a_la_main": [p.numero for p in ordre if p.manuel],
        "imposees": [n for n in imposees], "couverture_lieux": round(float(couverture), 3)}, indent=1))

    # --- assemblage : de la dernière photo à la première, chacune posée par-dessus ---
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
    # de la plus prioritaire à la moins prioritaire : photos placées à la main (près de leurs
    # points), puis chaque photo là où elle est bien placée, puis là où elle semble décalée,
    # et enfin les photos placées à la main loin de leurs points
    for p in ordre:
        p.masques_doux()
    couches = ([(p, "points") for p in ordre if p.manuel]
               + [(p, "fiable") for p in ordre if not p.manuel]
               + [(p, "decale") for p in ordre if not p.manuel]
               + [(p, "loin") for p in ordre if p.manuel])
    for by in range(0, hauteur, BLOC):
        for bx in range(0, largeur, BLOC):
            bw, bh = min(BLOC, largeur - bx), min(BLOC, hauteur - by)
            X0, Y1 = x0 + bx * RES, y1 - by * RES
            xs = X0 + (np.arange(bw) + 0.5) * RES
            ys = Y1 - (np.arange(bh) + 0.5) * RES
            # chaque couche est posée par-dessus les précédentes avec un poids (fondu aux bords
            # et aux raccords) ; « couvert » cumule ces poids, et on divise à la fin : une photo
            # seule reste entière jusqu'à son bord, sans assombrissement
            res = np.zeros((nc, bh, bw), np.float32)
            couvert = np.zeros((bh, bw), np.float32)
            for p, quoi in reversed(couches):
                f = p.fenetre(X0, Y1, bw, bh)
                if f is None or not f[1].any():
                    continue
                img, alpha, bord, fiable, zone = f
                m = {"points": zone, "loin": 1 - zone, "fiable": fiable, "decale": 1 - fiable}[quoi]
                w = np.clip(bord / FONDU_BORD, 0.02, 1) * alpha * m
                res = res * (1 - w) + img * w
                couvert = couvert * (1 - w) + w
            visible = couvert > 0.005
            res[:, visible] /= couvert[visible]
            for k in range(nc):
                out.GetRasterBand(k + 1).WriteArray(np.clip(res[k], 0, 255).astype(np.uint8), bx, by)
            out.GetRasterBand(nc + 1).WriteArray((visible * 255).astype(np.uint8), bx, by)
        print(f"  ligne {by // BLOC + 1}/{(hauteur + BLOC - 1) // BLOC}", flush=True)
    out.BuildOverviews("AVERAGE", [2, 4, 8, 16, 32])
    out = None
    print("OK :", sortie)


if __name__ == "__main__":
    main()
