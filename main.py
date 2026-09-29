#!/usr/bin/env python3
# -*- coding: utf-8 -*-


import multiprocessing
import os

from guizero import App, Text, PushButton, TitleBox, TextBox, Slider, CheckBox

try:
    from . import img_process  # contexte package (pdoc)
except ImportError:
    import img_process         # exécution directe (uv run)

boites_texte = {}
boites_titre = {}
curseurs = {}
valeurs_defaut_curseurs = {}
textes = {}
boutons = {}
app = None

# Processus du pipeline en cours d'exécution (None si aucun).
tache = None
# "spawn" : le calcul démarre dans un interpréteur neuf, sans hériter de
# l'état Tk de la fenêtre (forker un processus qui fait tourner Tk peut bloquer).
_contexte_processus = multiprocessing.get_context("spawn")


def reinitialiser_curseurs(arg):
    """
    Réinitialise un sous-ensemble de curseurs (sliders) à leurs valeurs par défaut.

    La fonction agit comme un filtre :
    - Si `arg == "reset_all"`, **tous** les curseurs sont réinitialisés.
    - Sinon, `arg` est utilisé comme préfixe : seuls les curseurs dont la
      clé contient cette chaîne sont remis à leur valeur par défaut.
      Par exemple `arg = "norm_"` ne réinitialise que les curseurs de
      l'étape de normalisation.

    Les valeurs de référence sont lues dans le dictionnaire global
    `valeurs_defaut_curseurs`, peuplé au moment de la création de l'interface.

    Paramètres
    ----------
    arg : str
        Soit la valeur spéciale `"reset_all"`, soit un préfixe de clé
        (typiquement `"norm_"`, `"decouper_"`, `"graver_"`, `"redimensionner_"`,
        `"gcode_"`, etc.).
    """
    if arg == "reset_all":
        for k in curseurs:
            curseurs[k].value = valeurs_defaut_curseurs[k]
    else:
        for k in curseurs:
            if arg in k:
                curseurs[k].value = valeurs_defaut_curseurs[k]


def choisir_fichier(arg):
    """
    Ouvre une boîte de dialogue pour sélectionner un fichier image et met à jour
    l'interface en conséquence.

    Le chemin du fichier sélectionné est affiché dans le widget Text identifié
    par la clé `arg`. Si la sélection concerne le fichier d'entrée principal
    (`arg == "fichiers_fichier_entree"`), des actions complémentaires sont
    déclenchées :
    - lecture des dimensions de l'image via `img_process.retourne_taille_image()` ;
    - mise à jour des champs `largeur de l'image` et `hauteur de l'image` ;
    - pré-remplissage des TextBox de taille finale (largeur / hauteur en mm)
      avec les dimensions en pixels et un facteur d'échelle initial de 1.

    Paramètres
    ----------
    arg : str
        Clé du dictionnaire `textes` du widget cible (typiquement
        `"fichiers_fichier_entree"`).
    """
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
    """
    Ouvre une boîte de dialogue pour sélectionner un dossier et l'affiche
    dans le widget Text correspondant.

    Paramètres
    ----------
    arg : str
        Clé du dictionnaire `textes` du widget cible (typiquement
        `"fichiers_dossier_sortie"`).
    """
    textes[arg].clear()
    dossier = app.select_folder()
    textes[arg].append(dossier)


def changement_echelle():
    """
    Callback déclenché lorsque l'utilisateur modifie le facteur d'échelle.

    Recalcule la largeur et la hauteur en mm à partir des dimensions en
    pixels de l'image source et du nouveau facteur d'échelle, puis met à
    jour les TextBox correspondantes.

    C'est l'une des trois fonctions de synchronisation triplette
    largeur ⇄ hauteur ⇄ facteur d'échelle.
    """
    boites_texte["redimensionner_largeur"].value = (
        float(textes["fichiers_largeur_image"].value)
        * float(boites_texte["redimensionner_facteur_echelle"].value)
    )
    boites_texte["redimensionner_hauteur"].value = (
        float(textes["fichiers_hauteur_image"].value)
        * float(boites_texte["redimensionner_facteur_echelle"].value)
    )


def changement_largeur():
    """
    Callback déclenché lorsque l'utilisateur saisit une nouvelle largeur en mm.

    Recalcule le facteur d'échelle correspondant (largeur cible ÷ largeur
    en pixels), puis appelle `changement_echelle()` pour mettre à jour la
    hauteur de manière cohérente (préservation du ratio).
    """
    boites_texte["redimensionner_facteur_echelle"].value = (
        float(boites_texte["redimensionner_largeur"].value)
        / float(textes["fichiers_largeur_image"].value)
    )
    changement_echelle()


def changement_hauteur():
    """
    Callback déclenché lorsque l'utilisateur saisit une nouvelle hauteur en mm.

    Recalcule le facteur d'échelle correspondant (hauteur cible ÷ hauteur
    en pixels), puis appelle `changement_echelle()` pour mettre à jour la
    largeur de manière cohérente (préservation du ratio).
    """
    boites_texte["redimensionner_facteur_echelle"].value = (
        float(boites_texte["redimensionner_hauteur"].value)
        / float(textes["fichiers_hauteur_image"].value)
    )
    changement_echelle()


def executer(arg):
    """
    Lance une étape du pipeline (ou la totalité) sans bloquer l'interface.

    Cette fonction joue le rôle d'aiguilleur principal de l'interface. Elle :
    1. Récupère les valeurs courantes de tous les curseurs et boîtes de texte
       pertinents.
    2. Vérifie que le dossier de destination (et le fichier d'entrée pour
       l'étape 1) ont bien été choisis.
    3. Démarre `executer_etapes()` dans un processus séparé : la fenêtre reste
       réactive pendant le calcul. Les boutons "exécute" sont désactivés
       jusqu'à la fin, détectée par `surveiller_tache()`.

    Paramètres
    ----------
    arg : str
        Identifiant de l'étape à exécuter, ou la valeur spéciale `"execute_all"`
        (voir `executer_etapes()`).
    """
    global tache
    if tache is not None:
        return
    parametres = {
        "fichier_entree": textes["fichiers_fichier_entree"].value,
        "dossier_sortie": textes["fichiers_dossier_sortie"].value,
        "norm_amplitude": curseurs["norm_amplitude"].value,
        "norm_rayon": curseurs["norm_rayon"].value,
        "norm_lissage_moyen": curseurs["norm_lissage"].value,
        "decouper_nb_images": curseurs["decouper_nombre"].value,
        "graver_rayon": curseurs["graver_rayon"].value,
        "redimensionner_facteur_echelle": boites_texte["redimensionner_facteur_echelle"].value,
        "redimensionner_taille_nettoyage": curseurs["redimensionner_taille_nettoyage"].value,
        "redimensionner_taille_approximation": curseurs["redimensionner_taille_approximation"].value,
        "gcode_hauteur_deplacement": curseurs["gcode_hauteur_deplacement"].value,
        "gcode_hauteur_ecriture": curseurs["gcode_hauteur_ecriture"].value,
        "gcode_inverser_y": bool(curseurs["gcode_inverser_y"].value),
    }
    if not os.path.isdir(parametres["dossier_sortie"]):
        textes["statut"].value = "⚠️ choisir d'abord le dossier de destination"
        return
    if (arg == "execute_all" or "norm" in arg) and not os.path.isfile(parametres["fichier_entree"]):
        textes["statut"].value = "⚠️ choisir d'abord le fichier à traiter"
        return

    tache = _contexte_processus.Process(target=executer_etapes, args=(arg, parametres))
    tache.start()
    for cle, bouton in boutons.items():
        if cle.startswith("execute"):
            bouton.disable()
    textes["statut"].value = f"⏳ en cours : {arg.rstrip('_')} (détails dans le terminal)"


def executer_etapes(arg, p):
    """
    Exécute réellement la ou les étapes demandées (dans le processus lancé
    par `executer()`).

    - `"execute_all"` : enchaîne les huit étapes du pipeline
      (normalisation → découpage → gravure → déformation → vectorisation →
      redimensionnement → génération du G-code → prévisualisation).
    - Toute autre valeur est interprétée comme un préfixe d'étape :
      * `"norm"`           → cmyk_negatif_normalisation
      * `"decouper"`       → decouper
      * `"graver"`         → graver
      * `"deformer"`       → deformer
      * `"vectoriser"`     → vectoriser
      * `"redimensionner"` → redimensionner
      * `"gcode"`          → generer_gcode
      * `"previsualiser"`  → previsualiser_gcode

    Paramètres
    ----------
    arg : str
        Identifiant de l'étape à exécuter, ou la valeur spéciale `"execute_all"`.
    p : dict
        Valeurs des widgets relevées par `executer()`.
    """
    dossier_sortie = p["dossier_sortie"]
    if arg == "execute_all":
        img_process.cmyk_negatif_normalisation(p["fichier_entree"], dossier_sortie,
                                               p["norm_amplitude"], p["norm_rayon"],
                                               p["norm_lissage_moyen"])
        img_process.decouper(dossier_sortie, p["decouper_nb_images"])
        img_process.graver(dossier_sortie, p["graver_rayon"])
        img_process.deformer(dossier_sortie)
        img_process.vectoriser(dossier_sortie)
        img_process.redimensionner(dossier_sortie, p["redimensionner_facteur_echelle"],
                                   p["redimensionner_taille_nettoyage"],
                                   p["redimensionner_taille_approximation"])
        img_process.generer_gcode(dossier_sortie, p["gcode_hauteur_deplacement"],
                                  p["gcode_hauteur_ecriture"], p["gcode_inverser_y"])
        img_process.previsualiser_gcode(dossier_sortie)
    elif "norm" in arg:
        img_process.cmyk_negatif_normalisation(p["fichier_entree"], dossier_sortie,
                                               p["norm_amplitude"], p["norm_rayon"],
                                               p["norm_lissage_moyen"])
    elif "decouper" in arg:
        img_process.decouper(dossier_sortie, p["decouper_nb_images"])
    elif "graver" in arg:
        img_process.graver(dossier_sortie, p["graver_rayon"])
    elif "deformer" in arg:
        img_process.deformer(dossier_sortie)
    elif "vectoriser" in arg:
        img_process.vectoriser(dossier_sortie)
    elif "redimensionner" in arg:
        img_process.redimensionner(dossier_sortie, p["redimensionner_facteur_echelle"],
                                   p["redimensionner_taille_nettoyage"],
                                   p["redimensionner_taille_approximation"])
    elif "gcode" in arg:
        img_process.generer_gcode(dossier_sortie, p["gcode_hauteur_deplacement"],
                                  p["gcode_hauteur_ecriture"], p["gcode_inverser_y"])
    elif "previsualiser" in arg:
        img_process.previsualiser_gcode(dossier_sortie)


def surveiller_tache():
    """
    Appelée toutes les 250 ms par la boucle Tk (`app.repeat`). Quand le
    processus lancé par `executer()` se termine, affiche le résultat dans la
    ligne de statut et réactive les boutons "exécute".
    """
    global tache
    if tache is None or tache.is_alive():
        return
    tache.join()
    if tache.exitcode == 0:
        textes["statut"].value = "✅ terminé"
    else:
        textes["statut"].value = f"❌ échec (code {tache.exitcode}), voir le terminal"
    tache = None
    for cle, bouton in boutons.items():
        if cle.startswith("execute"):
            bouton.enable()


def construire_interface():
    """
    Crée la fenêtre principale et tous ses widgets (un panneau par étape).

    Les widgets sont rangés dans les dictionnaires globaux (`curseurs`,
    `boites_texte`, `textes`, `boutons`…) pour que les callbacks les retrouvent.
    La construction est isolée dans une fonction pour que l'import du module
    (processus de calcul, pdoc) n'ouvre aucune fenêtre.
    """
    global app
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
    # rangée avec les curseurs pour profiter de la réinitialisation par défaut
    curseurs["gcode_inverser_y"] = CheckBox(boites_titre["gcode"],
                                            text="inverser l'axe Y (origine en bas "
                                                 "à gauche, convention CNC)",
                                            grid=[0, 2, 2, 1])
    valeurs_defaut_curseurs["gcode_inverser_y"] = 0
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

    textes["statut"] = Text(app, text="", grid=[0, 11])
    app.repeat(250, surveiller_tache)


if __name__ == "__main__":
    construire_interface()
    reinitialiser_curseurs("reset_all")
    app.display()
