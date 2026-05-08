#!/usr/bin/env python3
# -*- coding: utf-8 -*-


#!/usr/bin/python3

from scipy.spatial import cKDTree
import numpy as np
import gmic, os, math
from PIL import Image
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