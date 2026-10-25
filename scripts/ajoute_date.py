"""Traite une date de A à Z : photos IGN -> recalage -> photos de complément ->
mosaïque -> vérification finale -> contrôle -> tuiles.

Usage :
    python3 scripts/ajoute_date.py liste          # dates disponibles à l'IGN sur la zone
    python3 scripts/ajoute_date.py 1958-10-25     # traite cette date

Regardez ensuite l'image controle/<date>.jpg avant de publier.
"""
import json
import subprocess
import sys
from pathlib import Path

ICI = Path(__file__).resolve().parent


def etape(script, *args):
    print(f"\n=== {script} {' '.join(args)} ===", flush=True)
    subprocess.run([sys.executable, str(ICI / script), *args], check=True)


def infrarouge(date):
    """La date a-t-elle des photos infrarouges noir et blanc ?"""
    from commun import BRUTS
    return any(p.get("couleur") == "IR" for p in json.loads((BRUTS / date / "choix.json").read_text()))


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "liste":
        etape("photos.py", "liste")
        return
    date = sys.argv[1]
    etape("reference.py")                       # ortho 2022 (une seule fois)
    annee = int(date[:4])
    if annee <= 1995:                           # ortho historique de la même époque
        periode = "1950-1965" if annee <= 1965 else "1965-1980" if annee <= 1980 else "1980-1995"
        etape("reference.py", periode)
    etape("photos.py", date)
    if infrarouge(date):
        etape("reference.py", "irc")            # ortho 2022 infrarouge (une seule fois)
    etape("recale.py", date)
    etape("photos.py", date, "complete")   # d'autres photos là où il reste des trous
    etape("recale.py", date)               # (seules les nouvelles sont traitées)
    etape("mosaique.py", date)
    etape("verifie.py", date)              # efface les secteurs encore décalés
    etape("controle.py", date)
    etape("tuiles.py", date)


if __name__ == "__main__":
    main()
