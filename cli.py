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
    """Charge un fichier JSON de configuration et fusionne avec les défauts."""
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
    """Remplace les valeurs de config par celles passées en ligne de commande."""
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
    Si --largeur-mm ou --hauteur-mm est fourni, calcule le facteur d'échelle
    correspondant. Sinon, utilise celui du config.
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
    verifier_dossier_sortie(args.sortie)
    img_process.cmyk_negatif_normalisation(
        args.entree, args.sortie,
        config["norm_amplitude"],
        config["norm_rayon"],
        config["norm_lissage"],
    )


def etape_decouper(args, config):
    img_process.decouper(args.sortie, config["decouper_nombre"])


def etape_graver(args, config):
    img_process.graver(args.sortie, config["graver_rayon"])


def etape_deformer(args, config):
    img_process.deformer(args.sortie)


def etape_vectoriser(args, config):
    img_process.vectoriser(args.sortie)


def etape_redimensionner(args, config, fichier_entree=None):
    facteur = calculer_facteur_echelle(args, config, fichier_entree)
    print(f"facteur d'échelle utilisé : {facteur}")
    img_process.redimensionner(
        args.sortie, facteur,
        config["redimensionner_taille_nettoyage"],
        config["redimensionner_taille_approximation"],
    )


def etape_gcode(args, config):
    img_process.generer_gcode(
        args.sortie,
        config["gcode_hauteur_deplacement"],
        config["gcode_hauteur_ecriture"],
    )


# -----------------------------------------------------------------------------
# Pipeline complet
# -----------------------------------------------------------------------------
def commande_tout(args):
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

    print("\n✅ pipeline terminé avec succès")
    print(f"   fichiers G-code disponibles dans : {os.path.join(args.sortie, '7-gcode')}")


# -----------------------------------------------------------------------------
# Commandes individuelles (wrappers)
# -----------------------------------------------------------------------------
def commande_normaliser(args):
    if not args.entree or not os.path.isfile(args.entree):
        print(f"⚠️  fichier d'entrée introuvable : {args.entree}", file=sys.stderr)
        sys.exit(1)
    config = appliquer_overrides(charger_config(args.config), args)
    etape_normaliser(args, config)


def commande_decouper(args):
    config = appliquer_overrides(charger_config(args.config), args)
    etape_decouper(args, config)


def commande_graver(args):
    config = appliquer_overrides(charger_config(args.config), args)
    etape_graver(args, config)


def commande_deformer(args):
    config = appliquer_overrides(charger_config(args.config), args)
    etape_deformer(args, config)


def commande_vectoriser(args):
    config = appliquer_overrides(charger_config(args.config), args)
    etape_vectoriser(args, config)


def commande_redimensionner(args):
    config = appliquer_overrides(charger_config(args.config), args)
    etape_redimensionner(args, config, fichier_entree=args.entree)


def commande_gcode(args):
    config = appliquer_overrides(charger_config(args.config), args)
    etape_gcode(args, config)


def commande_config_defaut(args):
    """Génère un fichier de configuration JSON avec les valeurs par défaut."""
    chemin = args.sortie
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(DEFAUTS, f, indent=2, ensure_ascii=False)
    print(f"✅ configuration par défaut écrite dans : {chemin}")


# -----------------------------------------------------------------------------
# Construction du parser
# -----------------------------------------------------------------------------
def ajouter_args_communs(parser, avec_entree=False):
    parser.add_argument("--sortie", "-s", required=True,
                        help="dossier de destination du pipeline")
    parser.add_argument("--config", "-c", default=None,
                        help="fichier JSON de configuration optionnel")
    if avec_entree:
        parser.add_argument("--entree", "-e", required=True,
                            help="fichier image d'entrée (jpg/png/bmp)")


def ajouter_args_norm(parser):
    parser.add_argument("--norm-amplitude", type=float, default=None)
    parser.add_argument("--norm-rayon", type=int, default=None)
    parser.add_argument("--norm-lissage", type=float, default=None)


def ajouter_args_decouper(parser):
    parser.add_argument("--decouper-nombre", type=int, default=None,
                        help="nombre d'images par couleur")


def ajouter_args_graver(parser):
    parser.add_argument("--graver-rayon", type=float, default=None,
                        dest="graver_rayon",
                        help="épaisseur des traits de gravure")


def ajouter_args_redimensionner(parser):
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
    parser.add_argument("--hauteur-deplacement", type=float, default=None,
                        help="Z lors des déplacements à vide (mm)")
    parser.add_argument("--hauteur-ecriture", type=float, default=None,
                        help="Z lors de l'écriture (mm)")


def construire_parser():
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
    parser = construire_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()#!/usr/bin/env python3
# -*- coding: utf-8 -*-


