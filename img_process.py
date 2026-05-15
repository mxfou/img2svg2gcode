#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
# bitmap2vector2gcode

Pipeline Python qui transforme une image bitmap en G-code multicouches CMJN.

```{include} ../README.md
```
"""

from scipy.spatial import cKDTree
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import gmic, os, math
from PIL import Image, ImageDraw
from autotrace import Bitmap

import svgpathtools, json
from pprint import pprint as pp

# gscrib décore ses méthodes avec @typeguard.typechecked, ce qui ajoute
# une validation de types à chaque appel. Sur l'étape 7 ce coût domine
# (>80% du temps total du pipeline sur une image moyenne). On neutralise
# le décorateur avant l'import de gscrib pour qu'il devienne un no-op.
import typeguard
typeguard.typechecked = lambda target=None, **kwargs: target if target is not None else (lambda f: f)

from gscrib import GCodeBuilder


# ---------------------------------------------------------------------------
# Helpers pour la parallélisation par process
# ---------------------------------------------------------------------------
# Chaque ProcessPoolExecutor relance ses workers comme des processus Python
# indépendants. Pour les étapes G'MIC, on initialise une instance + on charge
# le stdlib une seule fois par worker (sinon : ~3 Mo rechargés par tâche).

_gmic_worker = None
_GMIC_STDLIB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gmic_stdlib.gmic")


def _init_gmic_worker():
    """Initialiseur de ProcessPoolExecutor pour les étapes G'MIC."""
    global _gmic_worker
    _gmic_worker = gmic.Gmic()
    _gmic_worker.run(f"command {_GMIC_STDLIB_PATH}")


def _gmic_run_task(args):
    """Exécute une commande G'MIC dans le worker courant."""
    cmd, label = args
    _gmic_worker.run(cmd)
    if label:
        print(label)


def _nb_workers(n_taches):
    """Nombre de workers : limité au nombre de tâches et au nombre de CPU."""
    cpu = os.cpu_count() or 1
    return max(1, min(cpu, n_taches))


def _vectoriser_un_fichier(args):
    """Worker étape 5 : vectorise une image PNG en SVG via AutoTrace."""
    fichier_entree, fichier_sortie, etiquette = args
    image = Image.open(fichier_entree).convert("RGB")
    donnees_bitmap = np.array(image)
    bitmap = Bitmap(donnees_bitmap)
    vecteur = bitmap.trace(centerline=True)
    vecteur.save(fichier_sortie)
    print(f"vectorisé : {etiquette}")


def _rendre_couleur_etape8(args):
    """Worker étape 8 : rend une couleur (RGBA + sauvegarde PNG sur fond),
    retourne (couleur, image_rgba) pour la composition finale."""
    (couleur, rgb, traces, deplacements,
     largeur_px, hauteur_px, xmin, ymin, marge_mm, px_par_mm,
     epaisseur_trait, fond, afficher_deplacements, chemin_out) = args

    def mm_vers_px(x, y):
        return ((x - xmin + marge_mm) * px_par_mm,
                (y - ymin + marge_mm) * px_par_mm)

    img = Image.new("RGBA", (largeur_px, hauteur_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    rgba = rgb + (255,)

    if afficher_deplacements:
        for (x0, y0, x1, y1) in deplacements:
            _ligne_pointillee(draw, mm_vers_px(x0, y0), mm_vers_px(x1, y1),
                              (180, 180, 180, 128))

    for (x0, y0, x1, y1) in traces:
        draw.line([mm_vers_px(x0, y0), mm_vers_px(x1, y1)],
                  fill=rgba, width=int(epaisseur_trait))

    img_finale = Image.new("RGB", (largeur_px, hauteur_px), fond)
    img_finale.paste(img, (0, 0), img)
    img_finale.save(chemin_out)
    print(f"  écrit : {chemin_out}")
    return couleur, img


def _generer_gcode_un_fichier(args):
    """Worker étape 7 : génère un .gcode à partir d'un .svg + son .meme2."""
    fichier_entree, fichier_sortie, etiquette, hauteur_deplacement, hauteur_ecriture = args
    with open(fichier_entree.replace(".svg", ".meme2"), 'r') as f:
        meme_point = json.load(f)
    print(f"gcode : {etiquette} → début")
    g = GCodeBuilder(output=fichier_sortie)
    g.set_axis(x=0, y=0, z=0)
    g.set_length_units("millimeters")
    g.set_distance_mode("absolute")
    g.set_resolution(0.1)
    chemins, attributs, attributs_svg = svgpathtools.svg2paths2(fichier_entree)
    cpt = 0
    for chemin in chemins:
        for seg in chemin:
            if not meme_point[cpt]:
                g.rapid(z=hauteur_deplacement)
                g.rapid(x=seg.start.real, y=seg.start.imag)
                g.rapid(z=hauteur_ecriture)
            g.move(x=seg.end.real, y=seg.end.imag)
            cpt += 1
    g.rapid(z=5)
    g.rapid(x=0, y=0)
    g.teardown()
    print(f"gcode : {etiquette} → terminé")
    return etiquette


def _traiter_couleur_etape6(args):
    """Worker étape 6 : traite intégralement une couleur (lecture, nettoyage,
    optimisation k-d tree, linéarisation, écriture SVG + .sens/.meme/.meme2).
    Reprend strictement la même logique que la version séquentielle."""
    (couleur, fichiers_couleur, chemin_entree, chemin_sortie,
     facteur_echelle, taille_nettoyage, taille_approximation) = args

    print(f"--- couleur {couleur} : début ---")
    chemins_propres = []
    fich_out = couleur + ".svg"
    fichier_sortie = os.path.join(chemin_sortie, fich_out)
    attributs_svg = None

    # --- Lecture et nettoyage ---
    for fich in fichiers_couleur:
        fichier_entree = os.path.join(chemin_entree, fich)
        chemins, attributs, attributs_svg = svgpathtools.svg2paths2(fichier_entree)
        print(f"[{couleur}] {len(chemins[0])} chemins dans {fich}")
        attributs_svg["width"]  = str(float(attributs_svg["width"])  * facteur_echelle)
        attributs_svg["height"] = str(float(attributs_svg["height"]) * facteur_echelle)
        for chemin in chemins:
            for seg in chemin:
                longueur_seg = seg.length() * facteur_echelle
                if longueur_seg >= taille_nettoyage:
                    chemins_propres.append(seg)

    n = len(chemins_propres)
    print(f"[{couleur}] {n} chemins après nettoyage")
    if n == 0:
        print(f"[{couleur}] aucun chemin, ignoré")
        return couleur

    # --- Initialisation du k-d tree ---
    disponible = np.ones(n, dtype=bool)
    arbre, table_correspondance = _construire_arbre(chemins_propres, disponible)

    seuil_reconstruction = max(10, n // 2)
    nb_supprimes_depuis_reconstruction = 0

    sens = []
    meme_point = []
    total_chemin_inutile = 0.
    total_chemin_utile = 0.
    dernier = complex(0, 0)
    chemins_optimises = []

    for k in range(n):
        point_requete = np.array([dernier.real, dernier.imag])
        k_actuel = min(8, len(table_correspondance))

        id_segment = -1
        sens_normal = True
        distance = 0.

        while id_segment == -1:
            k_q = min(k_actuel, len(table_correspondance))
            if k_q == 0:
                break
            distances, indices = arbre.query(point_requete, k=k_q)
            if np.isscalar(indices):
                indices = [int(indices)]
                distances = [float(distances)]

            for d, idx in zip(distances, indices):
                if not np.isfinite(d):
                    continue
                id_seg, est_debut = table_correspondance[idx]
                if disponible[id_seg]:
                    id_segment = id_seg
                    sens_normal = est_debut
                    distance = d
                    break

            if id_segment == -1:
                if k_actuel >= len(table_correspondance):
                    break
                k_actuel = min(k_actuel * 2, len(table_correspondance))

        segment_choisi = chemins_propres[id_segment]
        if sens_normal:
            dernier = segment_choisi.end
        else:
            dernier = segment_choisi.start

        sens.append(bool(sens_normal))
        meme_point.append(bool(distance == 0.))
        total_chemin_utile += segment_choisi.length()
        chemins_optimises.append(segment_choisi)
        total_chemin_inutile += distance

        disponible[id_segment] = False
        nb_supprimes_depuis_reconstruction += 1

        if nb_supprimes_depuis_reconstruction >= seuil_reconstruction:
            arbre, table_correspondance = _construire_arbre(chemins_propres, disponible)
            nb_supprimes_depuis_reconstruction = 0

    total_chemin_inutile += svgpathtools.Line(dernier, complex(0, 0)).length()
    print(f"[{couleur}] travail : {total_chemin_utile:.0f} / déplacement : {total_chemin_inutile:.0f}")

    with open(fichier_sortie.replace(".svg", ".sens"), 'w') as f:
        json.dump(sens, f)
    with open(fichier_sortie.replace(".svg", ".meme"), 'w') as f:
        json.dump(meme_point, f)

    # --- Mise à l'échelle et linéarisation des Bézier ---
    chemin_final = []
    meme_point_final = []
    for k in range(len(chemins_optimises)):
        seg = chemins_optimises[k]
        longueur_seg = seg.length() * facteur_echelle
        sens_normal = sens[k]
        meme1 = meme_point[k]

        if type(seg) == svgpathtools.path.Line:
            if sens_normal:
                seg2 = svgpathtools.path.Line(seg.start * facteur_echelle, seg.end * facteur_echelle)
            else:
                seg2 = svgpathtools.path.Line(seg.end * facteur_echelle, seg.start * facteur_echelle)
            chemin_final.append(seg2)
            meme_point_final.append(meme1)

        if type(seg) == svgpathtools.path.CubicBezier:
            nb_points = max(2, int(longueur_seg // taille_approximation))
            offset = 1 / nb_points
            tab = [0.]
            for j in range(1, nb_points):
                tab.append(j * offset)
            tab.append(1.)
            points = []
            for t in tab:
                points.append(seg.point(t) * facteur_echelle)
            if not sens_normal:
                points.reverse()
            for p in range(len(points) - 1):
                seg2 = svgpathtools.path.Line(points[p], points[p + 1])
                chemin_final.append(seg2)
                if p == 0:
                    meme_point_final.append(bool(meme1))
                else:
                    meme_point_final.append(True)

    print(f"[{couleur}] écriture de {fichier_sortie} ({len(chemin_final)} segments après linéarisation)")
    with open(fichier_sortie.replace(".svg", ".meme2"), 'w') as f:
        json.dump(meme_point_final, f)
    svgpathtools.wsvg(chemin_final, svg_attributes=attributs_svg, filename=fichier_sortie)
    return couleur


def retourne_taille_image(fichier):
    """
    Retourne les dimensions en pixels d'un fichier image.

    Paramètres
    ----------
    fichier : str
        Chemin vers le fichier image (JPG, PNG, BMP, etc.).

    Retour
    ------
    (largeur, hauteur) : tuple d'entiers
        Largeur et hauteur de l'image en pixels.
    """
    with Image.open(fichier) as img:
        largeur, hauteur = img.size
    return largeur, hauteur


def cmyk_negatif_normalisation(fichier_entree, dossier_sortie_global, amplitude, rayon, lissage_moyen):
    """
    Étape 1 du pipeline : séparation CMJN, négatif et normalisation locale.

    Convertit l'image source de RGB en CMJN (Cyan, Magenta, Yellow, blacK),
    sépare les 4 canaux, applique un négatif sur chacun (afin de transformer
    les zones sombres en zones claires pour les étapes ultérieures de gravure),
    puis applique le filtre G'MIC `fx_normalize_local` qui équilibre les
    contrastes localement.

    Le résultat est écrit dans le sous-dossier `1-cmyk` du dossier de sortie,
    sous forme de 4 images PNG nommées `image_000000.png` à `image_000003.png`
    (correspondant respectivement à C, M, Y, K).

    Paramètres
    ----------
    fichier_entree : str
        Chemin vers l'image source à traiter.
    dossier_sortie_global : str
        Dossier racine où sera créé le sous-dossier `1-cmyk`.
    amplitude : float
        Amplitude de la normalisation locale (typiquement 0 à 60).
    rayon : int
        Rayon en pixels de la zone de normalisation (typiquement 1 à 64).
    lissage_moyen : float
        Lissage de la composante moyenne (typiquement 0 à 60).
    """
    g = gmic.Gmic()
    g.run("command gmic_stdlib.gmic")
    dossier_sortie = "1-cmyk"
    dossier_sortie_complet = os.path.join(dossier_sortie_global, dossier_sortie)
    if not dossier_sortie in os.listdir(dossier_sortie_global):
        os.mkdir(dossier_sortie_complet)
    commande = f"rgb2cmyk split c negate fx_normalize_local {amplitude},{rayon},27.12,{lissage_moyen},1,12"
    fichier_sortie = os.path.join(dossier_sortie_complet, "image.png")
    cmd = f"input {fichier_entree} {commande} output {fichier_sortie}"
    print("séparation des couleurs, négatif, normalisation")
    g.run(cmd)
    del g


def decouper(dossier, nb_images):
    """
    Étape 2 du pipeline : découpage de chaque canal en N niveaux d'intensité.

    Pour chaque image présente dans `1-cmyk`, génère plusieurs versions
    décalées en luminosité (par incréments de 100/nb_images %), puis re-clamp
    les valeurs dans [0, 255]. Cela revient à produire des "tranches"
    d'intensité comparables à des courbes de niveau de densité.

    Les fichiers sont aussi renommés à cette étape : les suffixes numériques
    `000000` à `000003` produits par G'MIC sont remplacés par les noms
    explicites de couleurs `cyan`, `magenta`, `yellow`, `black`.

    Le résultat est écrit dans le sous-dossier `2-cut`.

    Paramètres
    ----------
    dossier : str
        Dossier racine du projet (contenant déjà `1-cmyk`).
    nb_images : int
        Nombre de tranches d'intensité à générer par couleur (typiquement 2 à 10).
    """
    dossier_entree = "1-cmyk"
    dossier_sortie = "2-cut"
    chemin_entree = os.path.join(dossier, dossier_entree)
    chemin_sortie = os.path.join(dossier, dossier_sortie)
    if not dossier_sortie in os.listdir(dossier):
        os.mkdir(chemin_sortie)
    liste_fichiers = sorted(os.listdir(chemin_entree))
    liste_decalage = list(range(0, 100, math.ceil(100 / nb_images)))

    taches = []
    for fich in liste_fichiers:
        for decalage in liste_decalage:
            commande = f"add {decalage}% cut 0,255"
            fichier_entree = os.path.join(chemin_entree, fich)
            fich_out = (fich.replace("000000", "cyan")
                           .replace("000001", "magenta")
                           .replace("000002", "yellow")
                           .replace("000003", "black")
                           .replace(".png", f"_{decalage}.png"))
            fichier_sortie = os.path.join(chemin_sortie, fich_out)
            cmd = f"input {fichier_entree} {commande} output {fichier_sortie}"
            taches.append((cmd, f"découpé : {fich_out}"))

    print(f"découpage de {len(liste_fichiers)} canaux en {nb_images} couches sur {_nb_workers(len(taches))} workers")
    with ProcessPoolExecutor(max_workers=_nb_workers(len(taches)),
                             initializer=_init_gmic_worker) as pool:
        for _ in pool.map(_gmic_run_task, taches):
            pass


def graver(dossier, rayon):
    """
    Étape 3 du pipeline : application du filtre "gravure" à chaque image.

    Applique le filtre G'MIC `fx_engrave` à toutes les images du sous-dossier
    `2-cut`. Ce filtre simule une gravure au burin en transformant les zones
    sombres en hachures de traits parallèles, dont l'épaisseur dépend du
    paramètre `rayon`. Les autres paramètres du filtre (50, 0, 18, 40, 5, 0.1,
    0, 10, 1, 0, 0, 0, 1) sont fixés et correspondent aux valeurs trouvées
    expérimentalement pour un rendu équilibré.

    Le résultat est écrit dans le sous-dossier `3-engrave`.

    Paramètres
    ----------
    dossier : str
        Dossier racine du projet (contenant déjà `2-cut`).
    rayon : float
        Épaisseur des traits de gravure (typiquement 0 à 2).
    """
    dossier_entree = "2-cut"
    dossier_sortie = "3-engrave"
    chemin_entree = os.path.join(dossier, dossier_entree)
    chemin_sortie = os.path.join(dossier, dossier_sortie)
    if not dossier_sortie in os.listdir(dossier):
        os.mkdir(chemin_sortie)
    liste_fichiers = sorted(os.listdir(chemin_entree))

    taches = []
    for fich in liste_fichiers:
        commande = f"fx_engrave {rayon},50,0,18,40,5,0.1,0,10,1,0,0,0,1"
        fichier_entree = os.path.join(chemin_entree, fich)
        fichier_sortie = os.path.join(chemin_sortie, fich)
        cmd = f"input {fichier_entree} {commande} output {fichier_sortie}"
        taches.append((cmd, f"gravé : {fich}"))

    print(f"gravure de {len(taches)} fichiers sur {_nb_workers(len(taches))} workers")
    with ProcessPoolExecutor(max_workers=_nb_workers(len(taches)),
                             initializer=_init_gmic_worker) as pool:
        for _ in pool.map(_gmic_run_task, taches):
            pass


def deformer(dossier):
    """
    Étape 4 du pipeline : déformation légère et tramage (dithering noir et blanc).

    Pour chaque image du sous-dossier `3-engrave` :
    - applique une déformation dont l'amplitude dépend du suffixe numérique
      du fichier (0, 33, 66) : couches plus claires = plus déformées, ce qui
      apporte de la variété visuelle entre les tranches d'intensité ;
    - applique le filtre G'MIC `fx_ditheredbw` qui binarise l'image en
      utilisant un tramage stylisé.

    Le résultat est écrit dans le sous-dossier `4-deform`.

    Paramètres
    ----------
    dossier : str
        Dossier racine du projet (contenant déjà `3-engrave`).
    """
    dossier_entree = "3-engrave"
    dossier_sortie = "4-deform"
    chemin_entree = os.path.join(dossier, dossier_entree)
    chemin_sortie = os.path.join(dossier, dossier_sortie)
    if not dossier_sortie in os.listdir(dossier):
        os.mkdir(chemin_sortie)
    liste_fichiers = sorted(os.listdir(chemin_entree))

    taches = []
    for fich in liste_fichiers:
        numero = int(fich.split("_")[-1].split(".")[0])
        amplitude = 0
        if numero > 33:
            amplitude = 1
        if numero > 66:
            amplitude = 2
        commande = f"deform {amplitude} fx_ditheredbw -7.8,100,-9.4,0,0,0"
        fichier_entree = os.path.join(chemin_entree, fich)
        fichier_sortie = os.path.join(chemin_sortie, fich)
        cmd = f"input {fichier_entree} {commande} output {fichier_sortie}"
        taches.append((cmd, f"déformé/dithered : {fich}"))

    print(f"déformation + dithering de {len(taches)} fichiers sur {_nb_workers(len(taches))} workers")
    with ProcessPoolExecutor(max_workers=_nb_workers(len(taches)),
                             initializer=_init_gmic_worker) as pool:
        for _ in pool.map(_gmic_run_task, taches):
            pass


def vectoriser(dossier):
    """
    Étape 5 du pipeline : vectorisation des images binarisées.

    Convertit chaque PNG du sous-dossier `4-deform` en SVG en utilisant
    AutoTrace en mode `centerline`. Ce mode extrait les **lignes médianes**
    des traits binarisés (et non leur contour), ce qui est idéal pour un
    tracé au stylo : un trait épais devient une seule ligne suivant son
    axe, et non deux lignes le contournant.

    Le résultat est écrit dans le sous-dossier `5-vector`, avec un SVG par
    PNG d'entrée.

    Paramètres
    ----------
    dossier : str
        Dossier racine du projet (contenant déjà `4-deform`).
    """
    dossier_entree = "4-deform"
    dossier_sortie = "5-vector"
    chemin_entree = os.path.join(dossier, dossier_entree)
    chemin_sortie = os.path.join(dossier, dossier_sortie)
    if not dossier_sortie in os.listdir(dossier):
        os.mkdir(chemin_sortie)
    liste_fichiers = sorted(os.listdir(chemin_entree))

    taches = []
    for fich in liste_fichiers:
        fichier_entree = os.path.join(chemin_entree, fich)
        fich_out = fich.replace(".png", ".svg")
        fichier_sortie = os.path.join(chemin_sortie, fich_out)
        taches.append((fichier_entree, fichier_sortie, fich))

    print(f"vectorisation de {len(taches)} fichiers sur {_nb_workers(len(taches))} workers")
    with ProcessPoolExecutor(max_workers=_nb_workers(len(taches))) as pool:
        for _ in pool.map(_vectoriser_un_fichier, taches):
            pass


def _construire_arbre(chemins_propres, disponible):
    """
    Construit un k-d tree à partir des extrémités (start/end) des segments
    encore disponibles.

    Retourne (arbre, table_correspondance) où :
      - arbre : cKDTree contenant 2*n_dispo points 2D
      - table_correspondance[i] = (id_segment, est_debut)
        permet de retrouver à quel segment correspond le i-ème point indexé
        et si c'est son point de départ (True) ou de fin (False).
    """
    points = []
    table_correspondance = []
    for i in range(len(chemins_propres)):
        if disponible[i]:
            seg = chemins_propres[i]
            points.append([seg.start.real, seg.start.imag])
            table_correspondance.append((i, True))
            points.append([seg.end.real, seg.end.imag])
            table_correspondance.append((i, False))
    if not points:
        return None, []
    return cKDTree(np.array(points)), table_correspondance


def redimensionner(dossier, facteur_echelle, taille_nettoyage, taille_approximation):
    """
    Étape 6 du pipeline : redimensionnement, nettoyage, optimisation et linéarisation.

    C'est l'étape la plus complexe et calculatoire. Pour chacune des quatre
    couleurs (black, cyan, magenta, yellow), elle effectue les opérations
    suivantes :

    1. **Lecture et fusion** : tous les SVG correspondant à la couleur
       (issus des différentes tranches d'intensité de l'étape 5) sont lus
       et leurs segments concaténés.
    2. **Nettoyage** : les segments dont la longueur (après mise à l'échelle)
       est inférieure à `taille_nettoyage` sont supprimés. Cela élimine les
       artefacts de vectorisation tout en préservant les détails utiles.
    3. **Optimisation du parcours** : recherche du plus proche voisin
       (algorithme glouton) pour ordonner les segments de manière à minimiser
       les déplacements à vide. Utilise un k-d tree (`scipy.spatial.cKDTree`)
       avec suppression paresseuse et reconstruction périodique pour rester
       en O(n log n) au lieu de O(n²).
       Pour chaque segment, les deux extrémités (start et end) sont candidates,
       et le sens de parcours retenu est celui qui minimise la distance.
    4. **Linéarisation des courbes de Bézier** : les `CubicBezier` sont
       approximés par une suite de segments droits dont la longueur cible
       est `taille_approximation`.
    5. **Sauvegarde** : un SVG final par couleur est écrit dans `6-resize`,
       accompagné de trois fichiers JSON :
       - `.sens` : sens de parcours de chaque segment original ;
       - `.meme` : True si la fin du segment précédent coïncide avec le début
         du courant (= continuité parfaite, pas besoin de lever l'outil) ;
       - `.meme2` : version étendue après linéarisation des Bézier, utilisée
         par l'étape 7 pour décider quand lever/baisser l'outil.

    Paramètres
    ----------
    dossier : str
        Dossier racine du projet (contenant déjà `5-vector`).
    facteur_echelle : float
        Multiplicateur appliqué aux coordonnées : 1.0 conserve la taille
        en pixels d'origine, des valeurs plus petites/grandes redimensionnent
        proportionnellement.
    taille_nettoyage : float
        Longueur minimale (en mm une fois mis à l'échelle) en-dessous de
        laquelle un segment est jeté.
    taille_approximation : float
        Longueur cible (en mm) des segments droits utilisés pour approximer
        les courbes de Bézier.
    """
    facteur_echelle = float(facteur_echelle)
    taille_nettoyage = float(taille_nettoyage)
    taille_approximation = float(taille_approximation)
    dossier_entree = "5-vector"
    dossier_sortie = "6-resize"
    chemin_entree = os.path.join(dossier, dossier_entree)
    chemin_sortie = os.path.join(dossier, dossier_sortie)
    if not dossier_sortie in os.listdir(dossier):
        os.mkdir(chemin_sortie)
    liste_fichiers = sorted(os.listdir(chemin_entree))

    # Une tâche par couleur : on ne passe au worker que la liste des SVGs
    # qui le concernent, pour éviter du pickle inutile.
    taches = []
    for couleur in ["black", "cyan", "magenta", "yellow"]:
        fichiers_couleur = [f for f in liste_fichiers if couleur in f]
        taches.append((couleur, fichiers_couleur, chemin_entree, chemin_sortie,
                       facteur_echelle, taille_nettoyage, taille_approximation))

    print(f"redimensionnement de 4 couleurs sur {_nb_workers(len(taches))} workers")
    with ProcessPoolExecutor(max_workers=_nb_workers(len(taches))) as pool:
        for _ in pool.map(_traiter_couleur_etape6, taches):
            pass


def generer_gcode(dossier, hauteur_deplacement, hauteur_ecriture):
    """
    Étape 7 du pipeline : génération du G-code à partir des SVG optimisés.

    Pour chaque SVG du sous-dossier `6-resize`, génère un fichier `.gcode`
    contenant les instructions machine :

    - **Coordonnées absolues**, unités en **millimètres**, résolution 0.1 mm.
    - Pour chaque segment :
      - Si le fichier `.meme2` indique qu'il y a continuité avec le segment
        précédent (`True`), l'outil reste en position d'écriture et trace
        directement (`g.move`).
      - Sinon, l'outil est levé à `hauteur_deplacement` (mouvement rapide
        vers la nouvelle position de départ), puis abaissé à `hauteur_ecriture`,
        avant de tracer le segment.
    - À la fin du fichier, l'outil remonte à Z=5 et retourne à l'origine (0, 0).

    Cette logique exploite directement le travail d'optimisation de l'étape 6
    (regroupement par couleur, ordre des segments, marqueurs de continuité)
    pour minimiser le nombre de levages d'outil et réduire le temps machine.

    Paramètres
    ----------
    dossier : str
        Dossier racine du projet (contenant déjà `6-resize`).
    hauteur_deplacement : float
        Position Z (mm) lorsque l'outil se déplace à vide (typiquement 1 à 15).
    hauteur_ecriture : float
        Position Z (mm) lorsque l'outil trace (typiquement -10 à 0).
    """
    hauteur_deplacement = float(hauteur_deplacement)
    hauteur_ecriture = float(hauteur_ecriture)
    dossier_entree = "6-resize"
    dossier_sortie = "7-gcode"
    chemin_entree = os.path.join(dossier, dossier_entree)
    chemin_sortie = os.path.join(dossier, dossier_sortie)
    if not dossier_sortie in os.listdir(dossier):
        os.mkdir(chemin_sortie)
    liste_fichiers = sorted(os.listdir(chemin_entree))

    taches = []
    for fich in liste_fichiers:
        if ".svg" in fich:
            fichier_entree = os.path.join(chemin_entree, fich)
            fich_out = fich.replace(".svg", ".gcode")
            fichier_sortie = os.path.join(chemin_sortie, fich_out)
            taches.append((fichier_entree, fichier_sortie, fich,
                           hauteur_deplacement, hauteur_ecriture))

    print(f"génération G-code de {len(taches)} fichiers sur {_nb_workers(len(taches))} workers")
    with ProcessPoolExecutor(max_workers=_nb_workers(len(taches))) as pool:
        for _ in pool.map(_generer_gcode_un_fichier, taches):
            pass

def previsualiser_gcode(dossier, dpi=150, marge_mm=10,
                        epaisseur_trait=3.0, fond="white",
                        afficher_deplacements=False):
    """
    Génère une image PNG par fichier G-code + une image composite finale
    superposant les 4 couleurs CMJN.

    Paramètres
    ----------
    dossier : str
        Dossier racine du projet (celui qui contient 7-gcode).
    dpi : int
        Résolution de l'image générée. 150 dpi donne une bonne qualité.
    marge_mm : float
        Marge blanche autour du dessin, en millimètres.
    epaisseur_trait : float
        Épaisseur du trait dessiné, en pixels.
    fond : str
        Couleur de fond ("white", "black", ou code hex).
    afficher_deplacements : bool
        Si True, dessine aussi les déplacements à vide en pointillés gris
        (utile pour visualiser le parcours machine et l'efficacité de
        l'optimisation k-d tree).
    """
    

    dossier_entree = "7-gcode"
    dossier_sortie = "8-preview"
    chemin_entree = os.path.join(dossier, dossier_entree)
    chemin_sortie = os.path.join(dossier, dossier_sortie)
    if not dossier_sortie in os.listdir(dossier):
        os.mkdir(chemin_sortie)

    # Couleurs CMJN au format RGB pour le rendu
    couleurs_rgb = {
        "cyan":    (0, 174, 239),
        "magenta": (236, 0, 140),
        "yellow":  (255, 242, 0),
        "black":   (0, 0, 0),
    }

    # --- Première passe : parser tous les fichiers et calculer la bbox globale ---
    print("analyse des fichiers G-code...")
    donnees_par_couleur = {}  # couleur -> liste de (segments_traces, deplacements)
    bbox = [float("inf"), float("inf"), float("-inf"), float("-inf")]  # xmin, ymin, xmax, ymax

    liste_fichiers = sorted(os.listdir(chemin_entree))
    for fich in liste_fichiers:
        if not fich.endswith(".gcode"):
            continue
        couleur = fich.replace(".gcode", "")
        if couleur not in couleurs_rgb:
            print(f"  ⚠️  couleur inconnue ignorée : {fich}")
            continue

        chemin_fichier = os.path.join(chemin_entree, fich)
        traces, deplacements = _parser_gcode(chemin_fichier)
        donnees_par_couleur[couleur] = (traces, deplacements)
        print(f"  {fich} : {len(traces)} traits, {len(deplacements)} déplacements à vide")

        # Mise à jour de la bbox
        for (x0, y0, x1, y1) in traces:
            bbox[0] = min(bbox[0], x0, x1)
            bbox[1] = min(bbox[1], y0, y1)
            bbox[2] = max(bbox[2], x0, x1)
            bbox[3] = max(bbox[3], y0, y1)

    if bbox[0] == float("inf"):
        print("⚠️  aucun trait trouvé dans les G-code")
        return

    xmin, ymin, xmax, ymax = bbox
    largeur_mm = (xmax - xmin) + 2 * marge_mm
    hauteur_mm = (ymax - ymin) + 2 * marge_mm
    print(f"dimensions du dessin : {largeur_mm:.1f} × {hauteur_mm:.1f} mm")

    # Conversion mm -> pixels
    px_par_mm = dpi / 25.4
    largeur_px = int(largeur_mm * px_par_mm)
    hauteur_px = int(hauteur_mm * px_par_mm)
    print(f"dimensions de l'image : {largeur_px} × {hauteur_px} px ({dpi} dpi)")

    # def mm_vers_px(x, y):
    #     """Convertit des coordonnées mm machine en coordonnées pixel image.
    #     On inverse l'axe Y car en image Y va vers le bas."""
    #     px = (x - xmin + marge_mm) * px_par_mm
    #     py = (ymax - y + marge_mm) * px_par_mm
    #     return (px, py)

    def mm_vers_px(x, y):
            """Convertit des coordonnées mm machine en coordonnées pixel image.
            Le G-code conserve la convention SVG (Y vers le bas), donc PIL et
            le G-code utilisent la même orientation : pas d'inversion Y."""
            px = (x - xmin + marge_mm) * px_par_mm
            py = (y - ymin + marge_mm) * px_par_mm
            return (px, py)

    # --- Deuxième passe : rendu individuel par couleur (en parallèle) ---
    taches_rendu = []
    for couleur, (traces, deplacements) in donnees_par_couleur.items():
        chemin_out = os.path.join(chemin_sortie, f"{couleur}.png")
        taches_rendu.append((couleur, couleurs_rgb[couleur], traces, deplacements,
                             largeur_px, hauteur_px, xmin, ymin, marge_mm, px_par_mm,
                             epaisseur_trait, fond, afficher_deplacements, chemin_out))

    images_par_couleur = {}
    print(f"rendu de {len(taches_rendu)} couleurs sur {_nb_workers(len(taches_rendu))} workers")
    with ProcessPoolExecutor(max_workers=_nb_workers(len(taches_rendu))) as pool:
        for couleur, img_rgba in pool.map(_rendre_couleur_etape8, taches_rendu):
            images_par_couleur[couleur] = img_rgba

    # --- Troisième passe : composition CMJN ---
    if images_par_couleur:
        print("composition de l'image finale...")
        compose = Image.new("RGB", (largeur_px, hauteur_px), fond)

        # Ordre d'empilement : jaune en bas, magenta, cyan, noir au-dessus
        # (mimique l'impression CMJN classique)
        ordre = ["yellow", "magenta", "cyan", "black"]
        for couleur in ordre:
            if couleur in images_par_couleur:
                # Mode "multiply" simulé via composition manuelle
                _composer_multiply(compose, images_par_couleur[couleur])

        chemin_compose = os.path.join(chemin_sortie, "compose.png")
        compose.save(chemin_compose)
        print(f"  écrit : {chemin_compose}")

    print("✅ prévisualisation terminée")


def _parser_gcode(chemin_fichier):
    """
    Parse un fichier G-code et retourne deux listes :
      - traces : liste de (x0, y0, x1, y1) pour les mouvements en écriture
      - deplacements : liste de (x0, y0, x1, y1) pour les mouvements à vide

    Le critère "en écriture" est : Z <= 0 (les déplacements ont Z > 0).
    """
    traces = []
    deplacements = []

    x, y, z = 0.0, 0.0, 0.0
    seuil_ecriture = 0.0  # Z <= 0 = en train d'écrire

    with open(chemin_fichier, "r") as f:
        for ligne in f:
            # Suppression des commentaires
            if ";" in ligne:
                ligne = ligne.split(";")[0]
            ligne = ligne.strip()
            if not ligne:
                continue

            # On s'intéresse aux G0 (rapide) et G1 (linéaire)
            if not (ligne.startswith("G0") or ligne.startswith("G1") or
                    ligne.startswith("G00") or ligne.startswith("G01")):
                continue

            # Extraction des nouvelles coordonnées
            nx, ny, nz = x, y, z
            tokens = ligne.split()
            for tok in tokens:
                if tok.startswith("X"):
                    try:
                        nx = float(tok[1:])
                    except ValueError:
                        pass
                elif tok.startswith("Y"):
                    try:
                        ny = float(tok[1:])
                    except ValueError:
                        pass
                elif tok.startswith("Z"):
                    try:
                        nz = float(tok[1:])
                    except ValueError:
                        pass

            # Classification du mouvement
            deplacement_xy = (nx != x) or (ny != y)
            if deplacement_xy:
                # On est "en écriture" si on était ET on reste sous le seuil
                if z <= seuil_ecriture and nz <= seuil_ecriture:
                    traces.append((x, y, nx, ny))
                else:
                    deplacements.append((x, y, nx, ny))

            x, y, z = nx, ny, nz

    return traces, deplacements


def _ligne_pointillee(draw, p0, p1, couleur, longueur_tiret=4, espace=4):
    """
    Dessine une ligne pointillée entre p0 et p1 sur un objet ImageDraw.

    Utilisée pour visualiser les déplacements à vide de la machine dans la
    prévisualisation. Découpe la ligne en tirets de longueur `longueur_tiret`
    pixels, séparés par des intervalles de `espace` pixels.

    Paramètres
    ----------
    draw : PIL.ImageDraw.ImageDraw
        Contexte de dessin sur lequel tracer.
    p0, p1 : tuple (x, y)
        Coordonnées (en pixels) des extrémités de la ligne.
    couleur : tuple
        Couleur du trait au format RGB ou RGBA.
    longueur_tiret : int
        Longueur de chaque tiret en pixels.
    espace : int
        Longueur de l'espace entre deux tirets en pixels.
    """
    import math
    x0, y0 = p0
    x1, y1 = p1
    dx, dy = x1 - x0, y1 - y0
    distance = math.hypot(dx, dy)
    if distance == 0:
        return
    ux, uy = dx / distance, dy / distance
    pas = longueur_tiret + espace
    nb_tirets = int(distance / pas)
    for i in range(nb_tirets + 1):
        d0 = i * pas
        d1 = min(d0 + longueur_tiret, distance)
        a = (x0 + ux * d0, y0 + uy * d0)
        b = (x0 + ux * d1, y0 + uy * d1)
        draw.line([a, b], fill=couleur, width=1)


def _composer_multiply(image_fond, image_couleur):
    """
    Compose image_couleur (RGBA) sur image_fond (RGB) avec un mode 'multiply'
    approximatif : les couleurs s'assombrissent comme à l'impression CMJN.
    """
    import numpy as np
    fond = np.array(image_fond, dtype=np.float32) / 255.0
    couleur = np.array(image_couleur, dtype=np.float32) / 255.0
    alpha = couleur[..., 3:4]
    rgb_couleur = couleur[..., :3]
    # Multiply : zones non dessinées (alpha=0) doivent garder le fond
    # On mélange linéairement : résultat = fond * (1 - alpha + alpha * couleur_rgb)
    melange = fond * (1 - alpha) + fond * rgb_couleur * alpha
    melange = np.clip(melange * 255.0, 0, 255).astype(np.uint8)
    image_fond.paste(Image.fromarray(melange), (0, 0))
