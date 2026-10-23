# Superbagnères, remonter le temps

Une carte pour voir la station de Superbagnères et la ville de Luchon (Pyrénées)
changer au fil des années : faites glisser le curseur des années, ou comparez
deux années de votre choix avec le rideau (2025 par défaut).

**Le site :** https://p69gp84gk7-arch.github.io/5n0p4k-3d1t0r/

## D'où viennent les images ?

- **Les couches IGN déjà prêtes** (1950–65, 1965–80, 2002 … 2025) sont lues
  directement sur la Géoplateforme de l'IGN.
- **Les années « recalées »** (par exemple 1958) sont fabriquées ici à partir des
  photos aériennes brutes de l'IGN (site « Remonter le temps »). Ces photos ne
  sont pas placées sur la carte au départ : les scripts du dossier `scripts/`
  les placent automatiquement en les comparant à l'ortho IGN 2022, puis les
  découpent en petites images (« tuiles ») rangées dans `tiles/<date>/`.
- **Les photos brutes** (fichiers TIFF, très lourds) ne sont **pas** dans le
  dépôt : elles sont publiées à part, dans la Release GitHub « originaux ».

## Ajouter une nouvelle année

Il faut un ordinateur avec Python 3 et GDAL (ou une session Claude Code, qui
peut tout faire à votre place : demandez-lui « ajoute l'année 1965 »).

1. **Installer les outils** (une seule fois) :

   ```
   sudo apt install gdal-bin python3-gdal
   pip install -r requirements.txt
   ```

2. **Voir les dates disponibles** à l'IGN sur la zone :

   ```
   python3 scripts/ajoute_date.py liste
   ```

   Chaque ligne donne une date de prise de vue, par exemple `1965-09-23`.

3. **Lancer le traitement** de la date choisie :

   ```
   python3 scripts/ajoute_date.py 1965-09-23
   ```

   Comptez environ 1 à 2 minutes par photo. Le script :
   - choisit et télécharge les photos IGN utiles (dossier `travail/`, jamais
     envoyé sur GitHub),
   - recale chaque photo sur l'ortho 2022,
   - assemble les photos en une seule image (mosaïque),
   - revérifie la mosaïque finie et efface les secteurs encore décalés,
   - fabrique l'image de contrôle `controle/1965-09-23.jpg`,
   - découpe les tuiles dans `tiles/1965-09-23/` et ajoute la date à la liste
     `annees.json` que lit le site (le site n'affiche une date que si son
     dossier de tuiles est publié).

4. **Vérifier l'image de contrôle** `controle/<date>.jpg` :
   - dans le **damier**, les routes, crêtes et bords de forêt doivent se
     prolonger sans cassure d'une case à l'autre ;
   - dans les **gros plans**, la croix rouge doit tomber au même endroit sur
     l'image ancienne et sur 2022 ;
   - les photos **encadrées en rouge** ont été jugées mal recalées : elles ne
     sont pas utilisées (la zone qu'elles couvraient peut rester vide).

5. **Vérifier la taille du site** : le script affiche la taille totale du
   dossier `tiles/`. Le site complet doit rester **sous 1 Go**.

6. **Publier** : créer une branche, y mettre les nouveaux fichiers
   (`tiles/<date>/`, `controle/<date>.jpg`, et `annees.json` s'il a changé), ouvrir une
   pull request, regarder l'image de contrôle, puis fusionner. Le site se met à
   jour tout seul quelques minutes après.

7. **Archiver les photos brutes** utilisées dans la Release « originaux » (sur
   GitHub : *Releases* → *originaux* → *Edit* → déposer les fichiers `.tif` du
   dossier `travail/bruts/<date>/`).

## Comment marche le recalage ?

Pour chaque photo :

1. **Départ** : l'IGN publie l'emprise approximative de chaque photo (ses 4 coins
   et le milieu de ses côtés), souvent décalée de 100 à 300 m.
2. **Correction globale** : la photo entière est déplacée, tournée et zoomée
   jusqu'à coller au mieux à l'ortho IGN historique de la même époque (même
   forêt, mêmes routes) ou, à défaut, à l'ortho 2022. Les repères stables comptent
   ensemble : le lac, le Grand Hôtel, les lignes de crête, le rebord du plateau,
   les rues et les allées d'Étigny à Luchon.
3. **Placement fin** : des centaines de petits carrés de terrain sont comparés,
   de plus en plus finement (2,4 m, puis 1,2 m, puis 0,6 m sur l'ortho 2022).
   Les carrés où le paysage a changé (forêt, pistes, bâtiments neufs) ne
   ressemblent à rien et sont écartés automatiquement.
4. **Déformation** avec GDAL : la photo est « étirée » pour passer par tous ces
   points (cela corrige les déformations dues au relief).
5. **Contrôle** : on remesure l'écart entre la photo recalée et l'ortho 2022
   sur les éléments qui n'ont pas changé. Une photo est acceptée si l'écart
   médian est inférieur à 4 m, si 90 % des points sont à moins de 10 m, et si
   ces points sont bien répartis sur la photo. Sinon, elle est signalée et
   n'est pas publiée.
6. **Contrôle secteur par secteur** : la photo est découpée en carrés de 250 m,
   comparés à l'ortho 2022. Une photo dont trop de secteurs sont décalés est
   écartée.

Puis, pour la date entière, on assemble **peu de photos, les meilleures** : le
plus petit nombre de photos qui couvrent toute la station de Superbagnères et
Luchon, en préférant les plus détaillées. Là où elles se recouvrent, la mieux
classée l'emporte (une seule photo par endroit, juste un fondu au bord), et leurs
teintes sont accordées entre elles. Passent en premier : les photos placées à la
main (près de leurs points), puis celles choisies dans `placer/priorites.json`
(par exemple `{"1996-07-19": ["1587"]}`). Ailleurs, le site montre la carte IGN.
Une **vérification finale** encadre en rouge, sur l'image de contrôle, les
secteurs qui semblent décalés de plus de 60 m.

Attention : là où le paysage a beaucoup changé (parkings, bâtiments neufs), le
placement automatique peut se tromper de 100 à 200 m sans que ses contrôles le
voient. Pour la station, le placement à la main est la méthode sûre.

## Placer une photo à la main

Quand le placement automatique d'une année est raté ou incomplet (par exemple la
station en 1993, où parkings et bâtiments ont trop changé), on peut placer une
photo soi-même avec la page **Placer une photo à la main** (lien en haut du site,
ou `placer.html`) :

1. choisir la date et la photo (les aperçus viennent de l'IGN) ;
2. cliquer un endroit précis sur la photo, puis le même endroit sur la carte
   d'aujourd'hui, une dizaine de fois, bien réparti ;
3. vérifier avec « Voir la photo posée sur la carte » ;
4. « Terminer et copier les points », puis coller le texte dans la conversation
   avec Claude.

La page ne modifie rien par elle-même. Les points reçus sont enregistrés dans
`placer/points/<date>/<photo>.json` ; au recalage suivant de cette date, la photo
part de ces points (au lieu de l'emprise IGN) et passe devant les autres dans
la mosaïque. Les aperçus sont
refaits avec `python3 scripts/apercus.py`.

## Contenu du dépôt

| Dossier / fichier   | Rôle |
|---------------------|------|
| `index.html`        | le site (carte Leaflet, curseur des années, comparaison) |
| `annees.json`       | la liste des dates recalées (prévues ou publiées) |
| `tiles/`            | les tuiles des années recalées (un dossier par date) |
| `controle/`         | une image de contrôle par date recalée |
| `placer.html`       | la page pour placer une photo à la main |
| `placer/`           | aperçus des photos IGN, liste des photos, points placés à la main |
| `scripts/`          | les scripts de traitement (Python) |
| `requirements.txt`  | les bibliothèques Python nécessaires |

Images © IGN – Géoplateforme (licence ouverte Etalab 2.0).
