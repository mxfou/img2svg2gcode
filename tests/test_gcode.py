"""Tests de l'étape 7 (génération du G-code) et de la lecture du G-code par l'étape 8."""

import json
import random

import numpy as np
import pytest

import img_process as ip


def _format_gscrib(x):
    """Formatage utilisé par gscrib (référence historique du G-code)."""
    if x == 0:
        return "0"
    return np.format_float_positional(x, precision=5, unique=True, fractional=True,
                                      sign=False, trim="-")


def test_nombre_gcode_exemples():
    assert ip._nombre_gcode(0) == "0"
    assert ip._nombre_gcode(-0.0) == "0"
    assert ip._nombre_gcode(3.0) == "3"
    assert ip._nombre_gcode(-2) == "-2"
    assert ip._nombre_gcode(16.000607) == "16.00061"
    assert ip._nombre_gcode(-3e-6) == "-0"  # particularité conservée de gscrib


def test_nombre_gcode_identique_a_gscrib():
    rng = random.Random(0)
    valeurs = [k / 64 for k in range(-3000, 3000)]  # égalités d'arrondi exactes en binaire
    valeurs += [rng.uniform(-2000, 2000) for _ in range(20000)]
    valeurs += [rng.uniform(-1, 1) * 1e-5 for _ in range(2000)]
    valeurs += [round(rng.uniform(-500, 500), d) + e
                for d in range(8) for e in (0, 5e-6, -5e-6) for _ in range(300)]
    for x in valeurs:
        assert ip._nombre_gcode(x) == _format_gscrib(x), x


def _ecrire_entree_etape7(dossier, segments, meme, hauteur=50.0):
    import svgpathtools
    lignes = [svgpathtools.Line(complex(x0, y0), complex(x1, y1)) for x0, y0, x1, y1 in segments]
    svg = dossier / "black.svg"
    ip._ecrire_svg_segments(str(svg), lignes, {"width": "100.0", "height": str(hauteur)})
    (dossier / "black.meme2").write_text(json.dumps(meme))
    return svg


SEGMENTS = [(0, 0, 10, 0), (10, 0, 10, 5.5), (1.234567, 2, 3, 4)]
MEME = [False, True, False]


def test_generer_gcode_texte_exact(tmp_path):
    svg = _ecrire_entree_etape7(tmp_path, SEGMENTS, MEME)
    sortie = tmp_path / "black.gcode"
    ip._generer_gcode_un_fichier((str(svg), str(sortie), "black", 3.0, -2.0, False))
    assert sortie.read_text() == (
        "G92 X0 Y0 Z0 ; Set axis position\n"
        "G21 ; Set length units, millimeters\n"
        "G90 ; Set distance mode, absolute\n"
        "G0 Z3\nG0 X0 Y0\nG0 Z-2\n"
        "G1 X10 Y0\n"
        "G1 X10 Y5.5\n"
        "G0 Z3\nG0 X1.23457 Y2\nG0 Z-2\n"
        "G1 X3 Y4\n"
        "G0 Z5\nG0 X0 Y0\n"
    )


def test_generer_gcode_inverser_y(tmp_path):
    svg = _ecrire_entree_etape7(tmp_path, SEGMENTS, MEME, hauteur=50.0)
    sortie = tmp_path / "black.gcode"
    ip._generer_gcode_un_fichier((str(svg), str(sortie), "black", 3.0, -2.0, True))
    lignes = sortie.read_text().splitlines()
    assert lignes[3] == ip.MARQUEUR_Y_INVERSE
    assert "G1 X10 Y44.5" in lignes            # 50 - 5.5
    assert "G0 X1.23457 Y48" in lignes         # 50 - 2
    assert lignes[-1] == "G0 X0 Y0"            # l'origine machine ne bouge pas


def test_generer_gcode_meme2_incoherent(tmp_path):
    svg = _ecrire_entree_etape7(tmp_path, SEGMENTS, MEME[:2])
    with pytest.raises(ValueError, match="meme2"):
        ip._generer_gcode_un_fichier((str(svg), str(tmp_path / "b.gcode"), "black", 3.0, -2.0, False))


@pytest.mark.parametrize("inverser", [False, True])
def test_aller_retour_gcode(tmp_path, inverser):
    """Ce que relit la prévisualisation correspond aux segments du SVG."""
    svg = _ecrire_entree_etape7(tmp_path, SEGMENTS, MEME, hauteur=50.0)
    sortie = tmp_path / "black.gcode"
    ip._generer_gcode_un_fichier((str(svg), str(sortie), "black", 3.0, -2.0, inverser))
    traces, deplacements, y_inverse = ip._parser_gcode(str(sortie))
    assert y_inverse == inverser
    attendu = [(x0, 50 - y0, x1, 50 - y1) if inverser else (x0, y0, x1, y1)
               for x0, y0, x1, y1 in SEGMENTS]
    assert traces == pytest.approx([tuple(round(v, 5) for v in s) for s in attendu])
    # déplacements à vide (XY) : vers le 1er segment (sauf s'il part de
    # l'origine, cas non inversé), vers le 3e, puis retour à l'origine
    assert len(deplacements) == (3 if inverser else 2)
