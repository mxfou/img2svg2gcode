"""Tests de l'étape 6 : écriture/lecture du SVG et optimisation du parcours."""

import json
import math
import random

import svgpathtools

import img_process as ip

ATTRIBUTS = {"xmlns": "http://www.w3.org/2000/svg", "width": "120.0", "height": "80.0"}


def _lignes_aleatoires(n, graine=0):
    rng = random.Random(graine)
    return [svgpathtools.Line(complex(rng.uniform(0, 120), rng.uniform(0, 80)),
                              complex(rng.uniform(0, 120), rng.uniform(0, 80)))
            for _ in range(n)]


def test_svg_identique_a_wsvg(tmp_path):
    """_ecrire_svg_segments remplace svgpathtools.wsvg octet pour octet."""
    lignes = _lignes_aleatoires(300)
    svgpathtools.wsvg(lignes, svg_attributes=dict(ATTRIBUTS), filename=str(tmp_path / "ref.svg"))
    ip._ecrire_svg_segments(str(tmp_path / "nous.svg"), lignes, ATTRIBUTS)
    assert (tmp_path / "nous.svg").read_bytes() == (tmp_path / "ref.svg").read_bytes()


def test_lecture_svg_exacte(tmp_path):
    lignes = _lignes_aleatoires(200, graine=1)
    ip._ecrire_svg_segments(str(tmp_path / "a.svg"), lignes, ATTRIBUTS)
    segments, hauteur = ip._lire_segments_svg(str(tmp_path / "a.svg"))
    assert hauteur == 80.0
    assert segments == [(s.start.real, s.start.imag, s.end.real, s.end.imag) for s in lignes]


def test_lecture_svg_format_inattendu(tmp_path):
    """Un SVG qui ne vient pas de l'étape 6 est lu via svgpathtools."""
    (tmp_path / "a.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="20">'
        '<path d="M 0,0 L 1,1 L 2,0"/></svg>')
    segments, hauteur = ip._lire_segments_svg(str(tmp_path / "a.svg"))
    assert hauteur == 20.0
    assert segments == [(0, 0, 1, 1), (1, 1, 2, 0)]


def _preparer_entree(dossier, lignes):
    entree = dossier / "5-vector"
    entree.mkdir()
    svgpathtools.wsvg(lignes, svg_attributes=dict(ATTRIBUTS),
                      filename=str(entree / "image_black_0.svg"))
    sortie = dossier / "6-resize"
    sortie.mkdir()
    return entree, sortie


def test_parcours_glouton(tmp_path):
    """Chaque segment est tracé une seule fois, et toujours en partant de
    l'extrémité disponible la plus proche (plus proche voisin)."""
    lignes = _lignes_aleatoires(400, graine=2)  # > 10 : force plusieurs reconstructions
    entree, sortie = _preparer_entree(tmp_path, lignes)
    ip._traiter_couleur_etape6(("black", ["image_black_0.svg"], str(entree), str(sortie), 1.0, 0.0, 0.5))

    segments, _ = ip._lire_segments_svg(str(sortie / "black.svg"))
    meme = json.loads((sortie / "black.meme2").read_text())
    non_orientes = lambda segs: sorted(tuple(sorted([(a, b), (c, d)])) for a, b, c, d in segs)
    originaux = [(s.start.real, s.start.imag, s.end.real, s.end.imag) for s in lignes]
    assert non_orientes(segments) == non_orientes(originaux)

    restants = list(originaux)
    position = (0.0, 0.0)
    for (x0, y0, x1, y1), continu in zip(segments, meme):
        d_choisi = math.dist(position, (x0, y0))
        d_min = min(min(math.dist(position, (a, b)), math.dist(position, (c, d)))
                    for a, b, c, d in restants)
        assert math.isclose(d_choisi, d_min, abs_tol=1e-9)
        assert continu == (d_choisi == 0)
        restants.remove(next(s for s in restants
                             if sorted([(s[0], s[1]), (s[2], s[3])]) == sorted([(x0, y0), (x1, y1)])))
        position = (x1, y1)


def test_nettoyage_et_linearisation(tmp_path):
    courbe = svgpathtools.CubicBezier(10 + 10j, 20 + 40j, 40 + 40j, 50 + 10j)
    lignes = [svgpathtools.Line(0 + 0j, 1 + 0j),   # 1 mm : supprimée (< 5 mm)
              svgpathtools.Line(60 + 0j, 60 + 30j),
              courbe]
    entree, sortie = _preparer_entree(tmp_path, lignes)
    ip._traiter_couleur_etape6(("black", ["image_black_0.svg"], str(entree), str(sortie), 1.0, 5.0, 2.0))
    segments, _ = ip._lire_segments_svg(str(sortie / "black.svg"))
    meme = json.loads((sortie / "black.meme2").read_text())
    assert all(math.dist((a, b), (c, d)) > 0.9 for a, b, c, d in segments)  # la ligne de 1 mm a disparu
    # la courbe devient une polyligne continue de ~longueur/2 segments
    extremites = [p for a, b, c, d in segments for p in ((a, b), (c, d))]
    for bout in [(10, 10), (50, 10)]:  # extrémités de la courbe conservées
        assert any(math.dist(bout, p) < 1e-9 for p in extremites)
    assert len(segments) == 1 + int(courbe.length() // 2.0)
    assert meme.count(False) == 2  # une levée par tracé continu
