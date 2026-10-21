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
   comparés à l'ortho 2022 jusqu'au bord de la photo. Seuls les secteurs à
   moins de 160 m d'un carré confirmé bien placé (écart < 20 m) vont dans la
   mosaïque ; les autres restent vides.

Puis, pour la date entière, une **vérification finale** compare la mosaïque
terminée à l'ortho 2022 : un secteur décalé de plus de 60 m (confirmé par ses
voisins) est effacé. C'est un filet de sécurité indépendant du contrôle de
chaque photo.

## Contenu du dépôt

| Dossier / fichier   | Rôle |
|---------------------|------|
| `index.html`        | le site (carte Leaflet, curseur des années, comparaison) |
| `annees.json`       | la liste des dates recalées (prévues ou publiées) |
| `tiles/`            | les tuiles des années recalées (un dossier par date) |
| `controle/`         | une image de contrôle par date recalée |
| `scripts/`          | les scripts de traitement (Python) |
| `requirements.txt`  | les bibliothèques Python nécessaires |

Images © IGN – Géoplateforme (licence ouverte Etalab 2.0).
