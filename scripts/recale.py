"""Géoréférence une photo IGN brute en la recalant sur l'ortho 2022.

1. Départ : l'emprise de la photo publiée par l'IGN (8 points : coins et milieux
   des côtés, déjà projetés sur le relief), mais décalée de 100 à 300 m.
2. Correction globale : on fait glisser/tourner la photo entière pour qu'elle
   colle au mieux à l'ortho (historique de la même époque, ou 2022). Tous les
   repères stables comptent ensemble : lac, crêtes, rebord du plateau, routes,
   bâtiments, rues de Luchon.
3. Placement fin : comparaison de centaines de petits carrés de terrain, de plus
   en plus fins (2,4 m, 1,2 m puis 0,6 m sur l'ortho 2022). Les carrés qui ont
   changé (forêt, pistes...) ne ressemblent à rien et sont écartés.
4. GDAL : la photo est déformée (thin plate spline, qui suit le relief) puis
   contrôlée : on mesure l'écart restant avec l'ortho 2022, en mètres.

Usage : python3 scripts/recale.py 1958-10-25 [fin_du_nom_de_photo ...]
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from multiprocessing import get_context

import cv2
import numpy as np
from osgeo import gdal
from scipy.interpolate import RBFInterpolator
from scipy.spatial import cKDTree
from shapely.geometry import shape

from commun import BRUTS, RECALES, REFERENCE, bande_alpha, lire_gris, points_manuels

gdal.UseExceptions()

MARGE_CADRE = 0.10     # on ignore le bord des scans (cadre, trous, numéros, reflets)
RES_FINE = 0.6         # m Web Mercator / pixel (zoom 18)
RES_GLOBALE = 4.0      # résolution de la correction globale
RETRAIT_EMPRISE = 0.03 # les sommets de l'emprise IGN tombent un peu à l'intérieur du scan
# passes fines : (résolution m, taille des carrés px, recherche px, tolérance px, références)
PASSES = [(6.0, 48, 30, 3.0, ("histo", "2022")),   # rattrape des décalages locaux jusqu'à ~180 m
          (2.4, 64, 20, 4.0, ("histo", "2022")),
          (1.2, 64, 12, 3.0, ("histo", "2022")),
          (0.6, 64, 8, 2.0, ("2022",))]
DEBORD = 600           # marge autour de l'emprise annoncée par l'IGN (m)
COS_LAT = 0.734        # 1 m Web Mercator = 0,734 m au sol à cette latitude

# Seuils de qualité (mètres au sol)
SEUIL_MEDIANE = 4.0
SEUIL_P90 = 10.0
PROCESSUS = 3          # photos traitées en même temps
MIN_CARRES = 25        # nombre minimal de points de contrôle
MIN_ZONES = 4          # ... répartis dans au moins 4 des 9 zones (3 x 3) de la photo
# Contrôle grossier (grands carrés de 250 m, recherche jusqu'à 200 m) : détecte une
# photo partie au mauvais endroit, que le contrôle fin (recherche ~20 m) ne voit pas
RES_GROSSIER = 4.0
SEUIL_GROSSIER = 8.0          # écart médian maximal (m)
DECALAGE_MAX = 20.0           # un secteur décalé de plus de 20 m est retiré de la mosaïque
MARGE_SECTEUR = 200.0         # ... avec 200 m autour
PORTEE_REPERE = 250.0         # au-delà, un secteur n'a pas de repère vérifié tout près : non vérifié, retiré...
PORTEE_INTERIEUR = 800.0      # ... sauf au cœur de la photo (pistes, prés, forêts sans repère net) :
BORD_SUR = 800.0              # à plus de 800 m du bord, un secteur sans repère mais entouré de
                              # secteurs vérifiés à moins de 800 m est gardé
ACCORD_MANUEL = 60.0          # m : un point automatique plus loin que ça des points manuels est faux
PART_GARDEE_MIN = 0.10        # une photo dont il reste moins de 10 % est écartée
PART_DECALEE_MAX = 0.30       # ... ou dont plus de 30 % des carrés contrôlés sont décalés


def historique_pour(date):
    """Ortho historique IGN de la même époque (aucune après 1995 : on utilise 2022)."""
    annee = int(date[:4])
    if annee <= 1965:
        return "1950-1965"
    if annee <= 1980:
        return "1965-1980"
    if annee <= 1995:
        return "1980-1995"
    return None


def clahe(img):
    return cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(img)


class Ortho:
    def __init__(self, nom):
        self.nom = nom
        self.ds = gdal.Open(str(REFERENCE / f"{nom}.tif"))
        self.gt = self.ds.GetGeoTransform()

    def lire(self, x0, y0, x1, y1, res):
        """Ortho en niveaux de gris (+ masque des zones vides) sur l'emprise."""
        gt = self.gt
        px0 = max(int(round((x0 - gt[0]) / gt[1])), 0)
        py0 = max(int(round((gt[3] - y1) / -gt[5])), 0)
        px1 = min(int(round((x1 - gt[0]) / gt[1])), self.ds.RasterXSize)
        py1 = min(int(round((gt[3] - y0) / -gt[5])), self.ds.RasterYSize)
        f = res / gt[1]
        w, h = int((px1 - px0) / f), int((py1 - py0) / f)
        rgb = self.ds.ReadAsArray(px0, py0, px1 - px0, py1 - py0, buf_xsize=w, buf_ysize=h,
                                  resample_alg=gdal.GRIORA_Average).astype(np.float32)
        gris = (0.3 * rgb[0] + 0.59 * rgb[1] + 0.11 * rgb[2]).astype(np.uint8)
        valide = rgb.sum(axis=0) > 0
        origine = (gt[0] + px0 * gt[1], gt[3] + py0 * gt[5])
        return gris, valide, origine


class Scan:
    """Photo brute, lue à plusieurs résolutions (les TIFF IGN contiennent des versions réduites)."""
    def __init__(self, chemin, mpp):
        self.ds = gdal.Open(str(chemin))
        self.W, self.H = self.ds.RasterXSize, self.ds.RasterYSize
        self.mpp = mpp              # mètres Web Mercator par pixel du scan (estimation)
        self._cache = {}

    def niveau(self, res):
        """Version réduite dont les pixels font à peu près res mètres (sans crénelage)."""
        f = int(2 ** max(0, min(5, np.floor(np.log2(res / self.mpp)))))
        if f not in self._cache:
            w, h = self.W // f, self.H // f
            img = lire_gris(self.ds, buf_xsize=w, buf_ysize=h, resample_alg=gdal.GRIORA_Average)
            self._cache[f] = (img, self.W / w)
        return self._cache[f]


def masque_cadre(forme, marge=MARGE_CADRE):
    h, w = forme
    m = np.zeros((h, w), np.uint8)
    m[int(h * marge):int(h * (1 - marge)), int(w * marge):int(w * (1 - marge))] = 255
    return m


def depart_emprise(photo, scan):
    """Les 8 sommets de l'emprise IGN (4 coins + milieux des côtés, dans un ordre
    qui varie d'une mission à l'autre) <-> coins et milieux des côtés du scan.

    Les coins sont reconnus par leur position (NO, NE, SE, SO). Avec une orientation
    IGN d'environ 180°, le nord est en haut du scan (coin haut-gauche = NO) ; chaque
    quart de tour de plus décale d'un coin (267° : haut-gauche = SO)."""
    anneau = np.array(shape(photo["emprise"]).exterior.coords)[:-1]
    if len(anneau) != 8:
        return None, None
    centre = anneau.mean(axis=0)
    dist = np.linalg.norm(anneau - centre, axis=1)
    parite = 0 if dist[0::2].mean() > dist[1::2].mean() else 1   # les coins sont les plus loin
    coins = {}
    for i in range(parite, 8, 2):
        dx, dy = anneau[i] - centre
        coins[("N" if dy > 0 else "S") + ("O" if dx < 0 else "E")] = i
    if len(coins) != 4:
        return None, None
    geo = [coins["NO"], coins["NE"], coins["SE"], coins["SO"]]

    def milieu(i, j):   # le sommet entre deux coins voisins sur l'anneau
        return (i + 1) % 8 if (i + 2) % 8 == j else (j + 1) % 8

    q = int(round(((photo.get("orientation") or 180) - 180) / 90)) % 4
    a, b, m = RETRAIT_EMPRISE, 1 - RETRAIT_EMPRISE, 0.5
    scan_coins = [(a, a), (b, a), (b, b), (a, b)]          # haut-gauche, haut-droit, bas-droit, bas-gauche
    scan_milieux = [(m, a), (b, m), (m, b), (a, m)]        # haut, droite, bas, gauche
    src, dst = [], []
    for k in range(4):
        g1, g2 = geo[(k - q) % 4], geo[(k + 1 - q) % 4]
        src += [scan_coins[k], scan_milieux[k]]
        dst += [anneau[g1], anneau[milieu(g1, g2)]]
    return np.float64(src) * [scan.W, scan.H], np.float64(dst)


def correction_globale(scan, gcp_photo, gcp_geo, orthos):
    """Fait glisser / tourner / zoomer légèrement la photo entière pour qu'elle colle
    au mieux à l'ortho. Renvoie (score, unicité, nom de la référence, nouvelles coordonnées)."""
    img_s, f = scan.niveau(RES_GLOBALE)
    em = (gcp_geo[:, 0].min() - DEBORD, gcp_geo[:, 1].min() - DEBORD,
          gcp_geo[:, 0].max() + DEBORD, gcp_geo[:, 1].max() + DEBORD)
    meilleur = (-1, 0, None, None)
    for ortho in orthos:
        ref, _, org = ortho.lire(*em, RES_GLOBALE)
        rp = np.column_stack([(gcp_geo[:, 0] - org[0]) / RES_GLOBALE, (org[1] - gcp_geo[:, 1]) / RES_GLOBALE])
        mx, my = carte_inverse(rp, gcp_photo / f, ref.shape, smoothing=0.0)
        img = cv2.remap(img_s, mx, my, cv2.INTER_LINEAR, borderValue=0)
        centre = cv2.remap(masque_cadre(img_s.shape, 0.15), mx, my, cv2.INTER_NEAREST, borderValue=0) > 0
        ys, xs = np.where(centre)
        if len(xs) == 0:
            continue
        cy, cx = (ys.min() + ys.max()) / 2, (xs.min() + xs.max()) / 2
        hh, ww = (ys.max() - ys.min()) * 0.42, (xs.max() - xs.min()) * 0.42
        R2 = cv2.GaussianBlur(clahe(ref).astype(np.float32), (0, 0), 1)
        for angle in np.arange(-10, 10.1, 1.5):
            for echelle in np.arange(0.90, 1.11, 0.04):
                M = cv2.getRotationMatrix2D((cx, cy), angle, echelle)
                tourne = cv2.warpAffine(img, M, (img.shape[1], img.shape[0]))
                modele = cv2.GaussianBlur(clahe(tourne).astype(np.float32), (0, 0), 1)
                modele = modele[int(cy - hh):int(cy + hh), int(cx - ww):int(cx + ww)]
                r = cv2.matchTemplate(R2, modele, cv2.TM_CCOEFF_NORMED)
                _, score, _, loc = cv2.minMaxLoc(r)
                if score <= meilleur[0]:
                    continue
                r[max(0, loc[1] - 10):loc[1] + 11, max(0, loc[0] - 10):loc[0] + 11] = -1
                t = np.float64([loc[0] - int(cx - ww), loc[1] - int(cy - hh)])
                nouv = (M @ np.column_stack([rp, np.ones(len(rp))]).T).T + t
                geo = np.column_stack([org[0] + nouv[:, 0] * RES_GLOBALE, org[1] - nouv[:, 1] * RES_GLOBALE])
                meilleur = (score, score - r.max(), ortho.nom, geo)
    return meilleur


def carte_inverse(ref_pts, photo_pts, forme, pas=16, smoothing=1.0):
    """Pour chaque pixel de la grille ortho : position dans la photo (pour cv2.remap)."""
    h, w = forme
    ys, xs = np.mgrid[0:h + pas:pas, 0:w + pas:pas]
    grille = np.column_stack([xs.ravel(), ys.ravel()]).astype(np.float64)
    rbf = RBFInterpolator(ref_pts, photo_pts, kernel="thin_plate_spline",
                          smoothing=smoothing, neighbors=min(len(ref_pts), 200))
    val = rbf(grille).reshape(ys.shape + (2,)).astype(np.float32)
    # chaque nœud de la grille correspond au pixel (i*pas) : on agrandit puis on recadre
    mx = cv2.resize(val[..., 0], (val.shape[1] * pas, val.shape[0] * pas), interpolation=cv2.INTER_LINEAR)
    my = cv2.resize(val[..., 1], (val.shape[1] * pas, val.shape[0] * pas), interpolation=cv2.INTER_LINEAR)
    decal = pas // 2   # cv2.resize aligne les centres de pixels
    return (mx[decal:decal + h, decal:decal + w].copy(), my[decal:decal + h, decal:decal + w].copy())


def correlation_carres(img, ref, valide, pas=40, taille=64, recherche=20, seuil=0.35, valide_ref=None):
    """Compare des carrés de img à ref autour de la même position.
    Si valide_ref est donné, seul le carré de img doit être dans valide (et la zone
    de recherche dans valide_ref) : on peut alors contrôler jusqu'au bord de la photo.
    Renvoie (positions dans img, décalages vers ref, score)."""
    h, w = img.shape
    t2 = taille // 2
    pts, dec, scores = [], [], []
    a = clahe(img).astype(np.float32)
    b = clahe(ref).astype(np.float32)
    for y in range(t2 + recherche, h - t2 - recherche, pas):
        for x in range(t2 + recherche, w - t2 - recherche, pas):
            if valide_ref is None:
                if not valide[y - t2 - recherche:y + t2 + recherche, x - t2 - recherche:x + t2 + recherche].all():
                    continue
            elif not (valide[y - t2:y + t2, x - t2:x + t2].all()
                      and valide_ref[y - t2 - recherche:y + t2 + recherche, x - t2 - recherche:x + t2 + recherche].all()):
                continue
            modele = a[y - t2:y + t2, x - t2:x + t2]
            if modele.std() < 8:
                continue
            zone = b[y - t2 - recherche:y + t2 + recherche, x - t2 - recherche:x + t2 + recherche]
            res = cv2.matchTemplate(zone, modele, cv2.TM_CCOEFF_NORMED)
            _, mx, _, loc = cv2.minMaxLoc(res)
            if mx < seuil:
                continue
            lx, ly = loc
            if lx in (0, res.shape[1] - 1) or ly in (0, res.shape[0] - 1):
                continue

            def sub(c, m, p):   # précision sous-pixel (parabole)
                d = c - 2 * m + p
                return 0.0 if d == 0 else 0.5 * (c - p) / d
            dx = lx - recherche + sub(res[ly, lx - 1], res[ly, lx], res[ly, lx + 1])
            dy = ly - recherche + sub(res[ly - 1, lx], res[ly, lx], res[ly + 1, lx])
            pts.append((x, y))
            dec.append((dx, dy))
            scores.append(mx)
    return np.float32(pts).reshape(-1, 2), np.float32(dec).reshape(-1, 2), np.float32(scores)


def filtre_voisins(pts, dec, rayon, tol):
    """Garde les décalages qui ressemblent à ceux de leurs voisins (le relief varie
    doucement ; un carré isolé qui part ailleurs est une fausse ressemblance)."""
    garde = np.zeros(len(pts), bool)
    if len(pts) < 10:
        return garde
    arbre = cKDTree(pts)
    for i, p in enumerate(pts):
        idx = arbre.query_ball_point(p, rayon)
        if len(idx) < 5:
            continue
        med = np.median(dec[idx], axis=0)
        garde[i] = np.linalg.norm(dec[i] - med) < tol
    return garde


def passe_fine(scan, gcp_photo, gcp_geo, ortho, emprise, res, taille, recherche, tol):
    """Une passe de comparaison de carrés : renvoie de nouveaux points (photo, géo)."""
    img_s, f = scan.niveau(res)
    ref, ref_valide, org = ortho.lire(*emprise, res)
    rp = np.column_stack([(gcp_geo[:, 0] - org[0]) / res, (org[1] - gcp_geo[:, 1]) / res])
    mx, my = carte_inverse(rp, gcp_photo / f, ref.shape,
                           smoothing=len(rp) * 0.5 if len(rp) > 40 else 0.0)
    img = cv2.remap(img_s, mx, my, cv2.INTER_LINEAR, borderValue=0)
    valide = cv2.remap(masque_cadre(img_s.shape), mx, my, cv2.INTER_NEAREST, borderValue=0) > 0
    valide &= ref_valide
    pts, dec, _ = correlation_carres(img, ref, valide, pas=taille // 2, taille=taille,
                                     recherche=recherche)
    garde = filtre_voisins(pts, dec, rayon=max(200 / res, 3 * taille), tol=tol)
    pts, dec = pts[garde], dec[garde]
    ix, iy = pts[:, 0].astype(int), pts[:, 1].astype(int)
    photo = np.column_stack([mx[iy, ix], my[iy, ix]]) * f
    cible = pts + dec
    geo = np.column_stack([org[0] + cible[:, 0] * res, org[1] - cible[:, 1] * res])
    return photo, geo


def lisse(photo, geo):
    """Lisse légèrement les points de calage et retire les aberrants."""
    rbf = RBFInterpolator(photo, geo, kernel="thin_plate_spline", smoothing=len(photo) * 0.02,
                          neighbors=min(len(photo), 150))
    g2 = rbf(photo)
    err = np.linalg.norm(g2 - geo, axis=1)
    garde = err < max(2.0, 3 * np.median(err))
    return photo[garde], g2[garde]


def controle(sortie, ortho2022, res=RES_FINE):
    """Écart restant entre la photo recalée et l'ortho 2022 (mètres au sol), mesuré à la
    résolution res (celle des photos : inutile, et trop gourmand, d'aller plus fin)."""
    out = gdal.Open(str(sortie))
    gt = out.GetGeoTransform()
    w0, h0 = int(out.RasterXSize * gt[1] / res), int(out.RasterYSize * -gt[5] / res)
    img = lire_gris(out, buf_xsize=w0, buf_ysize=h0, resample_alg=gdal.GRIORA_Average)
    alpha = bande_alpha(out).ReadAsArray(buf_xsize=w0, buf_ysize=h0) > 0
    ox0, oy1 = gt[0], gt[3]
    ox1, oy0 = ox0 + out.RasterXSize * gt[1], oy1 + out.RasterYSize * gt[5]
    ref, ref_valide, org = ortho2022.lire(ox0, oy0, ox1, oy1, res)
    h = min(ref.shape[0], img.shape[0])
    w = min(ref.shape[1], img.shape[1])
    valide = alpha[:h, :w] & ref_valide[:h, :w]
    pts, dec, _ = correlation_carres(img[:h, :w], ref[:h, :w], valide, pas=48, recherche=25, seuil=0.45)
    if len(pts) < 10:
        return {"carres_controle": int(len(pts)), "zones_controlees": 0}
    garde = filtre_voisins(pts, dec, rayon=400, tol=6.0)   # écarte les vrais changements du paysage
    pts, ecart = pts[garde], np.linalg.norm(dec[garde], axis=1) * res * COS_LAT
    if len(ecart) < 10:
        return {"carres_controle": int(len(ecart)), "zones_controlees": 0}
    # répartition : dans combien des 9 zones de la photo trouve-t-on des points de contrôle ?
    ys, xs = np.where(valide)
    zx = np.clip(((pts[:, 0] - xs.min()) / (xs.max() - xs.min() + 1) * 3).astype(int), 0, 2)
    zy = np.clip(((pts[:, 1] - ys.min()) / (ys.max() - ys.min() + 1) * 3).astype(int), 0, 2)
    zones = len(set(zip(zx.tolist(), zy.tolist()))) if len(pts) else 0
    return {"mediane_m": round(float(np.median(ecart)), 2),
            "p90_m": round(float(np.percentile(ecart, 90)), 2),
            "carres_controle": int(len(ecart)),
            "zones_controlees": int(zones),
            **controle_grossier(out, ortho2022, sortie.with_name(sortie.stem + "_secteurs.tif"))}


def controle_grossier(out, ortho2022, chemin_secteurs):
    """Écart à grande échelle avec l'ortho 2022 : grands carrés (250 m), recherche
    jusqu'à 320 m. Détecte une photo (ou une partie) placée au mauvais endroit.

    Écrit aussi la carte des secteurs de la photo (4 m par pixel) : 1 = vérifié bien
    placé (seul utilisé dans la mosaïque), 0 = décalé, 2 = sans repère comparable."""
    res = RES_GROSSIER
    gt = out.GetGeoTransform()
    w, h = int(out.RasterXSize * gt[1] / res), int(out.RasterYSize * -gt[5] / res)
    img = lire_gris(out, buf_xsize=w, buf_ysize=h, resample_alg=gdal.GRIORA_Average)
    alpha = bande_alpha(out).ReadAsArray(buf_xsize=w, buf_ysize=h) > 0
    ref, ref_valide, _ = ortho2022.lire(gt[0], gt[3] - h * res, gt[0] + w * res, gt[3], res)
    hh, ww = min(h, ref.shape[0]), min(w, ref.shape[1])
    # le carré de la photo doit être entier, mais la recherche peut sortir de la photo
    # (sinon les 400 m du bord, là où les photos se déforment le plus, ne sont jamais contrôlés)
    pts, dec, _ = correlation_carres(img[:hh, :ww], ref[:hh, :ww], alpha[:hh, :ww], pas=24, taille=64,
                                         recherche=80, seuil=0.3, valide_ref=ref_valide[:hh, :ww])
    garde = filtre_voisins(pts, dec, rayon=220, tol=3.0)
    if garde.sum() < 10:
        return {"grossier_carres": int(garde.sum())}
    ecart = np.linalg.norm(dec, axis=1) * res * COS_LAT
    bon = garde & (ecart <= DECALAGE_MAX)
    # un décalage compte s'il est confirmé par les carrés voisins (une partie de photo
    # mal placée l'est d'un seul tenant) ; un carré isolé est une fausse ressemblance
    mauvais = (ecart > DECALAGE_MAX) & garde
    # carte des secteurs : bien placé seulement tout près d'un carré vérifié bon
    ys, xs = np.mgrid[0:h, 0:w]
    grille = np.column_stack([xs.ravel(), ys.ravel()]).astype(np.float32)
    secteurs = np.full(h * w, 2, np.uint8)
    if bon.any():
        d_bon, _ = cKDTree(pts[bon]).query(grille)
        secteurs[d_bon * res <= PORTEE_REPERE] = 1
        # au cœur de la photo, la déformation est douce : un secteur sans repère (pistes
        # claires, prés, bâtiments changés) suit ses voisins vérifiés. Près du bord, non :
        # c'est là que les photos se déforment (le lac de 1977, décalé de 160 m)
        bord = cv2.distanceTransform(alpha.astype(np.uint8), cv2.DIST_L2, 5).ravel() * res
        secteurs[(secteurs == 2) & (d_bon * res <= PORTEE_INTERIEUR) & (bord >= BORD_SUR)] = 1
    if mauvais.any():
        d_mauvais, _ = cKDTree(pts[mauvais]).query(grille)
        secteurs[d_mauvais * res < MARGE_SECTEUR] = 0
    secteurs = secteurs.reshape(h, w).astype(np.uint8)
    ds = gdal.GetDriverByName("GTiff").Create(str(chemin_secteurs), w, h, 1, gdal.GDT_Byte, ["COMPRESS=DEFLATE"])
    ds.SetGeoTransform((gt[0], res, 0, gt[3], 0, -res))
    ds.SetProjection("EPSG:3857")
    ds.GetRasterBand(1).WriteArray(secteurs)
    ds = None
    part_gardee = float((secteurs[alpha] == 1).mean()) if alpha.any() else 0.0
    return {"grossier_carres": int(garde.sum()),
            "grossier_m": round(float(np.median(ecart[garde])), 1),
            "grossier_part_decalee": round(float(mauvais.sum() / max(1, bon.sum() + mauvais.sum())), 2),
            "part_gardee": round(part_gardee, 2)}


def recale(date, photo, orthos, sortie):
    emp = shape(photo["emprise"]).bounds
    scan = Scan(BRUTS / date / (photo["id"] + ".tif"), mpp=1.0)
    scan.mpp = max(emp[2] - emp[0], emp[3] - emp[1]) / max(scan.W, scan.H)
    W, H = scan.W, scan.H
    rapport = {"photo": photo["id"], "finesse_m": round(float(scan.mpp), 3)}
    # photos à petite échelle (pixels > 0,6 m) : on calcule à leur finesse réelle,
    # pas plus fin (sinon 6 à 9 fois plus de mémoire pour rien)
    echelle = max(1.0, scan.mpp / RES_FINE)

    manuels = points_manuels(date, photo["id"])
    if manuels:
        # 1-2. départ : les points placés à la main (page placer.html)
        gcp_photo = np.array([[p["u"] * W, p["v"] * H] for p in manuels], np.float64)
        gcp_geo = np.array([[p["x"], p["y"]] for p in manuels], np.float64)
        man_photo, man_geo = gcp_photo.copy(), gcp_geo.copy()
        modele_manuel = RBFInterpolator(man_photo, man_geo, kernel="thin_plate_spline", smoothing=len(man_photo) * 0.5)
        rapport["manuel"] = len(manuels)
    else:
        # 1. départ : emprise IGN
        gcp_photo, gcp_geo = depart_emprise(photo, scan)
        if gcp_photo is None:
            rapport.update(statut="échec", raison="emprise IGN inutilisable")
            return rapport

        # 2. correction globale
        score, unicite, nom, gcp_geo = correction_globale(scan, gcp_photo, gcp_geo, list(orthos.values()))
        rapport["global"] = {"reference": nom, "correlation": round(float(score), 3),
                             "unicite": round(float(unicite), 3)}
        if gcp_geo is None:
            rapport.update(statut="échec", raison="correction globale impossible")
            return rapport
    if manuels:
        # emprise de toute la photo (pas seulement des points) : coins par une transformation affine
        A = np.column_stack([gcp_photo, np.ones(len(gcp_photo))])
        coef = np.linalg.lstsq(A, gcp_geo, rcond=None)[0]
        coins = np.array([[0, 0, 1], [W, 0, 1], [W, H, 1], [0, H, 1]], np.float64) @ coef
    else:
        coins = gcp_geo
    emprise = (coins[:, 0].min() - 200, coins[:, 1].min() - 200,
               coins[:, 0].max() + 200, coins[:, 1].max() + 200)

    # 3. passes fines, de 2,4 m à 0,6 m (ou à la finesse des photos si elle est moindre)
    rapport["passes"] = []
    for res, taille, recherche, tol, refs in PASSES:
        res = round(res * echelle, 2)
        for nom in [r for r in refs if r in orthos]:
            p2, g2 = passe_fine(scan, gcp_photo, gcp_geo, orthos[nom], emprise, res, taille, recherche, tol)
            if len(p2) >= 40:
                break
        if manuels and len(p2):
            # on écarte ce qui contredit les points placés à la main (fausses ressemblances)
            accord = np.linalg.norm(modele_manuel(p2) - g2, axis=1) * COS_LAT < ACCORD_MANUEL
            p2, g2 = p2[accord], g2[accord]
        rapport["passes"].append({"res": res, "reference": nom, "points": int(len(p2))})
        if len(p2) < 15:
            continue   # passe sans résultat : on garde le placement précédent
        p2, g2 = lisse(p2, g2)
        # là où cette passe n'a rien trouvé (paysage changé), on garde les points précédents
        loin = cKDTree(p2).query(gcp_photo)[0] > 2500
        gcp_photo, gcp_geo = np.vstack([p2, gcp_photo[loin]]), np.vstack([g2, gcp_geo[loin]])

    if manuels:
        # les passes automatiques ne doivent pas contredire les points placés à la main :
        # si, à l'endroit de ces points, elles s'en écartent de plus de 25 m, on ne garde
        # que les points manuels (fausses ressemblances dans un paysage qui a changé)
        rbf = RBFInterpolator(gcp_photo, gcp_geo, kernel="thin_plate_spline", smoothing=len(gcp_photo) * 0.02,
                              neighbors=min(len(gcp_photo), 150))
        ecart = np.median(np.linalg.norm(rbf(man_photo) - man_geo, axis=1)) * COS_LAT
        rapport["ecart_points_manuels_m"] = round(float(ecart), 1)
        if ecart > 25:
            gcp_photo, gcp_geo = man_photo, man_geo
            rapport["passes_ignorees"] = True

    # On limite le nombre de points de calage
    if len(gcp_photo) > 1500:
        sel = np.random.default_rng(0).choice(len(gcp_photo), 1500, replace=False)
        gcp_photo, gcp_geo = gcp_photo[sel], gcp_geo[sel]
    rapport["points_calage"] = int(len(gcp_photo))

    # 3. GDAL : photo recadrée (sans le cadre) + points de calage -> thin plate spline
    mx0, my0 = int(W * MARGE_CADRE), int(H * MARGE_CADRE)
    mw, mh = int(W * (1 - 2 * MARGE_CADRE)), int(H * (1 - 2 * MARGE_CADRE))
    gcps = [gdal.GCP(float(g[0]), float(g[1]), 0, float(p[0] - mx0), float(p[1] - my0))
            for p, g in zip(gcp_photo, gcp_geo)]
    vrt = str(sortie.with_suffix(".vrt"))
    gdal.Translate(vrt, scan.ds, format="VRT", srcWin=[mx0, my0, mw, mh], GCPs=gcps, outputSRS="EPSG:3857")
    gdal.Warp(str(sortie), vrt, dstSRS="EPSG:3857", tps=True, xRes=RES_FINE, yRes=RES_FINE,
              targetAlignedPixels=True, resampleAlg="cubic", dstAlpha=True,
              warpMemoryLimit=1024, multithread=True,
              creationOptions=["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=2"])

    # 4. contrôle
    rapport.update(controle(sortie, orthos["2022"], RES_FINE * echelle))
    rapport["statut"] = statut(rapport)
    return rapport


def statut(rapport):
    """Verdict à partir des mesures de contrôle (réappliqué à chaque lancement)."""
    if rapport.get("manuel"):
        # photo placée à la main : gardée sauf si l'écart avec l'ortho 2022 est vraiment grand
        return "ok" if rapport.get("mediane_m", 0) <= 2 * SEUIL_MEDIANE else "mal recalée"
    if "carres_controle" not in rapport:
        return rapport["statut"]   # échec avant le contrôle
    bon = (rapport.get("mediane_m", 99) <= SEUIL_MEDIANE and rapport.get("p90_m", 99) <= SEUIL_P90
           and rapport["carres_controle"] >= MIN_CARRES and rapport["zones_controlees"] >= MIN_ZONES
           and rapport.get("grossier_m", 0) <= SEUIL_GROSSIER
           and rapport.get("part_gardee", 1) >= PART_GARDEE_MIN
           and rapport.get("grossier_part_decalee", 0) <= PART_DECALEE_MAX)
    return "ok" if bon else "mal recalée"


def traite(args):
    """Recale une photo (dans un processus à part) et écrit son rapport <photo>.json."""
    date, p = args
    dossier = RECALES / date
    orthos = {"2022": Ortho("ortho2022")}
    if historique_pour(date):
        orthos = {"histo": Ortho("histo" + historique_pour(date)), **orthos}
    try:
        r = recale(date, p, orthos, dossier / (p["id"] + ".tif"))
    except Exception as e:   # une photo ratée ne doit pas arrêter les autres
        r = {"photo": p["id"], "statut": "échec", "raison": f"erreur : {e}"}
    r["centre"] = p["centre"]
    (dossier / (p["id"] + ".json")).write_text(json.dumps(r, indent=1, ensure_ascii=False))
    return r


def main():
    date = sys.argv[1]
    choix = json.loads((BRUTS / date / "choix.json").read_text())
    dossier = RECALES / date
    dossier.mkdir(parents=True, exist_ok=True)
    if len(sys.argv) > 2:   # photos désignées : on les refait
        choix = [p for p in choix if any(p["id"].endswith(s) for s in sys.argv[2:])]
    else:                   # sinon on ne refait pas celles déjà traitées
        choix = [p for p in choix if not (dossier / (p["id"] + ".json")).exists()]
    # Plusieurs photos à la fois. Si un processus est tué net (manque de mémoire),
    # les photos non terminées sont retentées une par une ; une photo qui fait encore
    # tomber son processus est notée en échec, au lieu de tout bloquer.
    restantes = list(choix)
    for travailleurs in (PROCESSUS, 1):
        if not restantes:
            break
        if travailleurs == 1:
            print(f"{len(restantes)} photo(s) retentée(s) une par une", flush=True)
        lots = [restantes] if travailleurs > 1 else [[p] for p in restantes]
        for lot in lots:
            try:
                with ProcessPoolExecutor(travailleurs, mp_context=get_context("spawn")) as ex:
                    for r in ex.map(traite, [(date, p) for p in lot]):
                        print(json.dumps(r, ensure_ascii=False), flush=True)
            except BrokenProcessPool:
                if travailleurs == 1:
                    p = lot[0]
                    r = {"photo": p["id"], "statut": "échec", "centre": p["centre"],
                         "raison": "le calcul s'est arrêté net (mémoire insuffisante ?)"}
                    (dossier / (p["id"] + ".json")).write_text(json.dumps(r, indent=1, ensure_ascii=False))
                    print(json.dumps(r, ensure_ascii=False), flush=True)
        restantes = [p for p in restantes if not (dossier / (p["id"] + ".json")).exists()]
    # rapport de la date = réunion des rapports des photos, avec les seuils actuels
    rapports = {}
    for f in sorted(dossier.glob("IGNF_*.json")):
        r = json.loads(f.read_text())
        r["statut"] = statut(r)
        rapports[r["photo"]] = r
    (dossier / "rapport.json").write_text(json.dumps(rapports, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
