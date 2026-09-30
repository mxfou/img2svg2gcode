#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Interface web du pipeline bitmap2vector2gcode (Starlette + uvicorn).

Lancement :

    uv run web.py                           # http://127.0.0.1:8765
    uv run web.py --port 9000 --dossier-travaux ~/travaux

Le serveur n'écoute que sur 127.0.0.1 par défaut : pour y accéder depuis
le tailnet, l'exposer avec `tailscale serve` (voir le README).

Chaque image envoyée crée un *travail* : un dossier (dans `travaux/` par
défaut) qui contient l'image d'entrée, l'état, les paramètres, le journal
et les sous-dossiers `1-cmyk` … `8-preview` produits par le pipeline.

Les calculs passent par une file d'attente et sont exécutés un par un
(le pipeline occupe déjà tous les cœurs), chacun dans un processus séparé
qui lance `cli.py` avec un fichier de config : même code que la ligne de
commande, et un plantage du calcul ne fait pas tomber le serveur.
"""

import argparse
import asyncio
import contextlib
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import signal
import sys
import time
import urllib.parse
import zipfile
from pathlib import Path

from PIL import Image, ImageOps
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

try:
    from . import cli  # contexte package (pdoc)
except ImportError:
    import cli         # exécution directe (uv run)

RACINE = Path(__file__).resolve().parent

# Étapes du pipeline : (sous-commande de cli.py, dossier produit, libellé)
ETAPES = [
    ("normaliser", "1-cmyk", "séparation CMJN + normalisation"),
    ("decouper", "2-cut", "découpage par intensité"),
    ("graver", "3-engrave", "gravure"),
    ("deformer", "4-deform", "déformation + tramage"),
    ("vectoriser", "5-vector", "vectorisation"),
    ("redimensionner", "6-resize", "redimensionnement + optimisation"),
    ("gcode", "7-gcode", "génération du G-code"),
    ("previsualiser", "8-preview", "prévisualisation"),
]
COMMANDES = {"tout"} | {commande for commande, _, _ in ETAPES}
DOSSIERS_ETAPES = [dossier for _, dossier, _ in ETAPES]
# Sous-commandes de cli.py qui ont besoin de l'image d'entrée (-e)
COMMANDES_AVEC_ENTREE = {"tout", "normaliser"}

# Paramètres réglables : mêmes bornes que les curseurs de la GUI, valeurs
# par défaut reprises de cli.DEFAUTS. "etape" = sous-commande qui s'en sert.
PARAMETRES = [
    {"cle": "norm_amplitude", "etape": "normaliser", "libelle": "amplitude", "type": "reel", "min": 0, "max": 60, "pas": 0.01},
    {"cle": "norm_rayon", "etape": "normaliser", "libelle": "rayon", "type": "entier", "min": 1, "max": 64, "pas": 1},
    {"cle": "norm_lissage", "etape": "normaliser", "libelle": "lissage moyen", "type": "reel", "min": 0, "max": 60, "pas": 0.01},
    {"cle": "decouper_nombre", "etape": "decouper", "libelle": "nombre d'images par couleur", "type": "entier", "min": 2, "max": 10, "pas": 1},
    {"cle": "graver_rayon", "etape": "graver", "libelle": "épaisseur des traits", "type": "reel", "min": 0, "max": 2, "pas": 0.01},
    {"cle": "redimensionner_facteur_echelle", "etape": "redimensionner", "libelle": "facteur d'échelle (mm par pixel)", "type": "reel", "min": 0.001, "max": 100, "pas": 0.001},
    {"cle": "redimensionner_taille_nettoyage", "etape": "redimensionner", "libelle": "longueur min des traits (mm)", "type": "reel", "min": 1, "max": 50, "pas": 0.1},
    {"cle": "redimensionner_taille_approximation", "etape": "redimensionner", "libelle": "longueur des segments d'approximation des courbes (mm)", "type": "reel", "min": 0.01, "max": 2, "pas": 0.01},
    {"cle": "gcode_hauteur_deplacement", "etape": "gcode", "libelle": "hauteur de déplacement à vide (mm)", "type": "reel", "min": 1, "max": 15, "pas": 1},
    {"cle": "gcode_hauteur_ecriture", "etape": "gcode", "libelle": "hauteur d'écriture (mm)", "type": "reel", "min": -10, "max": 0, "pas": 0.1},
    {"cle": "gcode_inverser_y", "etape": "gcode", "libelle": "inverser l'axe Y (origine en bas à gauche, convention CNC)", "type": "booleen"},
    {"cle": "previsualiser_dpi", "etape": "previsualiser", "libelle": "résolution (dpi)", "type": "entier", "min": 50, "max": 600, "pas": 10},
    {"cle": "previsualiser_marge_mm", "etape": "previsualiser", "libelle": "marge (mm)", "type": "reel", "min": 0, "max": 50, "pas": 1},
    {"cle": "previsualiser_epaisseur_trait_mm", "etape": "previsualiser", "libelle": "largeur du trait du stylo (mm)", "type": "reel", "min": 0.05, "max": 3, "pas": 0.05},
    {"cle": "previsualiser_afficher_deplacements", "etape": "previsualiser", "libelle": "afficher les déplacements à vide", "type": "booleen"},
]
for _p in PARAMETRES:
    _p["defaut"] = cli.DEFAUTS[_p["cle"]]

TAILLE_MAX_ENVOI = 40 * 1024 * 1024  # octets
PIXELS_MAX = 25_000_000              # image de travail (~5000 × 5000) : au-delà, des heures de calcul
PIXELS_MAX_ORIGINAL = 100_000_000    # image envoyée, avant réduction (mémoire)
COTE_MAX_BORNES = (100, 10_000)      # bornes de l'option de réduction à l'envoi (px)
TAILLE_MINIATURE_MAX = 2000          # px

MOTIF_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
MOTIF_NOM_FICHIER = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
MOTIF_PROGRESSION = re.compile(r"^\[(\d)/8\]")

ETATS_ACTIFS = {"en_attente", "en_cours"}


def _maintenant():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def version_interface(dossier=RACINE / "web"):
    """Empreinte des fichiers de la page : change dès que l'un d'eux est
    modifié sur le disque. Une page ouverte la compare à celle qu'elle a
    chargée pour savoir si elle doit se recharger."""
    empreinte = hashlib.sha256()
    for fichier in sorted(Path(dossier).iterdir()):
        if fichier.is_file():
            empreinte.update(fichier.name.encode())
            empreinte.update(fichier.read_bytes())
    return empreinte.hexdigest()[:12]


class FichiersPage(StaticFiles):
    """Fichiers de la page servis avec Cache-Control: no-cache : le navigateur
    les revalide (ETag) à chaque chargement au lieu de garder une ancienne
    version en cache après une mise à jour."""

    def file_response(self, *args, **kwargs):
        reponse = super().file_response(*args, **kwargs)
        reponse.headers["Cache-Control"] = "no-cache"
        return reponse


def _derniere_etape(texte):
    """Numéro du dernier marqueur "[n/8]" (affiché par `cli.py tout`)."""
    etape = None
    for ligne in texte.splitlines():
        if m := MOTIF_PROGRESSION.match(ligne):
            etape = int(m.group(1))
    return etape


# ---------------------------------------------------------------------------
# Gestion des travaux (dossiers + file d'exécution)
# ---------------------------------------------------------------------------
class Travaux:
    """Dossiers des travaux, file d'attente et processus de calcul en cours."""

    def __init__(self, dossier):
        self.dossier = Path(dossier).resolve()
        self.file = None          # asyncio.Queue, créée dans la boucle du serveur
        self.attente = []         # ids en attente, dans l'ordre (pour la position)
        self.processus = {}       # id -> asyncio.subprocess.Process
        self.annulations = set()  # ids dont l'annulation a été demandée

    # --- chemins et état --------------------------------------------------
    def chemin(self, id_travail):
        """Dossier d'un travail ; 404 si l'identifiant est invalide ou inconnu."""
        if not MOTIF_ID.match(id_travail):
            raise HTTPException(404, "travail inconnu")
        chemin = self.dossier / id_travail
        if not (chemin / "etat.json").is_file():
            raise HTTPException(404, "travail inconnu")
        return chemin

    def lire_etat(self, id_travail):
        with open(self.chemin(id_travail) / "etat.json", encoding="utf-8") as f:
            return json.load(f)

    def ecrire_etat(self, id_travail, etat):
        chemin = self.dossier / id_travail / "etat.json"
        temporaire = chemin.with_suffix(".tmp")
        with open(temporaire, "w", encoding="utf-8") as f:
            json.dump(etat, f, indent=2, ensure_ascii=False)
        os.replace(temporaire, chemin)  # écriture atomique

    def infos(self, id_travail):
        """État d'un travail + fichiers produits par chaque étape."""
        etat = self.lire_etat(id_travail)
        dossier = self.chemin(id_travail)
        fichiers = {}
        for nom_dossier in DOSSIERS_ETAPES:
            sous_dossier = dossier / nom_dossier
            if sous_dossier.is_dir():
                fichiers[nom_dossier] = sorted(
                    f.name for f in sous_dossier.iterdir()
                    if f.is_file() and MOTIF_NOM_FICHIER.match(f.name))
        etat["fichiers"] = fichiers
        execution = etat.get("execution")
        if execution:
            journal = dossier / "journal.txt"
            if execution["commande"] == "tout" and journal.is_file():
                execution["etape"] = _derniere_etape(journal.read_text(encoding="utf-8", errors="replace"))
            if execution["etat"] == "en_attente":
                etat["position"] = self.position(id_travail)
        return etat

    def position(self, id_travail):
        """Rang dans la file d'attente (1 = prochain à passer)."""
        return self.attente.index(id_travail) + 1 if id_travail in self.attente else None

    def lister(self):
        resumes = []
        if not self.dossier.is_dir():
            return resumes
        for chemin in sorted(self.dossier.iterdir(), reverse=True):
            if MOTIF_ID.match(chemin.name) and (chemin / "etat.json").is_file():
                etat = self.lire_etat(chemin.name)
                resume = {cle: etat.get(cle) for cle in
                          ("id", "nom", "largeur_px", "hauteur_px", "cree", "execution")}
                resume["apercu"] = (chemin / "8-preview" / "compose.png").is_file()
                resumes.append(resume)
        return resumes

    # --- création ----------------------------------------------------------
    def creer(self, fichier_recu, nom_original, cote_max=None):
        """Valide l'image reçue, la normalise en PNG RGB et crée le travail.

        Si `cote_max` est donné, l'image est réduite (jamais agrandie) pour
        que son plus grand côté mesure au plus `cote_max` pixels : les étapes
        1 à 5 travaillent pixel par pixel, une photo de téléphone de 12 Mpx
        prend sinon une vingtaine de minutes. Appelée dans un thread (PIL est
        bloquant)."""
        try:
            with Image.open(fichier_recu) as img:
                largeur, hauteur = img.size
                if largeur * hauteur > PIXELS_MAX_ORIGINAL:
                    raise HTTPException(413, f"image trop grande ({largeur}×{hauteur} px, "
                                             f"maximum {PIXELS_MAX_ORIGINAL // 1_000_000} Mpx)")
                img.load()
                img = ImageOps.exif_transpose(img)  # photos de téléphone
                if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                    img = img.convert("RGBA")
                    fond = Image.new("RGBA", img.size, (255, 255, 255, 255))
                    img = Image.alpha_composite(fond, img)
                img = img.convert("RGB")
        except HTTPException:
            raise
        except Image.DecompressionBombError:
            raise HTTPException(413, f"image trop grande (maximum {PIXELS_MAX_ORIGINAL // 1_000_000} Mpx)")
        except Exception:
            raise HTTPException(400, "fichier illisible : ce n'est pas une image reconnue")

        taille_origine = img.size  # après redressement EXIF
        if cote_max and max(img.size) > cote_max:
            rapport = cote_max / max(img.size)
            nouvelle = (max(1, round(img.width * rapport)), max(1, round(img.height * rapport)))
            img = img.resize(nouvelle, Image.LANCZOS)
        if img.width * img.height > PIXELS_MAX:
            raise HTTPException(413, f"image trop grande ({img.width}×{img.height} px, maximum "
                                     f"{PIXELS_MAX // 1_000_000} Mpx) : choisir une réduction à l'envoi")

        id_travail = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
        dossier = self.dossier / id_travail
        dossier.mkdir(parents=True)
        img.save(dossier / "entree.png")
        parametres = {p["cle"]: p["defaut"] for p in PARAMETRES}
        etat = {
            "id": id_travail,
            "nom": nom_original,
            "largeur_px": img.width,
            "hauteur_px": img.height,
            "taille_origine": list(taille_origine),
            "cree": _maintenant(),
            "parametres": parametres,
            "execution": None,
        }
        self.ecrire_etat(id_travail, etat)
        return id_travail

    # --- exécution ---------------------------------------------------------
    def demander(self, id_travail, commande, parametres):
        """Met une exécution en file d'attente."""
        etat = self.lire_etat(id_travail)
        if etat["execution"] and etat["execution"]["etat"] in ETATS_ACTIFS:
            raise HTTPException(409, "un calcul est déjà prévu ou en cours pour ce travail")
        etat["parametres"] = parametres
        etat["execution"] = {"commande": commande, "etat": "en_attente", "demande": _maintenant(),
                             "debut": None, "fin": None, "code": None, "etape": None}
        dossier = self.chemin(id_travail)
        with open(dossier / "parametres.json", "w", encoding="utf-8") as f:
            json.dump(parametres, f, indent=2, ensure_ascii=False)
        (dossier / "journal.txt").unlink(missing_ok=True)  # journal de l'exécution précédente
        self.ecrire_etat(id_travail, etat)
        self.annulations.discard(id_travail)
        self.attente.append(id_travail)
        self.file.put_nowait(id_travail)

    def annuler(self, id_travail):
        etat = self.lire_etat(id_travail)
        execution = etat["execution"]
        if not execution or execution["etat"] not in ETATS_ACTIFS:
            raise HTTPException(409, "aucun calcul à annuler")
        self.annulations.add(id_travail)
        processus = self.processus.get(id_travail)
        if processus is None:
            # encore en attente : l'exécuteur l'ignorera
            if id_travail in self.attente:
                self.attente.remove(id_travail)
            execution.update(etat="annule", fin=_maintenant())
            self.ecrire_etat(id_travail, etat)
        else:
            _tuer(processus)

    async def executeur(self):
        """Tâche de fond : exécute les travaux de la file un par un."""
        while True:
            id_travail = await self.file.get()
            if id_travail in self.attente:
                self.attente.remove(id_travail)
            try:
                await self._executer(id_travail)
            except Exception as erreur:  # le serveur doit survivre à tout
                print(f"⚠️  travail {id_travail} : {erreur!r}", file=sys.stderr)
                with contextlib.suppress(Exception):
                    etat = self.lire_etat(id_travail)
                    etat["execution"].update(etat="echec", fin=_maintenant())
                    self.ecrire_etat(id_travail, etat)
            finally:
                self.processus.pop(id_travail, None)
                self.file.task_done()

    async def _executer(self, id_travail):
        dossier = self.chemin(id_travail)
        etat = self.lire_etat(id_travail)
        execution = etat["execution"]
        # Annulé pendant l'attente, ou doublon dans la file (annulé puis
        # relancé avant d'avoir été dépilé) : rien à faire.
        if not execution or execution["etat"] != "en_attente":
            return
        commande = execution["commande"]
        arguments = [sys.executable, str(RACINE / "cli.py"), commande,
                     "-s", str(dossier), "-c", str(dossier / "parametres.json")]
        if commande in COMMANDES_AVEC_ENTREE:
            arguments += ["-e", str(dossier / "entree.png")]

        execution.update(etat="en_cours", debut=_maintenant())
        self.ecrire_etat(id_travail, etat)
        with open(dossier / "journal.txt", "wb") as journal:
            processus = await asyncio.create_subprocess_exec(
                *arguments, stdout=journal, stderr=asyncio.subprocess.STDOUT,
                cwd=str(RACINE), start_new_session=True)  # groupe à part : annulation propre
            self.processus[id_travail] = processus
            code = await processus.wait()

        etat = self.lire_etat(id_travail)
        if id_travail in self.annulations:
            self.annulations.discard(id_travail)
            resultat = "annule"
        else:
            resultat = "termine" if code == 0 else "echec"
        etat["execution"].update(etat=resultat, fin=_maintenant(), code=code)
        self.ecrire_etat(id_travail, etat)

    def marquer_interrompus(self):
        """Au démarrage : les calculs qui tournaient quand le serveur s'est
        arrêté ne reprendront pas."""
        for resume in self.lister():
            execution = resume["execution"]
            if execution and execution["etat"] in ETATS_ACTIFS:
                etat = self.lire_etat(resume["id"])
                etat["execution"].update(etat="interrompu", fin=_maintenant())
                self.ecrire_etat(resume["id"], etat)


def _tuer(processus, delai=5):
    """Arrête un calcul et tous ses workers (même groupe de processus) :
    SIGTERM, puis SIGKILL s'il tourne encore après `delai` secondes."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(processus.pid, signal.SIGTERM)

    async def achever():
        await asyncio.sleep(delai)
        if processus.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(processus.pid, signal.SIGKILL)
    asyncio.get_running_loop().create_task(achever())


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def valider_parametres(recus):
    """Vérifie type et bornes de chaque paramètre ; les absents prennent
    leur valeur par défaut. Retourne le dict complet ou lève une 400."""
    if not isinstance(recus, dict):
        raise HTTPException(400, "paramètres invalides")
    inconnus = set(recus) - {p["cle"] for p in PARAMETRES}
    if inconnus:
        raise HTTPException(400, f"paramètres inconnus : {', '.join(sorted(inconnus))}")
    valides = {}
    for p in PARAMETRES:
        valeur = recus.get(p["cle"], p["defaut"])
        if p["type"] == "booleen":
            if not isinstance(valeur, bool):
                raise HTTPException(400, f"{p['cle']} : booléen attendu")
        else:
            if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
                raise HTTPException(400, f"{p['cle']} : nombre attendu")
            if p["type"] == "entier":
                if valeur != int(valeur):
                    raise HTTPException(400, f"{p['cle']} : entier attendu")
                valeur = int(valeur)
            if not p["min"] <= valeur <= p["max"]:
                raise HTTPException(400, f"{p['cle']} : doit être entre {p['min']} et {p['max']}")
        valides[p["cle"]] = valeur
    return valides


def _fichier_du_travail(travaux, id_travail, dossier, nom):
    """Chemin d'un fichier produit par une étape, après validation stricte
    (pas de ../, dossier d'étape connu, fichier existant)."""
    chemin_travail = travaux.chemin(id_travail)
    if dossier not in DOSSIERS_ETAPES or not MOTIF_NOM_FICHIER.match(nom):
        raise HTTPException(404, "fichier inconnu")
    chemin = chemin_travail / dossier / nom
    if not chemin.is_file():
        raise HTTPException(404, "fichier inconnu")
    return chemin


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
async def route_version(request):
    return JSONResponse({"version": await run_in_threadpool(version_interface)},
                        headers={"Cache-Control": "no-store"})


async def route_parametres(request):
    return JSONResponse({
        "version": await run_in_threadpool(version_interface),
        "parametres": PARAMETRES,
        "etapes": [{"commande": c, "dossier": d, "libelle": l} for c, d, l in ETAPES],
        "limites": {"taille_max_envoi": TAILLE_MAX_ENVOI, "pixels_max": PIXELS_MAX,
                    "pixels_max_original": PIXELS_MAX_ORIGINAL, "cote_max": COTE_MAX_BORNES},
    })


async def route_liste(request):
    travaux = request.app.state.travaux
    return JSONResponse(await run_in_threadpool(travaux.lister))


async def route_creer(request):
    """Reçoit l'image brute dans le corps de la requête (pas de multipart) ;
    le nom d'origine est dans l'en-tête X-Nom-Fichier (encodé URL). Option
    ?cote_max=N : réduire l'image pour que son plus grand côté fasse au plus
    N pixels (0 ou absent : pas de réduction)."""
    travaux = request.app.state.travaux
    try:
        cote_max = int(request.query_params.get("cote_max", "0"))
    except ValueError:
        raise HTTPException(400, "cote_max : entier attendu")
    if cote_max and not COTE_MAX_BORNES[0] <= cote_max <= COTE_MAX_BORNES[1]:
        raise HTTPException(400, f"cote_max : doit être entre {COTE_MAX_BORNES[0]} et {COTE_MAX_BORNES[1]} "
                                 "(ou 0 pour ne pas réduire)")
    travaux.dossier.mkdir(parents=True, exist_ok=True)
    nom = urllib.parse.unquote(request.headers.get("x-nom-fichier", "image"))
    nom = os.path.basename(nom)[:120] or "image"
    temporaire = travaux.dossier / f".envoi-{secrets.token_hex(8)}"
    try:
        with open(temporaire, "wb") as f:
            async for morceau in request.stream():
                f.write(morceau)
        if temporaire.stat().st_size == 0:
            raise HTTPException(400, "fichier vide")
        id_travail = await run_in_threadpool(travaux.creer, temporaire, nom, cote_max or None)
    finally:
        temporaire.unlink(missing_ok=True)
    return JSONResponse(await run_in_threadpool(travaux.infos, id_travail), status_code=201)


async def route_travail(request):
    travaux = request.app.state.travaux
    return JSONResponse(await run_in_threadpool(travaux.infos, request.path_params["id"]))


async def route_supprimer(request):
    travaux = request.app.state.travaux
    id_travail = request.path_params["id"]
    etat = travaux.lire_etat(id_travail)
    if etat["execution"] and etat["execution"]["etat"] in ETATS_ACTIFS:
        raise HTTPException(409, "annuler d'abord le calcul en cours")
    await run_in_threadpool(shutil.rmtree, travaux.chemin(id_travail))
    return Response(status_code=204)


async def route_executer(request):
    travaux = request.app.state.travaux
    id_travail = request.path_params["id"]
    travaux.chemin(id_travail)
    try:
        corps = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "JSON invalide")
    commande = corps.get("commande") if isinstance(corps, dict) else None
    if commande not in COMMANDES:
        raise HTTPException(400, f"commande inconnue (attendu : {', '.join(sorted(COMMANDES))})")
    parametres = valider_parametres(corps.get("parametres", {}))
    travaux.demander(id_travail, commande, parametres)
    return JSONResponse(await run_in_threadpool(travaux.infos, id_travail), status_code=202)


async def route_annuler(request):
    travaux = request.app.state.travaux
    travaux.annuler(request.path_params["id"])
    return JSONResponse(await run_in_threadpool(travaux.infos, request.path_params["id"]))


async def route_journal(request):
    """Flux SSE : lignes du journal au fil de l'eau + état de l'exécution.

    Chaque évènement porte en `id` la position atteinte dans le journal :
    après une coupure, le navigateur renvoie `Last-Event-ID` et le flux
    reprend là où il s'était arrêté. Le flux se termine avec l'exécution.
    """
    travaux = request.app.state.travaux
    id_travail = request.path_params["id"]
    chemin_journal = travaux.chemin(id_travail) / "journal.txt"
    try:
        position = int(request.headers.get("last-event-id", "0"))
    except ValueError:
        position = 0

    async def flux():
        nonlocal position
        dernier_etat = None
        etape = None
        if position and chemin_journal.is_file():  # reprise après coupure
            with open(chemin_journal, "rb") as f:
                etape = _derniere_etape(f.read(position).decode("utf-8", errors="replace"))
        while True:
            etat = travaux.lire_etat(id_travail)
            execution = etat["execution"] or {}
            fini = execution.get("etat") not in ETATS_ACTIFS
            lignes, reinitialiser = [], False
            if chemin_journal.is_file():
                if chemin_journal.stat().st_size < position:  # nouvelle exécution
                    position, etape, reinitialiser = 0, None, True
                with open(chemin_journal, "rb") as f:
                    f.seek(position)
                    donnees = f.read()
                # on n'envoie que des lignes complètes, sauf en fin d'exécution
                coupure = len(donnees) if fini else donnees.rfind(b"\n") + 1
                texte = donnees[:coupure].decode("utf-8", errors="replace")
                lignes = texte.splitlines()
                etape = _derniere_etape(texte) or etape
                position += coupure
            execution = {**execution, "etape": etape}
            if lignes or reinitialiser or execution != dernier_etat:
                message = {"lignes": lignes, "reinitialiser": reinitialiser,
                           "execution": execution, "position": travaux.position(id_travail)}
                yield f"id: {position}\ndata: {json.dumps(message, ensure_ascii=False)}\n\n"
                dernier_etat = execution
            if fini:
                yield "event: fin\ndata: {}\n\n"
                return
            if await request.is_disconnected():
                return
            await asyncio.sleep(0.4)

    return StreamingResponse(flux(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def route_fichier(request):
    p = request.path_params
    chemin = _fichier_du_travail(request.app.state.travaux, p["id"], p["dossier"], p["nom"])
    telecharger = request.query_params.get("telecharger") is not None
    entetes = {}
    if chemin.suffix == ".svg":  # un SVG ouvert directement ne doit rien exécuter
        entetes["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    return FileResponse(chemin, filename=chemin.name if telecharger else None, headers=entetes)


async def route_entree(request):
    chemin = request.app.state.travaux.chemin(request.path_params["id"]) / "entree.png"
    return FileResponse(chemin)


async def route_miniature(request):
    """PNG réduit (mis en cache) d'une image produite par une étape, ou de
    l'image d'entrée si dossier = "entree". Paramètre ?taille= (px)."""
    travaux = request.app.state.travaux
    p = request.path_params
    try:
        taille = max(64, min(TAILLE_MINIATURE_MAX, int(request.query_params.get("taille", "400"))))
    except ValueError:
        raise HTTPException(400, "taille invalide")
    if p["dossier"] == "entree":
        source = travaux.chemin(p["id"]) / "entree.png"
    else:
        source = _fichier_du_travail(travaux, p["id"], p["dossier"], p["nom"])
    if source.suffix.lower() != ".png":
        raise HTTPException(404, "miniature disponible pour les PNG uniquement")
    cache = travaux.chemin(p["id"]) / ".miniatures" / f"{p['dossier']}-{p['nom']}-{taille}.png"

    def fabriquer():
        if not cache.is_file() or cache.stat().st_mtime < source.stat().st_mtime:
            cache.parent.mkdir(exist_ok=True)
            with Image.open(source) as img:
                img.thumbnail((taille, taille))
                img.save(cache, optimize=True)
    await run_in_threadpool(fabriquer)
    return FileResponse(cache, headers={"Cache-Control": "no-cache"})


async def route_zip_gcode(request):
    travaux = request.app.state.travaux
    id_travail = request.path_params["id"]
    dossier = travaux.chemin(id_travail)
    gcodes = sorted((dossier / "7-gcode").glob("*.gcode")) if (dossier / "7-gcode").is_dir() else []
    if not gcodes:
        raise HTTPException(404, "aucun G-code pour ce travail")

    def compresser():
        tampon = io.BytesIO()
        with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as archive:
            for fichier in gcodes:
                archive.write(fichier, fichier.name)
            if (dossier / "parametres.json").is_file():
                archive.write(dossier / "parametres.json", "parametres.json")
        return tampon.getvalue()
    contenu = await run_in_threadpool(compresser)
    nom = f"gcode-{id_travail}.zip"
    return Response(contenu, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{nom}"'})


async def route_journal_brut(request):
    chemin = request.app.state.travaux.chemin(request.path_params["id"]) / "journal.txt"
    if not chemin.is_file():
        raise HTTPException(404, "pas encore de journal")
    return FileResponse(chemin, media_type="text/plain; charset=utf-8")


async def erreur_http(request, exc):
    return JSONResponse({"erreur": exc.detail}, status_code=exc.status_code)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
def creer_app(dossier_travaux=RACINE / "travaux"):
    """Construit l'application Starlette (utilisée aussi par les tests)."""
    travaux = Travaux(dossier_travaux)

    @contextlib.asynccontextmanager
    async def cycle_de_vie(app):
        travaux.file = asyncio.Queue()
        travaux.marquer_interrompus()
        tache = asyncio.create_task(travaux.executeur())
        app.state.travaux = travaux
        yield
        tache.cancel()
        for processus in list(travaux.processus.values()):
            with contextlib.suppress(ProcessLookupError):
                os.killpg(processus.pid, signal.SIGKILL)
        travaux.marquer_interrompus()

    id_ = "{id:str}"
    routes = [
        Route("/api/parametres", route_parametres),
        Route("/api/version", route_version),
        Route("/api/travaux", route_liste, methods=["GET"]),
        Route("/api/travaux", route_creer, methods=["POST"], max_body_size=TAILLE_MAX_ENVOI),
        Route(f"/api/travaux/{id_}", route_travail, methods=["GET"]),
        Route(f"/api/travaux/{id_}", route_supprimer, methods=["DELETE"]),
        Route(f"/api/travaux/{id_}/executer", route_executer, methods=["POST"], max_body_size=64 * 1024),
        Route(f"/api/travaux/{id_}/annuler", route_annuler, methods=["POST"]),
        Route(f"/api/travaux/{id_}/journal", route_journal),
        Route(f"/api/travaux/{id_}/journal.txt", route_journal_brut),
        Route(f"/api/travaux/{id_}/entree.png", route_entree),
        Route(f"/api/travaux/{id_}/gcode.zip", route_zip_gcode),
        Route(f"/api/travaux/{id_}/fichiers/{{dossier:str}}/{{nom:str}}", route_fichier),
        Route(f"/api/travaux/{id_}/miniatures/{{dossier:str}}/{{nom:str}}", route_miniature),
        Mount("/", FichiersPage(directory=RACINE / "web", html=True)),
    ]
    return Starlette(routes=routes, lifespan=cycle_de_vie,
                     exception_handlers={HTTPException: erreur_http})


def main():
    import uvicorn
    parser = argparse.ArgumentParser(description="Interface web de bitmap2vector2gcode")
    parser.add_argument("--hote", default="127.0.0.1",
                        help="adresse d'écoute (défaut : 127.0.0.1, exposer avec tailscale serve)")
    parser.add_argument("--port", type=int, default=8765, help="port (défaut : 8765)")
    parser.add_argument("--dossier-travaux", default=str(RACINE / "travaux"),
                        help="dossier où sont rangés les travaux (défaut : ./travaux)")
    args = parser.parse_args()
    print(f"🌐 interface web : http://{args.hote}:{args.port}  (travaux dans {args.dossier_travaux})")
    uvicorn.run(creer_app(args.dossier_travaux), host=args.hote, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
