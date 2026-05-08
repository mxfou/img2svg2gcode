#!/usr/bin/env python3
# -*- coding: utf-8 -*-


#!/usr/bin/python3

from scipy.spatial import cKDTree
import numpy as np
import gmic, os, math
from PIL import Image, ImageDraw
from autotrace import Bitmap

import svgpathtools, json
from pprint import pprint as pp
from gscrib import GCodeBuilder


def retourne_taille_image(fichier):
    with Image.open(fichier) as img:
        largeur, hauteur = img.size
    return largeur, hauteur


def cmyk_negatif_normalisation(fichier_entree, dossier_sortie_global, amplitude, rayon, lissage_moyen):
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
    g = gmic.Gmic()
    g.run("command gmic_stdlib.gmic")
    dossier_entree = "1-cmyk"
    dossier_sortie = "2-cut"
    chemin_entree = os.path.join(dossier, dossier_entree)
    chemin_sortie = os.path.join(dossier, dossier_sortie)
    if not dossier_sortie in os.listdir(dossier):
        os.mkdir(chemin_sortie)
    liste_fichiers = os.listdir(chemin_entree)
    liste_decalage = list(range(0, 100, math.ceil(100 / nb_images)))
    for fich in liste_fichiers:
        print(f"découpage de {fich} en {nb_images} images")
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
            g.run(cmd)
    del g


def graver(dossier, rayon):
    g = gmic.Gmic()
    g.run("command gmic_stdlib.gmic")
    dossier_entree = "2-cut"
    dossier_sortie = "3-engrave"
    chemin_entree = os.path.join(dossier, dossier_entree)
    chemin_sortie = os.path.join(dossier, dossier_sortie)
    if not dossier_sortie in os.listdir(dossier):
        os.mkdir(chemin_sortie)
    liste_fichiers = os.listdir(chemin_entree)
    for fich in liste_fichiers:
        commande = f"fx_engrave {rayon},50,0,18,40,5,0.1,0,10,1,0,0,0,1"
        fichier_entree = os.path.join(chemin_entree, fich)
        fichier_sortie = os.path.join(chemin_sortie, fich)
        cmd = f"input {fichier_entree} {commande} output {fichier_sortie}"
        print(f"gravure de {fich}")
        g.run(cmd)
    del g


def deformer(dossier):
    g = gmic.Gmic()
    g.run("command gmic_stdlib.gmic")
    dossier_entree = "3-engrave"
    dossier_sortie = "4-deform"
    chemin_entree = os.path.join(dossier, dossier_entree)
    chemin_sortie = os.path.join(dossier, dossier_sortie)
    if not dossier_sortie in os.listdir(dossier):
        os.mkdir(chemin_sortie)
    liste_fichiers = os.listdir(chemin_entree)
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
        print(f"déformation, dithering de {fich}")
        g.run(cmd)
    del g


def vectoriser(dossier):
    dossier_entree = "4-deform"
    dossier_sortie = "5-vector"
    chemin_entree = os.path.join(dossier, dossier_entree)
    chemin_sortie = os.path.join(dossier, dossier_sortie)
    if not dossier_sortie in os.listdir(dossier):
        os.mkdir(chemin_sortie)
    liste_fichiers = os.listdir(chemin_entree)
    for fich in liste_fichiers:
        fichier_entree = os.path.join(chemin_entree, fich)
        fich_out = fich.replace(".png", ".svg")
        fichier_sortie = os.path.join(chemin_sortie, fich_out)
        print(f"vectorisation de {fich}")
        image = Image.open(fichier_entree).convert("RGB")
        donnees_bitmap = np.array(image)
        bitmap = Bitmap(donnees_bitmap)
        vecteur = bitmap.trace(centerline=True)
        vecteur.save(fichier_sortie)


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
    facteur_echelle = float(facteur_echelle)
    taille_nettoyage = float(taille_nettoyage)
    taille_approximation = float(taille_approximation)
    dossier_entree = "5-vector"
    dossier_sortie = "6-resize"
    chemin_entree = os.path.join(dossier, dossier_entree)
    chemin_sortie = os.path.join(dossier, dossier_sortie)
    if not dossier_sortie in os.listdir(dossier):
        os.mkdir(chemin_sortie)
    liste_fichiers = os.listdir(chemin_entree)

    for couleur in ["black", "cyan", "magenta", "yellow"]:
        print("----------------------------")
        chemins_propres = []
        fich_out = couleur + ".svg"
        fichier_sortie = os.path.join(chemin_sortie, fich_out)
        attributs_svg = None

        # --- Lecture et nettoyage ---
        for fich in liste_fichiers:
            if couleur in fich:
                fichier_entree = os.path.join(chemin_entree, fich)
                chemins, attributs, attributs_svg = svgpathtools.svg2paths2(fichier_entree)
                print(f"{len(chemins[0])} chemins dans le fichier {fich}")
                attributs_svg["width"]  = str(float(attributs_svg["width"])  * facteur_echelle)
                attributs_svg["height"] = str(float(attributs_svg["height"]) * facteur_echelle)
                print(f"nettoyage de {fich}")
                for chemin in chemins:
                    for seg in chemin:
                        longueur_seg = seg.length() * facteur_echelle
                        if longueur_seg >= taille_nettoyage:
                            chemins_propres.append(seg)

        n = len(chemins_propres)
        print(f"{n} chemins dans le fichier {couleur} nettoyé")
        if n == 0:
            print(f"aucun chemin pour {couleur}, passage à la couleur suivante")
            continue

        print(f"optimisation de {couleur} avec k-d tree")

        # --- Initialisation du k-d tree ---
        # On indexe 2*n points (start + end de chaque segment).
        disponible = np.ones(n, dtype=bool)
        arbre, table_correspondance = _construire_arbre(chemins_propres, disponible)

        # Heuristique de reconstruction : quand la moitié des segments
        # ont été consommés, on reconstruit le k-d tree pour qu'il ne
        # contienne plus que les points encore utiles.
        seuil_reconstruction = max(10, n // 2)
        nb_supprimes_depuis_reconstruction = 0

        # --- Variables résultat ---
        sens = []
        meme_point = []
        total_chemin_inutile = 0.
        total_chemin_utile = 0.
        dernier = complex(0, 0)
        chemins_optimises = []

        step = max(1, n // 20)
        pc = list(range(0, n, step))

        for k in range(n):
            point_requete = np.array([dernier.real, dernier.imag])

            # Nombre de voisins demandés à chaque requête.
            # On en demande peu au début ; si tous sont déjà supprimés,
            # on double jusqu'à en trouver un valide.
            k_actuel = min(8, len(table_correspondance))

            id_segment = -1
            sens_normal = True
            distance = 0.

            while id_segment == -1:
                k_q = min(k_actuel, len(table_correspondance))
                if k_q == 0:
                    break
                distances, indices = arbre.query(point_requete, k=k_q)
                # Quand k=1, scipy renvoie un scalaire, on uniformise
                if np.isscalar(indices):
                    indices = [int(indices)]
                    distances = [float(distances)]

                for d, idx in zip(distances, indices):
                    if not np.isfinite(d):
                        continue  # scipy renvoie inf si moins de k points existent
                    id_seg, est_debut = table_correspondance[idx]
                    if disponible[id_seg]:
                        id_segment = id_seg
                        sens_normal = est_debut
                        distance = d
                        break

                if id_segment == -1:
                    # Tous les k_q candidats sont déjà supprimés : on élargit
                    if k_actuel >= len(table_correspondance):
                        break  # sécurité
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

            # Marquage du segment comme consommé (suppression "paresseuse")
            disponible[id_segment] = False
            nb_supprimes_depuis_reconstruction += 1

            # Reconstruction périodique du k-d tree
            if nb_supprimes_depuis_reconstruction >= seuil_reconstruction:
                arbre, table_correspondance = _construire_arbre(chemins_propres, disponible)
                nb_supprimes_depuis_reconstruction = 0

            if k in pc:
                print(f"{round(k / n * 100)}%", end="-")
        print("#")

        total_chemin_inutile += svgpathtools.Line(dernier, complex(0, 0)).length()
        print(f"{couleur} -> distance de travail : {total_chemin_utile}")
        print(f"{couleur} -> distance de déplacement : {total_chemin_inutile}")

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

        print(f"écriture du fichier {fichier_sortie}")
        print(f"{len(chemin_final)} chemins dans le fichier {couleur} après transformation des splines en segments")
        with open(fichier_sortie.replace(".svg", ".meme2"), 'w') as f:
            json.dump(meme_point_final, f)
        svgpathtools.wsvg(chemin_final, svg_attributes=attributs_svg, filename=fichier_sortie)


def generer_gcode(dossier, hauteur_deplacement, hauteur_ecriture):
    hauteur_deplacement = float(hauteur_deplacement)
    hauteur_ecriture = float(hauteur_ecriture)
    dossier_entree = "6-resize"
    dossier_sortie = "7-gcode"
    chemin_entree = os.path.join(dossier, dossier_entree)
    chemin_sortie = os.path.join(dossier, dossier_sortie)
    if not dossier_sortie in os.listdir(dossier):
        os.mkdir(chemin_sortie)
    liste_fichiers = os.listdir(chemin_entree)
    for fich in liste_fichiers:
        if ".svg" in fich:
            fichier_entree = os.path.join(chemin_entree, fich)
            fich_out = fich.replace(".svg", ".gcode")
            fichier_sortie = os.path.join(chemin_sortie, fich_out)
            with open(fichier_entree.replace(".svg", ".meme2"), 'r') as f:
                meme_point = json.load(f)
            print(f"génération du gcode à partir de {fich}")
            g = GCodeBuilder(output=fichier_sortie)
            g.set_axis(x=0, y=0, z=0)
            g.set_length_units("millimeters")
            g.set_distance_mode("absolute")
            g.set_resolution(0.1)
            chemins, attributs, attributs_svg = svgpathtools.svg2paths2(fichier_entree)
            nb_chemins = len(chemins)
            # protection contre la division par zéro
            step = max(1, nb_chemins // 20)
            pc = list(range(0, nb_chemins, step))
            print(f"{nb_chemins} chemins à traiter")
            cpt = 0
            for chemin in chemins:
                for seg in chemin:
                    if meme_point[cpt]:
                        pass
                    else:
                        g.rapid(z=hauteur_deplacement)
                        g.rapid(x=seg.start.real, y=seg.start.imag)
                        g.rapid(z=hauteur_ecriture)
                    g.move(x=seg.end.real, y=seg.end.imag)
                    cpt += 1
                if cpt in pc:
                    print(f"{round(cpt / nb_chemins * 100)}%", end="-")
            print("#")
            g.rapid(z=5)
            g.rapid(x=0, y=0)
            g.teardown()

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

    # --- Deuxième passe : rendu individuel par couleur ---
    images_par_couleur = {}
    for couleur, (traces, deplacements) in donnees_par_couleur.items():
        # Image RGBA avec fond transparent pour permettre la superposition
        img = Image.new("RGBA", (largeur_px, hauteur_px), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        rgb = couleurs_rgb[couleur]
        rgba = rgb + (255,)

        # Déplacements à vide (en pointillés gris)
        if afficher_deplacements:
            for (x0, y0, x1, y1) in deplacements:
                p0 = mm_vers_px(x0, y0)
                p1 = mm_vers_px(x1, y1)
                _ligne_pointillee(draw, p0, p1, (180, 180, 180, 128))

        # Traits réels
        for (x0, y0, x1, y1) in traces:
            p0 = mm_vers_px(x0, y0)
            p1 = mm_vers_px(x1, y1)
            draw.line([p0, p1], fill=rgba, width=int(epaisseur_trait))

        # Sauvegarde de l'image isolée pour cette couleur (sur fond blanc)
        img_finale = Image.new("RGB", (largeur_px, hauteur_px), fond)
        img_finale.paste(img, (0, 0), img)
        chemin_out = os.path.join(chemin_sortie, f"{couleur}.png")
        img_finale.save(chemin_out)
        print(f"  écrit : {chemin_out}")

        images_par_couleur[couleur] = img

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
    """Dessine une ligne pointillée entre p0 et p1."""
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
