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
from multiprocessing import Pool

import cv2
import numpy as np
from osgeo import gdal
from scipy.interpolate import RBFInterpolator
from scipy.spatial import cKDTree
from shapely.geometry import shape

from commun import BRUTS, RECALES, REFERENCE

gdal.UseExceptions()

MARGE_CADRE = 0.10     # on ignore le bord des scans (cadre, trous, numéros, reflets)
RES_FINE = 0.6         # m Web Mercator / pixel (zoom 18)
RES_GLOBALE = 4.0      # résolution de la correction globale
RETRAIT_EMPRISE = 0.03 # les sommets de l'emprise IGN tombent un peu à l'intérieur du scan
# passes fines : (résolution m, taille des carrés px, recherche px, tolérance px, références)
PASSES = [(2.4, 64, 20, 4.0, ("histo", "2022")),
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
            img = self.ds.GetRasterBand(1).ReadAsArray(buf_xsize=w, buf_ysize=h,
                                                       resample_alg=gdal.GRIORA_Average)
            self._cache[f] = (img, self.W / w)
        return self._cache[f]


def masque_cadre(forme, marge=MARGE_CADRE):
    h, w = forme
    m = np.zeros((h, w), np.uint8)
    m[int(h * marge):int(h * (1 - marge)), int(w * marge):int(w * (1 - marge))] = 255
    return m


def depart_emprise(photo, scan):
    """Les 8 sommets de l'emprise IGN (NE, E, SE, S, SO, O, NO, N) <-> coins et
    milieux des côtés du scan (le nord est en haut des scans)."""
    anneau = np.array(shape(photo["emprise"]).exterior.coords)[:-1]
    if len(anneau) != 8:
        return None, None
    a, b, m = RETRAIT_EMPRISE, 1 - RETRAIT_EMPRISE, 0.5
    src = np.float64([[b, a], [b, m], [b, b], [m, b], [a, b], [a, m], [a, a], [m, a]]) * [scan.W, scan.H]
    return src, anneau


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


def correlation_carres(img, ref, valide, pas=40, taille=64, recherche=20, seuil=0.35):
    """Compare des carrés de img à ref autour de la même position.
    Renvoie (positions dans img, décalages vers ref, score)."""
    h, w = img.shape
    t2 = taille // 2
    pts, dec, scores = [], [], []
    a = clahe(img).astype(np.float32)
    b = clahe(ref).astype(np.float32)
    for y in range(t2 + recherche, h - t2 - recherche, pas):
        for x in range(t2 + recherche, w - t2 - recherche, pas):
            if not valide[y - t2 - recherche:y + t2 + recherche, x - t2 - recherche:x + t2 + recherche].all():
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


def controle(sortie, ortho2022):
    """Écart restant entre la photo recalée et l'ortho 2022 (mètres au sol)."""
    out = gdal.Open(str(sortie))
    gt = out.GetGeoTransform()
    img = out.GetRasterBand(1).ReadAsArray()
    alpha = out.GetRasterBand(2).ReadAsArray() > 0
    ox0, oy1 = gt[0], gt[3]
    ox1, oy0 = ox0 + out.RasterXSize * gt[1], oy1 + out.RasterYSize * gt[5]
    ref, ref_valide, org = ortho2022.lire(ox0, oy0, ox1, oy1, RES_FINE)
    h = min(ref.shape[0], img.shape[0])
    w = min(ref.shape[1], img.shape[1])
    valide = alpha[:h, :w] & ref_valide[:h, :w]
    pts, dec, _ = correlation_carres(img[:h, :w], ref[:h, :w], valide, pas=48, recherche=25, seuil=0.45)
    if len(pts) < 10:
        return {"carres_controle": int(len(pts)), "zones_controlees": 0}
    garde = filtre_voisins(pts, dec, rayon=400, tol=6.0)   # écarte les vrais changements du paysage
    pts, ecart = pts[garde], np.linalg.norm(dec[garde], axis=1) * RES_FINE * COS_LAT
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
            "zones_controlees": int(zones)}


def recale(date, photo, orthos, sortie):
    emp = shape(photo["emprise"]).bounds
    scan = Scan(BRUTS / date / (photo["id"] + ".tif"), mpp=1.0)
    scan.mpp = (emp[2] - emp[0]) / scan.W
    W, H = scan.W, scan.H
    rapport = {"photo": photo["id"]}

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
    emprise = (gcp_geo[:, 0].min() - 200, gcp_geo[:, 1].min() - 200,
               gcp_geo[:, 0].max() + 200, gcp_geo[:, 1].max() + 200)

    # 3. passes fines, de 2,4 m à 0,6 m
    rapport["passes"] = []
    for res, taille, recherche, tol, refs in PASSES:
        for nom in [r for r in refs if r in orthos]:
            p2, g2 = passe_fine(scan, gcp_photo, gcp_geo, orthos[nom], emprise, res, taille, recherche, tol)
            if len(p2) >= 40:
                break
        rapport["passes"].append({"res": res, "reference": nom, "points": int(len(p2))})
        if len(p2) < 15:
            continue   # passe sans résultat : on garde le placement précédent
        p2, g2 = lisse(p2, g2)
        # là où cette passe n'a rien trouvé (paysage changé), on garde les points précédents
        loin = cKDTree(p2).query(gcp_photo)[0] > 2500
        gcp_photo, gcp_geo = np.vstack([p2, gcp_photo[loin]]), np.vstack([g2, gcp_geo[loin]])

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
    rapport.update(controle(sortie, orthos["2022"]))
    rapport["statut"] = statut(rapport)
    return rapport


def statut(rapport):
    """Verdict à partir des mesures de contrôle (réappliqué à chaque lancement)."""
    if "carres_controle" not in rapport:
        return rapport["statut"]   # échec avant le contrôle
    bon = (rapport.get("mediane_m", 99) <= SEUIL_MEDIANE and rapport.get("p90_m", 99) <= SEUIL_P90
           and rapport["carres_controle"] >= MIN_CARRES and rapport["zones_controlees"] >= MIN_ZONES)
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
    with Pool(PROCESSUS) as pool:
        for r in pool.imap_unordered(traite, [(date, p) for p in choix]):
            print(json.dumps(r, ensure_ascii=False), flush=True)
    # rapport de la date = réunion des rapports des photos, avec les seuils actuels
    rapports = {}
    for f in sorted(dossier.glob("IGNF_*.json")):
        r = json.loads(f.read_text())
        r["statut"] = statut(r)
        rapports[r["photo"]] = r
    (dossier / "rapport.json").write_text(json.dumps(rapports, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
