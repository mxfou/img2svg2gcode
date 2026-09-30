"""Tests de la CLI : priorité drapeau > fichier de config > valeurs par défaut."""

import json

import cli


def _config(argv, tmp_path, contenu=None):
    if contenu is not None:
        chemin = tmp_path / "config.json"
        chemin.write_text(json.dumps(contenu))
        argv = argv + ["-c", str(chemin)]
    args = cli.construire_parser().parse_args(argv)
    return cli.appliquer_overrides(cli.charger_config(args.config), args)


def test_priorite_options_previsualisation(tmp_path):
    base = ["previsualiser", "-s", str(tmp_path)]
    assert _config(base, tmp_path)["previsualiser_dpi"] == 150
    assert _config(base, tmp_path, {"previsualiser_dpi": 50})["previsualiser_dpi"] == 50
    assert _config(base + ["--dpi", "100"], tmp_path, {"previsualiser_dpi": 50})["previsualiser_dpi"] == 100


def test_drapeau_booleen_absent_ne_masque_pas_la_config(tmp_path):
    config = _config(["tout", "-e", "x.png", "-s", str(tmp_path)], tmp_path,
                     {"gcode_inverser_y": True, "previsualiser_afficher_deplacements": True})
    assert config["gcode_inverser_y"] is True
    assert config["previsualiser_afficher_deplacements"] is True
    config = _config(["tout", "-e", "x.png", "-s", str(tmp_path), "--afficher-deplacements"], tmp_path)
    assert config["previsualiser_afficher_deplacements"] is True
