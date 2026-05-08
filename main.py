#!/usr/bin/env python3
# -*- coding: utf-8 -*-


from guizero import App, Text, PushButton, TitleBox, TextBox, Slider
import img_process

boites_texte = {}
boites_titre = {}
curseurs = {}
valeurs_defaut_curseurs = {}
textes = {}
boutons = {}


def reinitialiser_curseurs(arg):
    if arg == "reset_all":
        for k in curseurs:
            curseurs[k].value = valeurs_defaut_curseurs[k]
    else:
        for k in curseurs:
            if arg in k:
                curseurs[k].value = valeurs_defaut_curseurs[k]


def choisir_fichier(arg):
    fichier = app.select_file(filetypes=[["All files", "*.*"],
                                         ["image-jpeg", "*.jpg"],
                                         ["image-png", "*.png"],
                                         ["image-bmp", "*.bmp"]])
    textes[arg].clear()
    textes[arg].append(fichier)
    if arg == "fichiers_fichier_entree":
        textes["fichiers_largeur_image"].clear()
        textes["fichiers_hauteur_image"].clear()
        largeur, hauteur = img_process.retourne_taille_image(fichier)
        textes["fichiers_largeur_image"].append(largeur)
        textes["fichiers_hauteur_image"].append(hauteur)
        boites_texte["redimensionner_largeur"].value = largeur
        boites_texte["redimensionner_hauteur"].value = hauteur
        boites_texte["redimensionner_facteur_echelle"].value = 1


def choisir_dossier(arg):
    textes[arg].clear()
    dossier = app.select_folder()
    textes[arg].append(dossier)


def changement_echelle():
    boites_texte["redimensionner_largeur"].value = (
        float(textes["fichiers_largeur_image"].value)
        * float(boites_texte["redimensionner_facteur_echelle"].value)
    )
    boites_texte["redimensionner_hauteur"].value = (
        float(textes["fichiers_hauteur_image"].value)
        * float(boites_texte["redimensionner_facteur_echelle"].value)
    )


def changement_largeur():
    boites_texte["redimensionner_facteur_echelle"].value = (
        float(boites_texte["redimensionner_largeur"].value)
        / float(textes["fichiers_largeur_image"].value)
    )
    changement_echelle()


def changement_hauteur():
    boites_texte["redimensionner_facteur_echelle"].value = (
        float(boites_texte["redimensionner_hauteur"].value)
        / float(textes["fichiers_hauteur_image"].value)
    )
    changement_echelle()


def executer(arg):
    fichier_entree = textes["fichiers_fichier_entree"].value
    dossier_sortie = textes["fichiers_dossier_sortie"].value
    norm_amplitude = curseurs["norm_amplitude"].value
    norm_rayon = curseurs["norm_rayon"].value
    norm_lissage_moyen = curseurs["norm_lissage"].value
    decouper_nb_images = curseurs["decouper_nombre"].value
    graver_rayon = curseurs["graver_rayon"].value
    redimensionner_facteur_echelle = boites_texte["redimensionner_facteur_echelle"].value
    redimensionner_taille_nettoyage = curseurs["redimensionner_taille_nettoyage"].value
    redimensionner_taille_approximation = curseurs["redimensionner_taille_approximation"].value
    gcode_hauteur_deplacement = curseurs["gcode_hauteur_deplacement"].value
    gcode_hauteur_ecriture = curseurs["gcode_hauteur_ecriture"].value

    if arg == "execute_all":
        img_process.cmyk_negatif_normalisation(fichier_entree, dossier_sortie,
                                               norm_amplitude, norm_rayon, norm_lissage_moyen)
        img_process.decouper(dossier_sortie, decouper_nb_images)
        img_process.graver(dossier_sortie, graver_rayon)
        img_process.deformer(dossier_sortie)
        img_process.vectoriser(dossier_sortie)
        img_process.redimensionner(dossier_sortie, redimensionner_facteur_echelle,
                                   redimensionner_taille_nettoyage,
                                   redimensionner_taille_approximation)
        img_process.generer_gcode(dossier_sortie, gcode_hauteur_deplacement,
                                  gcode_hauteur_ecriture)
    elif "norm" in arg:
        img_process.cmyk_negatif_normalisation(fichier_entree, dossier_sortie,
                                               norm_amplitude, norm_rayon, norm_lissage_moyen)
    elif "decouper" in arg:
        img_process.decouper(dossier_sortie, decouper_nb_images)
    elif "graver" in arg:
        img_process.graver(dossier_sortie, graver_rayon)
    elif "deformer" in arg:
        img_process.deformer(dossier_sortie)
    elif "vectoriser" in arg:
        img_process.vectoriser(dossier_sortie)
    elif "redimensionner" in arg:
        img_process.redimensionner(dossier_sortie, redimensionner_facteur_echelle,
                                   redimensionner_taille_nettoyage,
                                   redimensionner_taille_approximation)
    elif "gcode" in arg:
        img_process.generer_gcode(dossier_sortie, gcode_hauteur_deplacement,
                                  gcode_hauteur_ecriture)
    elif "previsualiser" in arg:
        img_process.previsualiser_gcode(dossier_sortie)


app = App(title="bitmap2vector2gcode", layout="grid", height=1100, width=1100)

# ---------------------- Fichiers I/O ----------------------
boites_titre["fichiers"] = TitleBox(app, layout="grid", text="fichiers (E/S)", grid=[0, 0])
textes["fichiers_fichier_entree"] = Text(boites_titre["fichiers"],
                                         text="fichier à traiter", grid=[0, 0])
boutons["fichiers_fichier_entree"] = PushButton(boites_titre["fichiers"],
                                                text="choisir le fichier", grid=[1, 0],
                                                command=choisir_fichier,
                                                args=["fichiers_fichier_entree"])
textes["fichiers_largeur"] = Text(boites_titre["fichiers"], text="largeur", grid=[2, 0])
textes["fichiers_largeur_image"] = Text(boites_titre["fichiers"],
                                        text="largeur de l'image", grid=[3, 0])
textes["fichiers_hauteur"] = Text(boites_titre["fichiers"], text="hauteur", grid=[2, 1])
textes["fichiers_hauteur_image"] = Text(boites_titre["fichiers"],
                                        text="hauteur de l'image", grid=[3, 1])
textes["fichiers_dossier_sortie"] = Text(boites_titre["fichiers"],
                                         text="dossier de destination", grid=[0, 1])
boutons["fichiers_dossier_sortie"] = PushButton(boites_titre["fichiers"],
                                                text="choisir le dossier", grid=[1, 1],
                                                command=choisir_dossier,
                                                args=["fichiers_dossier_sortie"])

boutons["reset_all"] = PushButton(app, text="tous les paramètres par défaut",
                                  grid=[1, 0], command=reinitialiser_curseurs,
                                  args=["reset_all"])
boutons["execute_all"] = PushButton(app, text="exécute tout", grid=[2, 0],
                                    command=executer, args=["execute_all"])

# ---------------------- Normalisation CMYK ----------------------
boites_titre["norm"] = TitleBox(app, layout="grid",
                                text="découpage CMYK + négatif + normalisation", grid=[0, 1])
textes["norm_amplitude"] = Text(boites_titre["norm"], text="amplitude", grid=[0, 0])
curseurs["norm_amplitude"] = Slider(boites_titre["norm"], start=0, end=60,
                                    grid=[1, 0], step=0.01, width=300)
valeurs_defaut_curseurs["norm_amplitude"] = 4.32
textes["norm_rayon"] = Text(boites_titre["norm"], text="rayon", grid=[0, 1])
curseurs["norm_rayon"] = Slider(boites_titre["norm"], start=1, end=64,
                                grid=[1, 1], step=1, width=300)
valeurs_defaut_curseurs["norm_rayon"] = 11
textes["norm_lissage"] = Text(boites_titre["norm"], text="lissage moyen", grid=[0, 2])
curseurs["norm_lissage"] = Slider(boites_titre["norm"], start=0, end=60,
                                  grid=[1, 2], step=0.01, width=300)
valeurs_defaut_curseurs["norm_lissage"] = 8.68
boutons["reset_norm"] = PushButton(app, text="paramètres de normalisation par défaut",
                                   grid=[1, 1], command=reinitialiser_curseurs,
                                   args=["norm_"])
boutons["execute_norm"] = PushButton(app, text="exécute", grid=[2, 1],
                                     command=executer, args=["norm_"])

# ---------------------- Découpage par intensité ----------------------
boites_titre["decouper"] = TitleBox(app, layout="grid",
                                    text="découpage en fonction de l'intensité", grid=[0, 2])
textes["decouper_nombre"] = Text(boites_titre["decouper"],
                                 text="nombre d'images par couleur", grid=[0, 1])
curseurs["decouper_nombre"] = Slider(boites_titre["decouper"], start=2, end=10,
                                     grid=[1, 1], step=1, width=300)
valeurs_defaut_curseurs["decouper_nombre"] = 5
boutons["reset_decouper"] = PushButton(app, text="paramètres de découpage par défaut",
                                       grid=[1, 2], command=reinitialiser_curseurs,
                                       args=["decouper_"])
boutons["execute_decouper"] = PushButton(app, text="exécute", grid=[2, 2],
                                         command=executer, args=["decouper_"])

# ---------------------- Gravure ----------------------
boites_titre["graver"] = TitleBox(app, layout="grid", text='filtre "gravure"', grid=[0, 3])
textes["graver_rayon"] = Text(boites_titre["graver"],
                              text="épaisseur des traits", grid=[0, 1])
curseurs["graver_rayon"] = Slider(boites_titre["graver"], start=0, end=2,
                                  grid=[1, 1], step=0.01, width=300)
valeurs_defaut_curseurs["graver_rayon"] = 0.48
boutons["reset_graver"] = PushButton(app, text="paramètres de gravure par défaut",
                                     grid=[1, 3], command=reinitialiser_curseurs,
                                     args=["graver_"])
boutons["execute_graver"] = PushButton(app, text="exécute", grid=[2, 3],
                                       command=executer, args=["graver_"])

# ---------------------- Déformation + dithering ----------------------
boites_titre["deformer"] = TitleBox(app, layout="grid",
                                    text='déformation + dithering', grid=[0, 4])
textes["deformer_dossier_entree"] = Text(boites_titre["deformer"],
                                         text="déformation", grid=[0, 0])
boutons["execute_deformer"] = PushButton(app, text="exécute", grid=[2, 4],
                                         command=executer, args=["deformer_"])

# ---------------------- Vectorisation ----------------------
boites_titre["vectoriser"] = TitleBox(app, layout="grid",
                                      text='vectorisation (autotrace -centerline)',
                                      grid=[0, 5])
textes["vectoriser_dossier_entree"] = Text(boites_titre["vectoriser"],
                                           text="vectorisation", grid=[0, 0])
boutons["execute_vectoriser"] = PushButton(app, text="exécute", grid=[2, 5],
                                           command=executer, args=["vectoriser_"])

# ---------------------- Redimensionnement / nettoyage / approximation ----------------------
boites_titre["redimensionner"] = TitleBox(app, layout="grid",
                                          text='redimensionner + nettoyer + grouper + approximer',
                                          grid=[0, 6])
textes["redimensionner_taille_finale"] = Text(boites_titre["redimensionner"],
                                              text="taille finale du dessin (mm)",
                                              grid=[0, 0])
textes["redimensionner_largeur"] = Text(boites_titre["redimensionner"],
                                        text="largeur", grid=[0, 1])
boites_texte["redimensionner_largeur"] = TextBox(boites_titre["redimensionner"],
                                                 grid=[1, 1], command=changement_largeur)
textes["redimensionner_hauteur"] = Text(boites_titre["redimensionner"],
                                        text="hauteur", grid=[0, 2])
boites_texte["redimensionner_hauteur"] = TextBox(boites_titre["redimensionner"],
                                                 grid=[1, 2], command=changement_hauteur)
textes["redimensionner_facteur_echelle"] = Text(boites_titre["redimensionner"],
                                                text="facteur d'échelle", grid=[0, 3])
boites_texte["redimensionner_facteur_echelle"] = TextBox(boites_titre["redimensionner"],
                                                         grid=[1, 3],
                                                         command=changement_echelle)
textes["redimensionner_nettoyer"] = Text(boites_titre["redimensionner"],
                                         text="enlève les traits trop petits "
                                              "et regroupe les fichiers par couleur",
                                         grid=[0, 4])
textes["redimensionner_taille_nettoyage"] = Text(boites_titre["redimensionner"],
                                                 text="longueur min des traits (mm)",
                                                 grid=[0, 5])
curseurs["redimensionner_taille_nettoyage"] = Slider(boites_titre["redimensionner"],
                                                     start=1, end=50, grid=[1, 5],
                                                     step=0.1, width=300)
valeurs_defaut_curseurs["redimensionner_taille_nettoyage"] = 5
textes["redimensionner_approximer"] = Text(boites_titre["redimensionner"],
                                           text="découpage des splines en lignes",
                                           grid=[0, 6])
textes["redimensionner_taille_approximation"] = Text(boites_titre["redimensionner"],
                                                     text="longueur des segments\n"
                                                          "d'approximation des splines (mm)",
                                                     grid=[0, 7])
curseurs["redimensionner_taille_approximation"] = Slider(boites_titre["redimensionner"],
                                                         start=0.01, end=2, grid=[1, 7],
                                                         step=0.01, width=300)
valeurs_defaut_curseurs["redimensionner_taille_approximation"] = 0.5
boutons["reset_redimensionner"] = PushButton(app,
                                             text="paramètres de redimensionnement par défaut",
                                             grid=[1, 6],
                                             command=reinitialiser_curseurs,
                                             args=["redimensionner_"])
boutons["execute_redimensionner"] = PushButton(app, text="exécute", grid=[2, 6],
                                               command=executer,
                                               args=["redimensionner_"])

# ---------------------- Génération G-code ----------------------
boites_titre["gcode"] = TitleBox(app, layout="grid", text="création du gcode", grid=[0, 9])
textes["gcode_hauteur_deplacement"] = Text(boites_titre["gcode"],
                                           text="hauteur de déplacement à vide (mm)",
                                           grid=[0, 0])
curseurs["gcode_hauteur_deplacement"] = Slider(boites_titre["gcode"], start=1, end=15,
                                               grid=[1, 0], step=1, width=300)
valeurs_defaut_curseurs["gcode_hauteur_deplacement"] = 3
textes["gcode_hauteur_ecriture"] = Text(boites_titre["gcode"],
                                        text="hauteur de déplacement en écriture (mm)",
                                        grid=[0, 1])
curseurs["gcode_hauteur_ecriture"] = Slider(boites_titre["gcode"], start=-10, end=0,
                                            grid=[1, 1], step=0.1, width=300)
valeurs_defaut_curseurs["gcode_hauteur_ecriture"] = -2
boutons["reset_gcode"] = PushButton(app, text="paramètres de gcode par défaut",
                                    grid=[1, 9], command=reinitialiser_curseurs,
                                    args=["gcode_"])
boutons["execute_gcode"] = PushButton(app, text="exécute", grid=[2, 9],
                                      command=executer, args=["gcode_"])

# ---------------------- prévisualisation ----------------------
boites_titre["previsualiser"] = TitleBox(app, layout="grid",
                                         text="prévisualisation du résultat",
                                         grid=[0, 10])
textes["previsualiser_info"] = Text(boites_titre["previsualiser"],
                                    text="génère un aperçu PNG du tracé final",
                                    grid=[0, 0])
boutons["execute_previsualiser"] = PushButton(app, text="exécute",
                                              grid=[2, 10],
                                              command=executer,
                                              args=["previsualiser_"])

reinitialiser_curseurs("reset_all")
app.display()