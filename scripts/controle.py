"""Image de contrôle d'une date : avant (photos brutes) / après (mosaïque recalée).

Produit controle/<date>.csv (la liste des photos IGN originales utilisées, avec
leur verdict et leur lien de téléchargement direct à l'IGN) et controle/<date>.jpg avec :
- les photos brutes retenues (avant),
- la mosaïque recalée, l'ortho 2022 et un damier des deux (après) : si les
  routes et les crêtes se prolongent d'une case à l'autre, le recalage est bon,
- des gros plans sur des repères stables : ancienne image, 2022, et les deux
  superposées (magenta = 2022, vert = ancienne, gris = les deux coïncident),
- le bilan photo par photo (écart mesuré avec l'ortho 2022).

Usage : python3 scripts/controle.py 1958-10-25
"""
import csv
import json
import sys

import numpy as np
from osgeo import gdal
from PIL import Image, ImageDraw, ImageFont

from commun import BRUTS, CONTROLE, GEOPF, RECALES, REFERENCE, zone_merc

gdal.UseExceptions()

# Repères stables (mètres Web Mercator), choisis sur l'ortho 2022
REPERES = {
    "Lac": (62765, 5277202),
    "Grand Hôtel": (64265, 5276827),
    "Ligne de crête": (59865, 5275327),
    "Allées d'Étigny (Luchon)": (66008, 5279686),
}
LARGEUR_REPERE = 360   # mètres montrés autour de chaque repère

FOND = (24, 30, 36)
TEXTE = (235, 240, 245)
GRIS = (150, 162, 175)
VERT = (60, 190, 110)
ROUGE = (230, 70, 60)


def police(taille, gras=False):
    nom = "DejaVuSans-Bold.ttf" if gras else "DejaVuSans.ttf"
    for chemin in (f"/usr/share/fonts/truetype/dejavu/{nom}", nom):
        try:
            return ImageFont.truetype(chemin, taille)
        except OSError:
            pass
    return ImageFont.load_default()


def lire(fichier, x0, y0, x1, y1, largeur_px, gris=True):
    """Extrait une emprise d'un raster géoréférencé, à la taille voulue."""
    hauteur_px = int(round(largeur_px * (y1 - y0) / (x1 - x0)))
    ds = gdal.Warp("", str(fichier), format="MEM", outputBounds=(x0, y0, x1, y1),
                   width=largeur_px, height=hauteur_px, resampleAlg="average", dstNodata=0)
    a = ds.ReadAsArray()
    if a.ndim == 3:
        if a.shape[0] == 2:          # gris + alpha
            a = np.where(a[1] > 0, a[0], 0)
        elif gris:
            a = (0.3 * a[0] + 0.59 * a[1] + 0.11 * a[2]).astype(np.uint8)
        else:
            a = np.moveaxis(a[:3], 0, -1)
    return Image.fromarray(a.astype(np.uint8)).convert("RGB")


def damier(a, b, cases=8):
    a, b = np.array(a), np.array(b)
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    m = ((xx * cases // w) + (yy * cases // h)) % 2 == 0
    return Image.fromarray(np.where(m[..., None], a, b))


def main():
    date = sys.argv[1]
    annee = date[:4]
    dossier = RECALES / date
    rapports = json.loads((dossier / "rapport.json").read_text())
    choix = json.loads((BRUTS / date / "choix.json").read_text())
    mosaique = dossier / "mosaique.tif"
    ortho = REFERENCE / "ortho2022.tif"
    x0, y0, x1, y1 = zone_merc()

    L = 2400
    T, t, p = police(40, True), police(26, True), police(20)
    img = Image.new("RGB", (L, 4000), FOND)
    d = ImageDraw.Draw(img)
    y = 30
    ok = [r for r in rapports.values() if r.get("statut") == "ok"]
    mal = [r for r in rapports.values() if r.get("statut") != "ok"]
    d.text((40, y), f"Contrôle du recalage — {date}", font=T, fill=TEXTE)
    y += 60
    d.text((40, y), f"{len(choix)} photos IGN retenues · {len(ok)} bien recalées (publiées) · "
                    f"{len(mal)} écartées (mal recalées ou en échec)", font=p, fill=GRIS)
    y += 50

    # --- AVANT : photos brutes ---
    d.text((40, y), "AVANT : photos brutes (non géoréférencées)", font=t, fill=TEXTE)
    y += 45
    n = len(choix)
    par_ligne = 12
    v = (L - 80 - (par_ligne - 1) * 8) // par_ligne
    for i, ph in enumerate(sorted(choix, key=lambda q: q["id"])):
        ds = gdal.Open(str(BRUTS / date / (ph["id"] + ".tif")))
        a = ds.GetRasterBand(1).ReadAsArray(buf_xsize=v, buf_ysize=v)
        cx = 40 + (i % par_ligne) * (v + 8)
        cy = y + (i // par_ligne) * (v + 34)
        img.paste(Image.fromarray(a).convert("RGB"), (cx, cy))
        r = rapports.get(ph["id"], {})
        coul = VERT if r.get("statut") == "ok" else ROUGE
        d.rectangle([cx - 2, cy - 2, cx + v + 1, cy + v + 1], outline=coul, width=3)
        d.text((cx, cy + v + 4), ph["id"][-4:], font=p, fill=coul)
    y += ((n + par_ligne - 1) // par_ligne) * (v + 34) + 20

    # --- APRÈS : mosaïque / ortho 2022 / damier ---
    d.text((40, y), f"APRÈS : mosaïque {annee} recalée  |  ortho IGN 2022  |  damier des deux",
           font=t, fill=TEXTE)
    y += 45
    w3 = (L - 80 - 2 * 20) // 3
    m = lire(mosaique, x0, y0, x1, y1, w3)
    o = lire(ortho, x0, y0, x1, y1, w3, gris=False)
    dm = damier(lire(mosaique, x0, y0, x1, y1, w3), lire(ortho, x0, y0, x1, y1, w3))
    for k, im in enumerate((m, o, dm)):
        img.paste(im, (40 + k * (w3 + 20), y))
    # repères sur la mosaïque
    for nom, (rx, ry) in REPERES.items():
        px = 40 + (rx - x0) / (x1 - x0) * w3
        py = y + (y1 - ry) / (y1 - y0) * m.height
        d.ellipse([px - 7, py - 7, px + 7, py + 7], outline=ROUGE, width=3)
    y += m.height + 20

    # --- Gros plans sur les repères ---
    d.text((40, y), f"Gros plans : {annee}  |  2022  |  superposition (magenta = 2022, vert = {annee}, "
                    "gris = les deux coïncident)", font=t, fill=TEXTE)
    y += 45
    w = (L - 80 - 40) // 2            # deux repères par ligne
    v = (w - 2 * 8) // 3              # trois vignettes par repère
    for k, (nom, (rx, ry)) in enumerate(REPERES.items()):
        e = LARGEUR_REPERE / 2
        bx = 40 + (k % 2) * (w + 40)
        by = y + (k // 2) * (v + 44)
        ancien = lire(mosaique, rx - e, ry - e, rx + e, ry + e, v)
        recent = lire(ortho, rx - e, ry - e, rx + e, ry + e, v, gris=False)
        a = np.array(ancien)[:, :, 0]
        b = np.array(recent.convert("L"))
        sup = Image.fromarray(np.dstack([b, a, b]))
        for j, im in enumerate((ancien, recent, sup)):
            dd = ImageDraw.Draw(im)
            c = v // 2
            dd.line([c - 14, c, c + 14, c], fill=ROUGE, width=2)
            dd.line([c, c - 14, c, c + 14], fill=ROUGE, width=2)
            img.paste(im, (bx + j * (v + 8), by))
        d.text((bx, by + v + 6), nom, font=p, fill=TEXTE)
    y += 2 * (v + 44) + 10

    # --- Bilan photo par photo ---
    d.text((40, y), "Bilan par photo (écart restant avec l'ortho 2022, mesuré sur les éléments stables)",
           font=t, fill=TEXTE)
    y += 42
    lignes = []
    for r in sorted(rapports.values(), key=lambda q: q["photo"]):
        num = r["photo"][-4:]
        if "mediane_m" in r:
            s = (f"{num}  écart médian {r['mediane_m']:.1f} m · 90 % < {r['p90_m']:.1f} m · "
                 f"{r['carres_controle']} pts / {r['zones_controlees']} zones"
                 + (f" · {r['part_gardee']:.0%} gardé" if "part_gardee" in r else "")
                 + f" · {r['statut']}")
        else:
            s = f"{num}  {r['statut']} : {r.get('raison', 'contrôle impossible')}"
        lignes.append((s, VERT if r.get("statut") == "ok" else ROUGE))
    col = 3
    hl = 28
    par_col = (len(lignes) + col - 1) // col
    for i, (s, c) in enumerate(lignes):
        d.text((40 + (i // par_col) * ((L - 80) // col), y + (i % par_col) * hl), s, font=p, fill=c)
    y += par_col * hl + 40

    CONTROLE.mkdir(parents=True, exist_ok=True)
    missions = {p["id"]: p["mission"] for p in choix}
    with open(CONTROLE / f"{date}.csv", "w", newline="", encoding="utf-8") as fc:
        w = csv.writer(fc)
        w.writerow(["photo", "verdict", "ecart_median_m", "ecart_90pc_m", "points_controle",
                    "zones_controlees", "telechargement_ign"])
        for r in sorted(rapports.values(), key=lambda q: q["photo"]):
            w.writerow([r["photo"], r["statut"], r.get("mediane_m", ""), r.get("p90_m", ""),
                        r.get("carres_controle", ""), r.get("zones_controlees", ""),
                        f"{GEOPF}/telechargement/download/pva/{missions.get(r['photo'], '')}/{r['photo']}.tif"])
    sortie = CONTROLE / f"{date}.jpg"
    img.crop((0, 0, L, y)).save(sortie, quality=85)
    print("OK :", sortie)


if __name__ == "__main__":
    main()
