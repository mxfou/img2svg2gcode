"""Tests de l'interface web (API Starlette)."""

import io
import json
import os
import signal
import time
import zipfile
from pathlib import Path

import pytest
from PIL import Image
from starlette.testclient import TestClient

import cli
import web

EXEMPLE = Path(__file__).resolve().parent.parent / "exemples" / "pont.png"


def _png(largeur=240, hauteur=120, mode="RGB"):
    """Petite image réaliste : la moitié gauche (photo d'origine) de l'exemple du pont."""
    with Image.open(EXEMPLE) as img:
        img = img.crop((12, 22, 1004, 516)).convert(mode).resize((largeur, hauteur))
    tampon = io.BytesIO()
    img.save(tampon, "PNG")
    return tampon.getvalue()


@pytest.fixture
def client(tmp_path):
    with TestClient(web.creer_app(tmp_path / "travaux")) as c:
        yield c


def _creer(client, contenu=None, nom="mon pont.png"):
    reponse = client.post("/api/travaux", content=contenu or _png(),
                          headers={"X-Nom-Fichier": "mon%20pont.png" if nom == "mon pont.png" else nom})
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _attendre(client, id_travail, delai=180):
    fin = time.time() + delai
    while time.time() < fin:
        infos = client.get(f"/api/travaux/{id_travail}").json()
        if infos["execution"]["etat"] not in web.ETATS_ACTIFS:
            return infos
        time.sleep(0.3)
    raise AssertionError("le calcul ne s'est pas terminé à temps")


# --- paramètres et validation ---------------------------------------------------
def test_parametres_par_defaut_alignes_sur_la_cli(client):
    donnees = client.get("/api/parametres").json()
    assert {p["cle"]: p["defaut"] for p in donnees["parametres"]} == cli.DEFAUTS
    assert [e["dossier"] for e in donnees["etapes"]] == web.DOSSIERS_ETAPES


def test_valider_parametres():
    assert web.valider_parametres({}) == cli.DEFAUTS
    assert web.valider_parametres({"decouper_nombre": 3.0})["decouper_nombre"] == 3
    for mauvais in [{"decouper_nombre": 11}, {"decouper_nombre": 2.5}, {"norm_rayon": "5"},
                    {"gcode_inverser_y": 1}, {"graver_rayon": True}, {"inconnu": 1}, [1, 2]]:
        with pytest.raises(web.HTTPException) as erreur:
            web.valider_parametres(mauvais)
        assert erreur.value.status_code == 400


# --- création de travaux ----------------------------------------------------------
def test_creation_travail(client):
    infos = _creer(client, _png(mode="RGBA"))
    assert (infos["nom"], infos["largeur_px"], infos["hauteur_px"]) == ("mon pont.png", 240, 120)
    assert infos["execution"] is None and infos["parametres"] == cli.DEFAUTS
    entree = Image.open(io.BytesIO(client.get(f"/api/travaux/{infos['id']}/entree.png").content))
    assert entree.mode == "RGB"  # transparence aplatie sur blanc
    liste = client.get("/api/travaux").json()
    assert [t["id"] for t in liste] == [infos["id"]] and liste[0]["apercu"] is False


@pytest.mark.parametrize("contenu, code", [(b"", 400), (b"pas une image", 400)])
def test_envoi_refuse(client, contenu, code):
    assert client.post("/api/travaux", content=contenu).status_code == code


def test_limites_envoi(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "TAILLE_MAX_ENVOI", 2000)
    monkeypatch.setattr(web, "PIXELS_MAX", 100 * 100)
    with TestClient(web.creer_app(tmp_path / "travaux")) as client:
        assert client.post("/api/travaux", content=b"x" * 5000).status_code == 413
        petite = io.BytesIO()
        Image.new("RGB", (200, 100)).save(petite, "PNG")  # 20 000 px > 10 000, ~300 octets
        reponse = client.post("/api/travaux", content=petite.getvalue())
        assert reponse.status_code == 413 and "trop grande" in reponse.json()["erreur"]


def test_chemins_proteges(client):
    id_travail = _creer(client)["id"]
    for url in ["/api/travaux/..%2F..%2Fetc", "/api/travaux/pas-un-id",
                f"/api/travaux/{id_travail}/fichiers/..%2F..%2F/etat.json",
                f"/api/travaux/{id_travail}/fichiers/1-cmyk/..",
                f"/api/travaux/{id_travail}/fichiers/1-cmyk/.etat.json",
                f"/api/travaux/{id_travail}/fichiers/etat.json/x",
                f"/api/travaux/{id_travail}/miniatures/..%2F/entree.png"]:
        assert client.get(url).status_code == 404, url
    # Le client HTTP normalise les "..", qui n'atteignent donc jamais la route :
    # on vérifie la validation elle-même, comme la verrait un client qui ne
    # normalise pas (curl --path-as-is).
    travaux = client.app.state.travaux
    (travaux.dossier / "secret.txt").write_text("hors du travail")  # cible existante un niveau au-dessus
    for dossier, nom in [(".", "etat.json"), ("..", "secret.txt"), ("..", id_travail), ("1-cmyk", ".."),
                         ("1-cmyk", ".miniatures"), ("entree", "entree.png"), ("7-gcode", "x.gcode")]:
        with pytest.raises(web.HTTPException) as erreur:
            web._fichier_du_travail(travaux, id_travail, dossier, nom)
        assert erreur.value.status_code == 404


def test_execution_refusee(client):
    id_travail = _creer(client)["id"]
    url = f"/api/travaux/{id_travail}/executer"
    assert client.post(url, json={"commande": "rm -rf"}).status_code == 400
    assert client.post(url, json={"commande": "tout", "parametres": {"norm_rayon": 999}}).status_code == 400
    assert client.post(url, content=b"{pas du json").status_code == 400
    assert client.post("/api/travaux/20260101-000000-abcdef/executer", json={"commande": "tout"}).status_code == 404


# --- exécution --------------------------------------------------------------------
def test_pipeline_complet(client):
    id_travail = _creer(client)["id"]
    parametres = {"redimensionner_facteur_echelle": 1.0, "previsualiser_dpi": 50}
    reponse = client.post(f"/api/travaux/{id_travail}/executer", json={"commande": "tout", "parametres": parametres})
    assert reponse.status_code == 202
    assert client.post(f"/api/travaux/{id_travail}/executer", json={"commande": "tout"}).status_code == 409

    # le flux SSE se termine avec l'exécution et annonce la progression
    messages = []
    with client.stream("GET", f"/api/travaux/{id_travail}/journal") as flux:
        for ligne in flux.iter_lines():
            if ligne.startswith("data: {\""):
                messages.append(json.loads(ligne[6:]))
    assert messages[-1]["execution"]["etat"] == "termine"
    assert messages[-1]["execution"]["etape"] == 8
    lignes = [l for m in messages for l in m["lignes"]]
    assert any(l.startswith("[3/8]") for l in lignes) and "✅ pipeline terminé avec succès" in lignes
    # les workers écrivent en parallèle : aucune ligne ne doit en contenir deux
    assert not [l for l in lignes if l.count(" : image_") > 1]

    infos = client.get(f"/api/travaux/{id_travail}").json()
    assert infos["parametres"]["previsualiser_dpi"] == 50
    assert infos["execution"]["etape"] == 8  # progression aussi connue hors du flux SSE
    assert set(web.DOSSIERS_ETAPES) <= set(infos["fichiers"])
    assert "compose.png" in infos["fichiers"]["8-preview"]
    gcodes = infos["fichiers"]["7-gcode"]
    assert gcodes

    archive = zipfile.ZipFile(io.BytesIO(client.get(f"/api/travaux/{id_travail}/gcode.zip").content))
    assert sorted(archive.namelist()) == sorted(gcodes + ["parametres.json"])
    assert json.loads(archive.read("parametres.json"))["previsualiser_dpi"] == 50
    miniature = client.get(f"/api/travaux/{id_travail}/miniatures/8-preview/compose.png?taille=100")
    assert max(Image.open(io.BytesIO(miniature.content)).size) == 100
    svg = client.get(f"/api/travaux/{id_travail}/fichiers/5-vector/{infos['fichiers']['5-vector'][0]}")
    assert "sandbox" in svg.headers["content-security-policy"]
    assert client.get("/api/travaux").json()[0]["apercu"] is True

    # relancer une seule étape avec d'autres paramètres
    client.post(f"/api/travaux/{id_travail}/executer",
                json={"commande": "decouper", "parametres": {**parametres, "decouper_nombre": 3}})
    infos = _attendre(client, id_travail)
    assert infos["execution"]["etat"] == "termine"
    assert len(infos["fichiers"]["2-cut"]) == 4 * 3

    assert client.delete(f"/api/travaux/{id_travail}").status_code == 204
    assert client.get(f"/api/travaux/{id_travail}").status_code == 404


def test_annulation_et_file_d_attente(client):
    a = _creer(client)["id"]
    b = _creer(client)["id"]
    client.post(f"/api/travaux/{a}/executer", json={"commande": "tout"})
    infos_b = client.post(f"/api/travaux/{b}/executer", json={"commande": "tout"}).json()
    assert infos_b["execution"]["etat"] == "en_attente" and infos_b["position"] == 1

    assert client.delete(f"/api/travaux/{b}").status_code == 409  # en attente : pas de suppression
    assert client.post(f"/api/travaux/{b}/annuler").json()["execution"]["etat"] == "annule"
    assert client.post(f"/api/travaux/{b}/annuler").status_code == 409

    # attendre que A tourne vraiment, puis l'annuler : le groupe de processus est tué
    fin = time.time() + 30
    while client.get(f"/api/travaux/{a}").json()["execution"]["etat"] != "en_cours" and time.time() < fin:
        time.sleep(0.1)
    time.sleep(1)
    client.post(f"/api/travaux/{a}/annuler")
    infos_a = _attendre(client, a, delai=30)
    assert infos_a["execution"]["etat"] == "annule" and infos_a["execution"]["code"] < 0
    # B, annulé pendant l'attente, n'a jamais démarré
    assert client.get(f"/api/travaux/{b}").json()["execution"]["debut"] is None


def test_calcul_arrete_de_l_exterieur(client):
    # systemd arrête le service : SIGTERM à tous ses processus, calcul compris
    id_travail = _creer(client)["id"]
    client.post(f"/api/travaux/{id_travail}/executer", json={"commande": "tout"})
    processus = client.app.state.travaux.processus
    fin = time.time() + 30
    while id_travail not in processus and time.time() < fin:
        time.sleep(0.1)
    os.killpg(processus[id_travail].pid, signal.SIGTERM)
    execution = _attendre(client, id_travail, delai=30)["execution"]
    assert execution["etat"] == "interrompu" and execution["code"] == -signal.SIGTERM


def test_travaux_interrompus_au_redemarrage(tmp_path):
    with TestClient(web.creer_app(tmp_path / "travaux")) as client:
        id_travail = _creer(client)["id"]
    etat_json = tmp_path / "travaux" / id_travail / "etat.json"
    etat = json.loads(etat_json.read_text())
    etat["execution"] = {"commande": "tout", "etat": "en_cours", "demande": None,
                         "debut": None, "fin": None, "code": None, "etape": None}
    etat_json.write_text(json.dumps(etat))
    with TestClient(web.creer_app(tmp_path / "travaux")) as client:
        assert client.get(f"/api/travaux/{id_travail}").json()["execution"]["etat"] == "interrompu"


# --- réduction à l'envoi ----------------------------------------------------------
def test_reduction_a_l_envoi(client):
    infos = client.post("/api/travaux?cote_max=100", content=_png(240, 120)).json()
    assert (infos["largeur_px"], infos["hauteur_px"], infos["taille_origine"]) == (100, 50, [240, 120])
    entree = Image.open(io.BytesIO(client.get(f"/api/travaux/{infos['id']}/entree.png").content))
    assert entree.size == (100, 50)
    # jamais d'agrandissement ; 0 = pas de réduction
    for option in ["?cote_max=5000", "?cote_max=0", ""]:
        infos = client.post(f"/api/travaux{option}", content=_png(240, 120)).json()
        assert (infos["largeur_px"], infos["hauteur_px"]) == (240, 120), option


@pytest.mark.parametrize("valeur", ["abc", "50", "20000", "-100"])
def test_reduction_valeur_refusee(client, valeur):
    assert client.post(f"/api/travaux?cote_max={valeur}", content=_png()).status_code == 400


def test_reduction_et_limites_de_pixels(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "PIXELS_MAX", 100 * 100)
    with TestClient(web.creer_app(tmp_path / "travaux")) as client:
        grande = _png(200, 100)  # 20 000 px : trop pour l'image de travail…
        reponse = client.post("/api/travaux", content=grande)
        assert reponse.status_code == 413 and "réduction" in reponse.json()["erreur"]
        # … acceptée une fois réduite à 100 × 50
        assert client.post("/api/travaux?cote_max=100", content=grande).status_code == 201
        # la limite sur l'original s'applique même avec réduction (mémoire)
        monkeypatch.setattr(web, "PIXELS_MAX_ORIGINAL", 150 * 100)
        assert client.post("/api/travaux?cote_max=100", content=grande).status_code == 413


# --- mise à jour de la page -------------------------------------------------------
def test_version_interface(tmp_path):
    (tmp_path / "app.js").write_text("console.log(1)")
    (tmp_path / "index.html").write_text("<p>")
    v1 = web.version_interface(tmp_path)
    assert web.version_interface(tmp_path) == v1
    (tmp_path / "app.js").write_text("console.log(2)")
    assert web.version_interface(tmp_path) != v1


def test_version_et_cache_des_fichiers_de_la_page(client):
    version = client.get("/api/version")
    assert version.json()["version"] == web.version_interface() == client.get("/api/parametres").json()["version"]
    assert version.headers["cache-control"] == "no-store"
    for chemin in ["/", "/app.js", "/style.css"]:
        reponse = client.get(chemin)
        assert reponse.status_code == 200 and reponse.headers["cache-control"] == "no-cache", chemin
        revalidation = client.get(chemin, headers={"If-None-Match": reponse.headers["etag"]})
        assert revalidation.status_code == 304 and revalidation.headers["cache-control"] == "no-cache", chemin
