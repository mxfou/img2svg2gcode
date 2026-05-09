#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Interface en ligne de commande pour le pipeline bitmap2vector2gcode.

Exemples d'utilisation :

    # Pipeline complet avec valeurs par défaut
    ./cli.py tout --entree photo.jpg --sortie ./resultat

    # Pipeline complet avec dimension finale imposée
    ./cli.py tout --entree photo.jpg --sortie ./resultat --largeur-mm 200

    # Réexécuter uniquement une étape (utile pour itérer)
    ./cli.py graver --sortie ./resultat --rayon 0.6

    # Charger les paramètres depuis un fichier de config
    ./cli.py tout --entree photo.jpg --sortie ./resultat --config params.json

    # Générer un fichier de configuration par défaut
    ./cli.py config-defaut --sortie params.json
"""

import argparse
import json
import os
import sys

import img_process


# -----------------------------------------------------------------------------
# Valeurs par défaut (alignées avec main.py)
# -----------------------------------------------------------------------------
DEFAUTS = {
    "norm_amplitude": 4.32,
    "norm_rayon": 11,
    "norm_lissage": 8.68,
    "decouper_nombre": 5,
    "graver_rayon": 0.48,
    "redimensionner_facteur_echelle": 1.0,
    "redimensionner_taille_nettoyage": 5.0,
    "redimensionner_taille_approximation": 0.5,
    "gcode_hauteur_deplacement": 3.0,
    "gcode_hauteur_ecriture": -2.0,
}


# -----------------------------------------------------------------------------
# Utilitaires
# -----------------------------------------------------------------------------
def charger_config(chemin):
    """
    Charge un fichier JSON de configuration et fusionne avec les défauts.

    Crée d'abord un dictionnaire de configuration à partir des valeurs
    inscrites dans `DEFAUTS`, puis le complète/écrase avec celles trouvées
    dans le fichier JSON pointé par `chemin`. Cela permet à l'utilisateur
    de ne fournir qu'un sous-ensemble de paramètres dans son fichier de
    configuration : tous les autres conserveront leur valeur par défaut.

    Si `chemin` vaut `None`, retourne simplement une copie de `DEFAUTS`.
    Si `chemin` désigne un fichier inexistant, affiche une erreur sur
    stderr et termine le programme avec le code 1.

    Paramètres
    ----------
    chemin : str ou None
        Chemin vers le fichier JSON de configuration, ou `None` pour
        utiliser uniquement les valeurs par défaut.

    Retour
    ------
    dict
        Dictionnaire de configuration complet, prêt à être passé aux
        étapes du pipeline.
    """
    config = dict(DEFAUTS)
    if chemin:
        if not os.path.isfile(chemin):
            print(f"⚠️  fichier de config introuvable : {chemin}", file=sys.stderr)
            sys.exit(1)
        with open(chemin, "r", encoding="utf-8") as f:
            user_config = json.load(f)
        config.update(user_config)
    return config


def appliquer_overrides(config, args):
    """
    Remplace les valeurs de `config` par celles passées en ligne de commande.

    Implémente la priorité décroissante des sources de configuration :
    arguments CLI > fichier JSON > valeurs par défaut. Pour chaque argument
    présent dans le mapping interne et défini dans `args` (différent de
    `None`), la clé correspondante de `config` est mise à jour.

    Le mapping fait correspondre le nom *court* de l'argument CLI
    (par exemple `facteur_echelle`) au nom *long* utilisé dans la config
    (par exemple `redimensionner_facteur_echelle`).

    Paramètres
    ----------
    config : dict
        Configuration de départ (typiquement issue de `charger_config()`).
    args : argparse.Namespace
        Objet retourné par `argparse.ArgumentParser.parse_args()`.

    Retour
    ------
    dict
        Configuration mise à jour avec les valeurs CLI surchargeantes.
    """
    mapping = {
        "norm_amplitude": "norm_amplitude",
        "norm_rayon": "norm_rayon",
        "norm_lissage": "norm_lissage",
        "decouper_nombre": "decouper_nombre",
        "graver_rayon": "graver_rayon",
        "facteur_echelle": "redimensionner_facteur_echelle",
        "taille_nettoyage": "redimensionner_taille_nettoyage",
        "taille_approximation": "redimensionner_taille_approximation",
        "hauteur_deplacement": "gcode_hauteur_deplacement",
        "hauteur_ecriture": "gcode_hauteur_ecriture",
    }
    for arg_name, config_key in mapping.items():
        valeur = getattr(args, arg_name, None)
        if valeur is not None:
            config[config_key] = valeur
    return config


def calculer_facteur_echelle(args, config, fichier_entree):
    """
    Détermine le facteur d'échelle à utiliser pour l'étape de redimensionnement.

    Trois sources possibles, par ordre de priorité :
    1. Si `--largeur-mm` est fourni : facteur = largeur_mm / largeur_pixel
       de l'image source.
    2. Sinon, si `--hauteur-mm` est fourni : facteur = hauteur_mm /
       hauteur_pixel de l'image source.
    3. Sinon : valeur stockée dans `config["redimensionner_facteur_echelle"]`.

    Lorsque `--largeur-mm` ou `--hauteur-mm` est utilisé, un fichier d'entrée
    valide doit être fourni pour pouvoir lire les dimensions en pixels de
    l'image source ; sinon le programme s'arrête avec une erreur.

    Paramètres
    ----------
    args : argparse.Namespace
        Arguments de la ligne de commande.
    config : dict
        Configuration courante (utilisée si aucune dimension explicite).
    fichier_entree : str ou None
        Chemin vers l'image source, requis seulement si `--largeur-mm` ou
        `--hauteur-mm` est utilisé.

    Retour
    ------
    float
        Le facteur d'échelle à appliquer.
    """
    largeur_mm = getattr(args, "largeur_mm", None)
    hauteur_mm = getattr(args, "hauteur_mm", None)

    if largeur_mm is None and hauteur_mm is None:
        return config["redimensionner_facteur_echelle"]

    if not fichier_entree or not os.path.isfile(fichier_entree):
        print("⚠️  pour utiliser --largeur-mm ou --hauteur-mm, il faut "
              "fournir un --entree existant", file=sys.stderr)
        sys.exit(1)

    largeur_pix, hauteur_pix = img_process.retourne_taille_image(fichier_entree)
    if largeur_mm is not None:
        return largeur_mm / largeur_pix
    return hauteur_mm / hauteur_pix


def verifier_dossier_sortie(dossier, creer=True):
    """
    Vérifie que le dossier de sortie existe et le crée si demandé.

    Si le dossier n'existe pas :
    - avec `creer=True` (par défaut) : il est créé récursivement et un
      message d'information est affiché ;
    - avec `creer=False` : le programme s'arrête avec une erreur.

    Paramètres
    ----------
    dossier : str
        Chemin du dossier à vérifier.
    creer : bool
        Si True, crée le dossier manquant ; sinon échoue.
    """
    if not os.path.isdir(dossier):
        if creer:
            os.makedirs(dossier, exist_ok=True)
            print(f"📁 dossier de sortie créé : {dossier}")
        else:
            print(f"⚠️  dossier de sortie introuvable : {dossier}", file=sys.stderr)
            sys.exit(1)


# -----------------------------------------------------------------------------
# Étapes individuelles
# -----------------------------------------------------------------------------
def etape_normaliser(args, config):
    """
    Lance l'étape 1 du pipeline : séparation CMJN + négatif + normalisation.

    Vérifie d'abord que le dossier de sortie existe (le crée au besoin),
    puis appelle `img_process.cmyk_negatif_normalisation()` avec les valeurs
    appropriées tirées de `config`.

    Paramètres
    ----------
    args : argparse.Namespace
        Doit contenir `args.entree` (image source) et `args.sortie` (dossier).
    config : dict
        Configuration contenant les clés `norm_amplitude`, `norm_rayon`,
        `norm_lissage`.
    """
    verifier_dossier_sortie(args.sortie)
    img_process.cmyk_negatif_normalisation(
        args.entree, args.sortie,
        config["norm_amplitude"],
        config["norm_rayon"],
        config["norm_lissage"],
    )


def etape_decouper(args, config):
    """
    Lance l'étape 2 du pipeline : découpage par intensité.

    Wrapper minimal autour de `img_process.decouper()`.

    Paramètres
    ----------
    args : argparse.Namespace
        Doit contenir `args.sortie`.
    config : dict
        Doit contenir la clé `decouper_nombre`.
    """
    img_process.decouper(args.sortie, config["decouper_nombre"])


def etape_graver(args, config):
    """
    Lance l'étape 3 du pipeline : filtre "gravure".

    Wrapper minimal autour de `img_process.graver()`.

    Paramètres
    ----------
    args : argparse.Namespace
        Doit contenir `args.sortie`.
    config : dict
        Doit contenir la clé `graver_rayon`.
    """
    img_process.graver(args.sortie, config["graver_rayon"])


def etape_deformer(args, config):
    """
    Lance l'étape 4 du pipeline : déformation + dithering.

    Wrapper minimal autour de `img_process.deformer()`. Cette étape
    n'expose aucun paramètre configurable.

    Paramètres
    ----------
    args : argparse.Namespace
        Doit contenir `args.sortie`.
    config : dict
        Non utilisé (présent pour homogénéité de signature).
    """
    img_process.deformer(args.sortie)


def etape_vectoriser(args, config):
    """
    Lance l'étape 5 du pipeline : vectorisation par autotrace.

    Wrapper minimal autour de `img_process.vectoriser()`. Cette étape
    n'expose aucun paramètre configurable.

    Paramètres
    ----------
    args : argparse.Namespace
        Doit contenir `args.sortie`.
    config : dict
        Non utilisé (présent pour homogénéité de signature).
    """
    img_process.vectoriser(args.sortie)


def etape_redimensionner(args, config, fichier_entree=None):
    """
    Lance l'étape 6 du pipeline : redimensionnement, nettoyage, optimisation.

    Calcule d'abord le facteur d'échelle effectif via
    `calculer_facteur_echelle()`, puis appelle `img_process.redimensionner()`
    avec les paramètres correspondants.

    Paramètres
    ----------
    args : argparse.Namespace
        Doit contenir `args.sortie` ; éventuellement `args.largeur_mm`
        ou `args.hauteur_mm` s'ils sont utilisés.
    config : dict
        Doit contenir les clés `redimensionner_facteur_echelle`,
        `redimensionner_taille_nettoyage`,
        `redimensionner_taille_approximation`.
    fichier_entree : str ou None
        Image source, requise uniquement si l'utilisateur a fourni
        `--largeur-mm` ou `--hauteur-mm`.
    """
    facteur = calculer_facteur_echelle(args, config, fichier_entree)
    print(f"facteur d'échelle utilisé : {facteur}")
    img_process.redimensionner(
        args.sortie, facteur,
        config["redimensionner_taille_nettoyage"],
        config["redimensionner_taille_approximation"],
    )


def etape_gcode(args, config):
    """
    Lance l'étape 7 du pipeline : génération du G-code.

    Wrapper minimal autour de `img_process.generer_gcode()`.

    Paramètres
    ----------
    args : argparse.Namespace
        Doit contenir `args.sortie`.
    config : dict
        Doit contenir les clés `gcode_hauteur_deplacement`
        et `gcode_hauteur_ecriture`.
    """
    img_process.generer_gcode(
        args.sortie,
        config["gcode_hauteur_deplacement"],
        config["gcode_hauteur_ecriture"],
    )


# -----------------------------------------------------------------------------
# Pipeline complet
# -----------------------------------------------------------------------------
def commande_tout(args):
    """
    Exécute la totalité du pipeline (étapes 1 à 8) en une seule commande.

    Cette commande est l'équivalent CLI du bouton "exécute tout" de
    l'interface graphique. Elle :
    1. Vérifie l'existence du fichier d'entrée.
    2. Charge la configuration (fichier JSON + surcharges CLI).
    3. Crée le dossier de sortie au besoin.
    4. Affiche un récapitulatif des paramètres.
    5. Enchaîne les huit étapes :
       normalisation → découpage → gravure → déformation → vectorisation →
       redimensionnement → génération G-code → prévisualisation.

    Paramètres
    ----------
    args : argparse.Namespace
        Arguments parsés de la sous-commande `tout`. Doit notamment
        contenir `args.entree` et `args.sortie`.
    """
    if not args.entree or not os.path.isfile(args.entree):
        print(f"⚠️  fichier d'entrée introuvable : {args.entree}", file=sys.stderr)
        sys.exit(1)

    config = charger_config(args.config)
    config = appliquer_overrides(config, args)
    verifier_dossier_sortie(args.sortie)

    print("=" * 60)
    print("PIPELINE COMPLET bitmap2vector2gcode")
    print("=" * 60)
    print(f"entrée : {args.entree}")
    print(f"sortie : {args.sortie}")
    print(f"paramètres : {json.dumps(config, indent=2, ensure_ascii=False)}")
    print("=" * 60)

    print("\n[1/7] normalisation CMJN")
    etape_normaliser(args, config)

    print("\n[2/7] découpage par intensité")
    etape_decouper(args, config)

    print("\n[3/7] gravure")
    etape_graver(args, config)

    print("\n[4/7] déformation + dithering")
    etape_deformer(args, config)

    print("\n[5/7] vectorisation")
    etape_vectoriser(args, config)

    print("\n[6/7] redimensionnement + nettoyage + optimisation")
    etape_redimensionner(args, config, fichier_entree=args.entree)

    print("\n[7/7] génération G-code")
    etape_gcode(args, config)

    print("\n[8/8] prévisualisation")
    img_process.previsualiser_gcode(args.sortie)

    print("\n✅ pipeline terminé avec succès")
    print(f"   fichiers G-code disponibles dans : {os.path.join(args.sortie, '7-gcode')}")


# -----------------------------------------------------------------------------
# Commandes individuelles (wrappers)
# -----------------------------------------------------------------------------
def commande_normaliser(args):
    """
    Sous-commande CLI `normaliser` : exécute uniquement l'étape 1.

    Vérifie d'abord la présence du fichier d'entrée, charge la
    configuration (avec surcharges CLI), puis appelle `etape_normaliser()`.

    Paramètres
    ----------
    args : argparse.Namespace
        Doit contenir `args.entree`, `args.sortie`, et éventuellement
        `args.config` ainsi que les surcharges spécifiques à la
        normalisation.
    """
    if not args.entree or not os.path.isfile(args.entree):
        print(f"⚠️  fichier d'entrée introuvable : {args.entree}", file=sys.stderr)
        sys.exit(1)
    config = appliquer_overrides(charger_config(args.config), args)
    etape_normaliser(args, config)


def commande_decouper(args):
    """
    Sous-commande CLI `decouper` : exécute uniquement l'étape 2.

    Charge la configuration (avec surcharges CLI) puis appelle
    `etape_decouper()`.
    """
    config = appliquer_overrides(charger_config(args.config), args)
    etape_decouper(args, config)


def commande_graver(args):
    """
    Sous-commande CLI `graver` : exécute uniquement l'étape 3.

    Charge la configuration (avec surcharges CLI) puis appelle
    `etape_graver()`.
    """
    config = appliquer_overrides(charger_config(args.config), args)
    etape_graver(args, config)


def commande_deformer(args):
    """
    Sous-commande CLI `deformer` : exécute uniquement l'étape 4.

    Charge la configuration (par cohérence, même si l'étape n'a pas
    de paramètre) puis appelle `etape_deformer()`.
    """
    config = appliquer_overrides(charger_config(args.config), args)
    etape_deformer(args, config)


def commande_vectoriser(args):
    """
    Sous-commande CLI `vectoriser` : exécute uniquement l'étape 5.

    Charge la configuration (par cohérence, même si l'étape n'a pas
    de paramètre) puis appelle `etape_vectoriser()`.
    """
    config = appliquer_overrides(charger_config(args.config), args)
    etape_vectoriser(args, config)


def commande_redimensionner(args):
    """
    Sous-commande CLI `redimensionner` : exécute uniquement l'étape 6.

    Charge la configuration (avec surcharges CLI) puis appelle
    `etape_redimensionner()` en lui passant `args.entree` afin de pouvoir
    interpréter `--largeur-mm` ou `--hauteur-mm` si l'utilisateur les
    a fournis.
    """
    config = appliquer_overrides(charger_config(args.config), args)
    etape_redimensionner(args, config, fichier_entree=args.entree)


def commande_gcode(args):
    """
    Sous-commande CLI `gcode` : exécute uniquement l'étape 7.

    Charge la configuration (avec surcharges CLI) puis appelle
    `etape_gcode()`.
    """
    config = appliquer_overrides(charger_config(args.config), args)
    etape_gcode(args, config)


def commande_config_defaut(args):
    """
    Sous-commande CLI `config-defaut` : génère un fichier JSON contenant
    les valeurs par défaut.

    Permet à l'utilisateur de créer un gabarit de configuration qu'il
    pourra ensuite éditer à la main, plutôt que de partir d'une page
    blanche.

    Paramètres
    ----------
    args : argparse.Namespace
        Doit contenir `args.sortie` (chemin du fichier JSON à créer).
    """
    chemin = args.sortie
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(DEFAUTS, f, indent=2, ensure_ascii=False)
    print(f"✅ configuration par défaut écrite dans : {chemin}")

def commande_previsualiser(args):
    """
    Sous-commande CLI `previsualiser` : exécute uniquement l'étape 8.

    Génère une image PNG par couleur ainsi qu'une composition CMJN finale
    à partir des fichiers G-code présents dans le sous-dossier `7-gcode`.
    Cette commande est particulièrement utile lorsque l'utilisateur a
    modifié manuellement un fichier G-code et souhaite vérifier le rendu
    sans relancer tout le pipeline.

    Paramètres
    ----------
    args : argparse.Namespace
        Doit contenir `args.sortie`, `args.dpi`, `args.marge_mm`,
        `args.epaisseur_trait`, `args.afficher_deplacements`.
    """
    img_process.previsualiser_gcode(
        args.sortie,
        dpi=args.dpi,
        marge_mm=args.marge_mm,
        epaisseur_trait=args.epaisseur_trait,
        afficher_deplacements=args.afficher_deplacements,
    )

# -----------------------------------------------------------------------------
# Construction du parser
# -----------------------------------------------------------------------------
def ajouter_args_communs(parser, avec_entree=False):
    """
    Ajoute au parser les arguments communs à toutes les sous-commandes.

    Tous les sous-parseurs reçoivent au minimum `--sortie` (obligatoire) et
    `--config` (optionnel). Si `avec_entree` est True, ils reçoivent aussi
    `--entree` (obligatoire). Ce dernier est utilisé pour les commandes qui
    ont besoin de l'image source : `tout`, `normaliser`, et — en option —
    `redimensionner` (pour `--largeur-mm`/`--hauteur-mm`).

    Paramètres
    ----------
    parser : argparse.ArgumentParser
        Sous-parseur à enrichir.
    avec_entree : bool
        Si True, ajoute également l'argument `--entree`.
    """
    parser.add_argument("--sortie", "-s", required=True,
                        help="dossier de destination du pipeline")
    parser.add_argument("--config", "-c", default=None,
                        help="fichier JSON de configuration optionnel")
    if avec_entree:
        parser.add_argument("--entree", "-e", required=True,
                            help="fichier image d'entrée (jpg/png/bmp)")


def ajouter_args_norm(parser):
    """
    Ajoute les arguments propres à l'étape de normalisation CMJN.

    Trois paramètres : `--norm-amplitude`, `--norm-rayon`, `--norm-lissage`.
    Tous facultatifs (défaut `None`) ; lorsqu'ils ne sont pas fournis, les
    valeurs viennent de `config` (fichier JSON ou défauts).
    """
    parser.add_argument("--norm-amplitude", type=float, default=None)
    parser.add_argument("--norm-rayon", type=int, default=None)
    parser.add_argument("--norm-lissage", type=float, default=None)


def ajouter_args_decouper(parser):
    """
    Ajoute les arguments propres à l'étape de découpage par intensité.

    Un seul paramètre : `--decouper-nombre` (nombre de tranches d'intensité
    par couleur).
    """
    parser.add_argument("--decouper-nombre", type=int, default=None,
                        help="nombre d'images par couleur")


def ajouter_args_graver(parser):
    """
    Ajoute les arguments propres à l'étape de gravure.

    Un seul paramètre : `--graver-rayon` (épaisseur des traits de gravure).
    """
    parser.add_argument("--graver-rayon", type=float, default=None,
                        dest="graver_rayon",
                        help="épaisseur des traits de gravure")


def ajouter_args_redimensionner(parser):
    """
    Ajoute les arguments propres à l'étape de redimensionnement / optimisation.

    Cinq paramètres :
    - `--facteur-echelle` : multiplicateur direct des coordonnées ;
    - `--largeur-mm` : largeur cible (prioritaire sur le facteur d'échelle) ;
    - `--hauteur-mm` : hauteur cible (prioritaire sur le facteur d'échelle) ;
    - `--taille-nettoyage` : longueur min des traits conservés ;
    - `--taille-approximation` : longueur des segments d'approximation
      des courbes de Bézier.
    """
    parser.add_argument("--facteur-echelle", type=float, default=None,
                        help="facteur d'échelle (1.0 = taille pixel d'origine)")
    parser.add_argument("--largeur-mm", type=float, default=None,
                        help="largeur finale du dessin en mm "
                             "(prioritaire sur --facteur-echelle)")
    parser.add_argument("--hauteur-mm", type=float, default=None,
                        help="hauteur finale du dessin en mm "
                             "(prioritaire sur --facteur-echelle)")
    parser.add_argument("--taille-nettoyage", type=float, default=None,
                        help="longueur min des traits conservés (mm)")
    parser.add_argument("--taille-approximation", type=float, default=None,
                        help="longueur des segments d'approximation des splines (mm)")


def ajouter_args_gcode(parser):
    """
    Ajoute les arguments propres à l'étape de génération du G-code.

    Deux paramètres :
    - `--hauteur-deplacement` : Z (mm) lorsque l'outil se déplace à vide ;
    - `--hauteur-ecriture` : Z (mm) lorsque l'outil trace.
    """
    parser.add_argument("--hauteur-deplacement", type=float, default=None,
                        help="Z lors des déplacements à vide (mm)")
    parser.add_argument("--hauteur-ecriture", type=float, default=None,
                        help="Z lors de l'écriture (mm)")


def construire_parser():
    """
    Construit et retourne l'`ArgumentParser` complet de l'application CLI.

    Le parseur expose neuf sous-commandes :
    - `tout` : pipeline complet ;
    - `normaliser`, `decouper`, `graver`, `deformer`, `vectoriser`,
      `redimensionner`, `gcode`, `previsualiser` : étapes individuelles ;
    - `config-defaut` : génération d'un fichier JSON de configuration par défaut.

    Chaque sous-commande est associée à une fonction `commande_*` via
    `set_defaults(func=...)`. Le point d'entrée `main()` se contente
    d'appeler `args.func(args)` à la fin.

    Retour
    ------
    argparse.ArgumentParser
        Parseur prêt à recevoir `parse_args()`.
    """
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="Pipeline bitmap2vector2gcode en ligne de commande",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="commande", required=True,
                                metavar="COMMANDE")

    # ----- Pipeline complet -----
    p_tout = sub.add_parser("tout", help="exécute le pipeline complet (7 étapes)")
    ajouter_args_communs(p_tout, avec_entree=True)
    ajouter_args_norm(p_tout)
    ajouter_args_decouper(p_tout)
    ajouter_args_graver(p_tout)
    ajouter_args_redimensionner(p_tout)
    ajouter_args_gcode(p_tout)
    p_tout.set_defaults(func=commande_tout)

    # ----- Étapes individuelles -----
    p_norm = sub.add_parser("normaliser", help="étape 1 : séparation CMJN + normalisation")
    ajouter_args_communs(p_norm, avec_entree=True)
    ajouter_args_norm(p_norm)
    p_norm.set_defaults(func=commande_normaliser)

    p_dec = sub.add_parser("decouper", help="étape 2 : découpage par intensité")
    ajouter_args_communs(p_dec)
    ajouter_args_decouper(p_dec)
    p_dec.set_defaults(func=commande_decouper)

    p_grav = sub.add_parser("graver", help="étape 3 : filtre gravure")
    ajouter_args_communs(p_grav)
    ajouter_args_graver(p_grav)
    p_grav.set_defaults(func=commande_graver)

    p_def = sub.add_parser("deformer", help="étape 4 : déformation + dithering")
    ajouter_args_communs(p_def)
    p_def.set_defaults(func=commande_deformer)

    p_vec = sub.add_parser("vectoriser", help="étape 5 : vectorisation autotrace")
    ajouter_args_communs(p_vec)
    p_vec.set_defaults(func=commande_vectoriser)

    p_red = sub.add_parser("redimensionner",
                           help="étape 6 : redim + nettoyage + optimisation")
    ajouter_args_communs(p_red)
    # entrée optionnelle pour pouvoir utiliser --largeur-mm / --hauteur-mm
    p_red.add_argument("--entree", "-e", default=None,
                       help="fichier image d'origine "
                            "(requis si --largeur-mm ou --hauteur-mm)")
    ajouter_args_redimensionner(p_red)
    p_red.set_defaults(func=commande_redimensionner)

    p_gc = sub.add_parser("gcode", help="étape 7 : génération G-code")
    ajouter_args_communs(p_gc)
    ajouter_args_gcode(p_gc)
    p_gc.set_defaults(func=commande_gcode)

    p_prev = sub.add_parser("previsualiser",
                            help="étape 8 : génère une image PNG du résultat")
    ajouter_args_communs(p_prev)
    p_prev.add_argument("--dpi", type=int, default=150,
                        help="résolution de l'image (défaut: 150)")
    p_prev.add_argument("--marge-mm", type=float, default=10,
                        help="marge en mm autour du dessin (défaut: 10)")
    p_prev.add_argument("--epaisseur-trait", type=float, default=1.0,
                        help="épaisseur des traits en pixels (défaut: 3.0)")
    p_prev.add_argument("--afficher-deplacements", action="store_true",
                        help="dessine les déplacements à vide en pointillés")
    p_prev.set_defaults(func=commande_previsualiser)

    # ----- Génération de config par défaut -----
    p_cfg = sub.add_parser("config-defaut",
                           help="génère un fichier de configuration JSON par défaut")
    p_cfg.add_argument("--sortie", "-s", required=True,
                       help="chemin du fichier JSON à créer")
    p_cfg.set_defaults(func=commande_config_defaut)

    

    return parser


# -----------------------------------------------------------------------------
# Point d'entrée
# -----------------------------------------------------------------------------
def main():
    """
    Point d'entrée du programme en ligne de commande.

    Construit le parseur via `construire_parser()`, parse `sys.argv`, puis
    délègue l'exécution à la fonction associée à la sous-commande choisie
    (renseignée via `set_defaults(func=...)` lors de la construction).
    """
    parser = construire_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
