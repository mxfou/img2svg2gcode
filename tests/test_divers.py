"""Tests transverses : chemins G'MIC, prévisualisation, import de la GUI."""

import os
import runpy

import numpy as np
from PIL import Image

import img_process as ip

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_gmic_chemins_speciaux(tmp_path):
    """Étape 2 dans un dossier dont le nom contient espace, virgule, $ et accolades."""
    dossier = tmp_path / "sortie avec espace, virgule $HOME {1} é"
    (dossier / "1-cmyk").mkdir(parents=True)
    Image.fromarray(np.full((8, 8), 100, dtype=np.uint8)).save(dossier / "1-cmyk" / "image_000000.png")
    ip.decouper(str(dossier), 2)
    assert sorted(os.listdir(dossier / "2-cut")) == ["image_cyan_0.png", "image_cyan_50.png"]


def _gcode(chemin, lignes):
    chemin.write_text("\n".join(["G92 X0 Y0 Z0", "G21", "G90"] + lignes + ["G0 Z5", "G0 X0 Y0"]) + "\n")


def test_previsualisation_y_inverse(tmp_path):
    """Un G-code en Y inversé donne exactement le même aperçu que l'original."""
    def trace(ys):  # même dessin, seules les ordonnées changent
        return ["G0 Z3", f"G0 X0 Y{ys[0]}", "G0 Z-2", f"G1 X40 Y{ys[1]}", f"G1 X40 Y{ys[2]}",
                "G0 Z3", f"G0 X5 Y{ys[3]}", "G0 Z-2", f"G1 X10 Y{ys[4]}"]
    normal = trace([0, 0, 20, 5, 30])
    inverse = trace([30 - y for y in [0, 0, 20, 5, 30]])  # hauteur 30 mm
    for nom, lignes, entete in [("normal", normal, []), ("inverse", inverse, [ip.MARQUEUR_Y_INVERSE])]:
        (tmp_path / nom / "7-gcode").mkdir(parents=True)
        _gcode(tmp_path / nom / "7-gcode" / "black.gcode", entete + lignes)
        ip.previsualiser_gcode(str(tmp_path / nom), dpi=50)
    for fichier in ["black.png", "compose.png"]:
        a = np.array(Image.open(tmp_path / "normal" / "8-preview" / fichier))
        b = np.array(Image.open(tmp_path / "inverse" / "8-preview" / fichier))
        assert a.shape == b.shape and (a == b).all()
        assert (a < 255).any()  # quelque chose a bien été dessiné


def test_epaisseur_trait_mm(tmp_path):
    (tmp_path / "7-gcode").mkdir()
    _gcode(tmp_path / "7-gcode" / "black.gcode", ["G0 Z3", "G0 X0 Y10", "G0 Z-2", "G1 X50 Y10"])
    largeurs = {}
    for mm in (0.5, 2.0):
        ip.previsualiser_gcode(str(tmp_path), dpi=254, epaisseur_trait_mm=mm)  # 10 px/mm
        img = np.array(Image.open(tmp_path / "8-preview" / "black.png").convert("L"))
        largeurs[mm] = int((img[:, img.shape[1] // 2] < 128).sum())
    assert largeurs == {0.5: 5, 2.0: 20}


def test_import_gui_sans_fenetre():
    """Réimporter main.py (ce que font les processus en spawn/forkserver)
    ne doit pas construire l'interface."""
    ns = runpy.run_path(os.path.join(RACINE, "main.py"), run_name="__mp_main__")
    assert ns["app"] is None


def test_relancer_une_etape_ne_laisse_pas_d_anciens_fichiers(tmp_path):
    """Découper en 5 puis en 3 couches : seules les 3 nouvelles restent."""
    (tmp_path / "1-cmyk").mkdir()
    Image.fromarray(np.full((8, 8), 100, dtype=np.uint8)).save(tmp_path / "1-cmyk" / "image_000000.png")
    ip.decouper(str(tmp_path), 5)
    assert len(os.listdir(tmp_path / "2-cut")) == 5
    ip.decouper(str(tmp_path), 3)
    assert sorted(os.listdir(tmp_path / "2-cut")) == ["image_cyan_0.png", "image_cyan_34.png", "image_cyan_68.png"]
