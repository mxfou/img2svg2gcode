# bitmap2vector2gcode

Pipeline Python qui transforme une **image bitmap** (photo, JPG/PNG/BMP) en **G-code multicouches CMJN** prêt à être envoyé à un traceur (plotter à stylos, AxiDraw, machine CNC à faible profondeur, etc.).

Le résultat est une reproduction artistique de l'image sous forme de **gravure tramée** en quatre couleurs (cyan, magenta, jaune, noir).

---

## 🎨 Principe général

L'image source est traitée en **7 étapes successives**, chacune produisant un sous-dossier numéroté dans le dossier de destination :

```
image source
    │
    ▼
1-cmyk      → séparation des canaux CMJN + négatif + normalisation locale
    │
    ▼
2-cut       → découpage de chaque canal en N niveaux d'intensité
    │
    ▼
3-engrave   → application du filtre "gravure" (hachures simulées)
    │
    ▼
4-deform    → déformation légère + tramage (dithering noir/blanc)
    │
    ▼
5-vector    → vectorisation par autotrace avec extraction des lignes médianes
    │
    ▼
6-resize    → mise à l'échelle, nettoyage, regroupement par couleur,
              optimisation du parcours machine (k-d tree),
              linéarisation des courbes de Bézier
    │
    ▼
7-gcode     → génération du G-code final (un fichier par couleur)
    │
    ▼
8-preview   → prévisualisation PNG du résultat (une image par couleur
              + une composition CMJN finale)
```

À la sortie : **4 fichiers G-code** (`black.gcode`, `cyan.gcode`, `magenta.gcode`, `yellow.gcode`), un par stylo, ainsi qu'un **aperçu visuel** du résultat avant tracé.

---

## 📦 Dépendances

### Système

UV -> https://docs.astral.sh/uv/getting-started/installation/

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Python

```bash
uv sync
```

| Paquet | Rôle |
|---|---|
| `numpy` | calcul matriciel, support k-d tree |
| `pillow` | lecture des images (PIL) |
| `scipy` | k-d tree (`scipy.spatial.cKDTree`) pour l'optimisation du parcours |
| `guizero` | interface graphique simple |
| `tk` | interface graphique |
| `svgpathtools` | manipulation de fichiers SVG (lecture, écriture, longueurs, segments) |
| `gmic-py` | binding Python de G'MIC pour les filtres image |
| `autotrace` | binding Python d'AutoTrace pour la vectorisation |
| `gscrib` | construction de fichiers G-code |
| `matplotlib` | calcul |

---

## 🚀 Utilisation

### Utilisation en ligne de commande

```bash
uv run cli.py --help
```
### Lancement de l'interface graphique

```bash
uv run main.py
```

L'interface graphique s'ouvre avec **un panneau par étape** du pipeline.

### Procédure recommandée

1. **Fichiers (E/S)**
   - Cliquer sur *choisir le fichier* pour sélectionner l'image source.
   - Cliquer sur *choisir le dossier* pour sélectionner le dossier de destination (où seront créés les sous-dossiers `1-cmyk` à `7-gcode`).

2. **Régler les paramètres** de chaque étape (ou laisser les valeurs par défaut).
   - Le bouton *paramètres … par défaut* d'une étape réinitialise uniquement les curseurs concernés.
   - Le bouton *tous les paramètres par défaut* réinitialise tout.

3. **Choisir la taille finale du dessin** dans le panneau *redimensionner* :
   - Soit en saisissant *largeur* (mm),
   - Soit en saisissant *hauteur* (mm),
   - Soit en saisissant directement le *facteur d'échelle*.
   - Les trois champs se synchronisent automatiquement.

4. **Exécuter le pipeline** :
   - *exécute tout* lance l'ensemble des 7 étapes d'un coup.
   - Chaque panneau a aussi son propre bouton *exécute* pour ne relancer qu'une étape (utile pour itérer sur un paramètre).

5. **Vérifier la prévisualisation** dans `8-preview/compose.png` avant d'envoyer le G-code à la machine.

---

## ⚙️ Description des étapes et paramètres

### 1. Normalisation CMJN
Sépare l'image en 4 canaux et applique le filtre G'MIC `fx_normalize_local`.

| Paramètre | Description | Défaut |
|---|---|---|
| amplitude | force de la normalisation locale | 4.32 |
| rayon | rayon en pixels de la normalisation | 11 |
| lissage moyen | lissage de la composante moyenne | 8.68 |

### 2. Découpage par intensité
Crée plusieurs versions décalées de chaque canal, simulant des couches d'intensité.

| Paramètre | Description | Défaut |
|---|---|---|
| nombre d'images par couleur | nombre de couches générées par canal | 5 |

### 3. Gravure
Filtre G'MIC `fx_engrave` qui transforme les zones sombres en hachures.

| Paramètre | Description | Défaut |
|---|---|---|
| épaisseur des traits | rayon du burin simulé | 0.48 |

### 4. Déformation + dithering
Applique une légère déformation puis un tramage noir/blanc pour binariser. Pas de paramètre exposé.

### 5. Vectorisation
AutoTrace en mode `centerline` : extrait les **lignes médianes** des traits binarisés (idéal pour un tracé au stylo plutôt qu'un remplissage). Pas de paramètre exposé.

### 6. Redimensionnement, nettoyage, optimisation

| Paramètre | Description | Défaut |
|---|---|---|
| largeur / hauteur (mm) | taille finale du dessin physique | — |
| facteur d'échelle | équivalent en multiplicateur | — |
| longueur min des traits (mm) | les segments plus courts sont supprimés | 5 |
| longueur des segments d'approximation (mm) | finesse de la linéarisation des courbes de Bézier | 0.5 |

C'est l'étape la plus calculatoire. Elle **regroupe** tous les SVG d'une même couleur, **nettoie** les segments parasites trop courts, **optimise l'ordre de tracé** par recherche du plus proche voisin (avec un **k-d tree** pour rester rapide même sur >100 000 segments), et **linéarise** les courbes de Bézier en suite de petits segments droits.

Trois fichiers annexes sont générés à côté de chaque SVG :
- `.sens` : sens de parcours de chaque segment (start→end ou end→start)
- `.meme` : `True` si le segment précédent finit exactement où celui-ci commence
- `.meme2` : version étendue après linéarisation des courbes

Ces fichiers sont utilisés par l'étape 7 pour décider quand lever l'outil.

### 7. Génération du G-code

| Paramètre | Description | Défaut |
|---|---|---|
| hauteur de déplacement à vide (mm) | Z lorsque l'outil se déplace sans tracer | 3 |
| hauteur d'écriture (mm) | Z lorsque l'outil trace | -2 |

Le G-code utilise des coordonnées **absolues** en **millimètres**. À la fin du tracé, l'outil remonte à Z=5 et retourne à l'origine (0, 0).

### 8. Prévisualisation

Génère une **image PNG** par couleur ainsi qu'une **composition CMJN finale** simulant le rendu de la machine. Ne nécessite aucun paramètre obligatoire mais accepte plusieurs options de qualité.

| Paramètre | Description | Défaut |
|---|---|---|
| `dpi` | résolution de l'image de sortie | 150 |
| `marge_mm` | marge blanche autour du dessin (mm) | 10 |
| `epaisseur_trait` | épaisseur des traits dans le rendu (pixels) | 1.0 |
| `fond` | couleur de fond (`"white"`, `"black"`, hex…) | "white" |
| `afficher_deplacements` | dessine les déplacements à vide en pointillés gris | False |

Cette étape est implémentée par **lecture directe du G-code** : elle parse les commandes `G0`/`G1` et reconstitue la trajectoire en distinguant les mouvements en écriture (Z ≤ 0) des déplacements à vide (Z > 0).

L'option `afficher_deplacements` est particulièrement utile pour **vérifier visuellement la qualité de l'optimisation** du parcours machine (étape 6) : moins il y a de pointillés, plus l'optimisation est efficace.

#### Convention de coordonnées

La prévisualisation respecte la convention SVG (origine en haut-gauche, Y vers le bas), qui est **la même** que celle utilisée par le pipeline tout au long du traitement. Si votre machine physique attend la convention CNC classique (Y vers le haut), le G-code généré produira un dessin **retourné verticalement** ; dans ce cas, il faut adapter la fonction `generer_gcode()` pour inverser Y, et non pas la fonction de prévisualisation.

---

## 🔬 Détails techniques

### Optimisation du parcours machine (k-d tree)

L'algorithme naïf du plus proche voisin est en O(n²) → impraticable pour de grandes images.

Le code utilise `scipy.spatial.cKDTree` avec :
- **indexation des 2n extrémités** (start + end) de chaque segment ;
- **table de correspondance** pour retrouver à quel segment et à quelle extrémité correspond chaque point ;
- **suppression paresseuse** d'un segment consommé (tableau `disponible[]`) ;
- **reconstruction périodique** du k-d tree quand 50 % des segments ont été consommés (pour ne pas accumuler de "fantômes" dans l'arbre).

Complexité finale : **O(n log n)**.

### Performance du pipeline

Sur une image 1024×768 et 8 cœurs, le pipeline complet tourne en **~1 min 30 s** au lieu de **~24 min** avant optimisation (× 14.7). Deux changements en sont responsables :

**1. Désactivation du type-checking runtime de gscrib.** `gscrib` décore ses méthodes avec `@typeguard.typechecked`, qui ajoute ~1 ms de validation par appel. Sur l'étape 7 (génération G-code) qui appelle `g.move()` plus d'un million de fois, ce coût représentait à lui seul **>80 %** du temps total. La librairie `typeguard` se comporte en no-op dès qu'on remplace son décorateur. Le patch est appliqué une fois dans `img_process.py` avant l'import de `gscrib` :

```python
import typeguard
typeguard.typechecked = lambda target=None, **kw: target if target is not None else (lambda f: f)
from gscrib import GCodeBuilder
```

**2. Parallélisation par `ProcessPoolExecutor`.** Les étapes 2 à 8 sont parallélisables :

| Étape | Granularité parallèle | Workers |
|---|---|---|
| 2, 3, 4 (G'MIC) | 1 tâche par fichier | jusqu'à `cpu_count()` |
| 5 (AutoTrace) | 1 tâche par fichier | jusqu'à `cpu_count()` |
| 6 (resize + k-d tree) | 1 tâche par couleur | 4 max |
| 7 (gcode) | 1 tâche par couleur | 4 max |
| 8 (preview) | rendu parallèle par couleur, compose séquentiel | 4 max |

Pour les étapes G'MIC, un `initializer=` crée **une seule instance G'MIC + recharge le stdlib** par worker process, qui est ensuite réutilisée pour toutes ses tâches.

### Reproductibilité

Le pipeline n'est **pas** bit-déterministe d'un run à l'autre : la commande G'MIC `deform` (étape 4) utilise un RNG sans seed explicite, et chaque worker démarre avec un seed différent. Les sorties restent fonctionnellement équivalentes (~1 % d'écart dans le nombre de segments par couleur) et le rendu visuel est inchangé. Si un déterminisme bit-à-bit est requis, prefixer la commande de l'étape 4 par `srand <seed_dérivé_du_nom_de_fichier>`.

### Format des fichiers de sortie

Pour chaque couleur :
```
6-resize/
    black.svg       ← SVG final (segments uniquement, dans l'ordre optimisé)
    black.sens      ← liste de booléens (sens de parcours par segment)
    black.meme      ← liste de booléens (continuité avec segment précédent)
    black.meme2     ← idem, après linéarisation des Bézier
    cyan.svg
    …

7-gcode/
    black.gcode
    cyan.gcode
    magenta.gcode
    yellow.gcode

8-preview/
    black.png       ← rendu du noir seul
    cyan.png        ← rendu du cyan seul
    magenta.png     ← rendu du magenta seul
    yellow.png      ← rendu du jaune seul
    compose.png     ← composition CMJN finale (aperçu réaliste)
```

---

## 🧰 Architecture du code

```
.
├── img_process.py    ← logique du pipeline (7 fonctions)
├── main.py           ← interface graphique (guizero)
├── cli.py            ← ligne de commande
└── README.md         ← ce fichier
```

---

## 🖨️ Conseils d'impression

- **Calibrer le Z** soigneusement : la hauteur d'écriture varie selon le stylo. Un test avec un cercle simple est recommandé.
- **Ordre des couleurs** : tracer dans l'ordre **jaune → magenta → cyan → noir** donne en général le meilleur résultat (les pigments les plus opaques en dernier).
- **Adapter `longueur min des traits`** : trop bas → plus long, beaucoup de travail machine; trop haut → perte de détails.
- **Utiliser la prévisualisation** : avant d'envoyer le G-code à la machine, ouvrez `8-preview/compose.png` pour vérifier le rendu et `8-preview/<couleur>.png` pour repérer d'éventuels artefacts couche par couche.
- **Vérifier l'efficacité de l'optimisation** : lancez la prévisualisation avec `--afficher-deplacements` pour visualiser les trajets à vide ; un dessin bien optimisé doit présenter peu de longues lignes pointillées.

---

## 📝 Licence

GNU GENERAL PUBLIC LICENSE Version 3, 29 June 2007

---

## 🤝 Crédits

- **G'MIC** : [Traitement d'images](https://gmic-py.readthedocs.io/en/latest/) - <https://gmic.eu> - <https://doi.org/10.21105/joss.06618>
- **AutoTrace** : [AutoTrace pour Python](https://github.com/lemonyte/pyautotrace)
- **svgpathtools** : [Outils pour manipuler les objets SVG, courbes de Bézier...](https://github.com/mathandy/svgpathtools)
- **gscrib** : [génération G-code avec Python](https://gscrib.readthedocs.io/en/latest/)
- **guizero** : [Interface graphiques faciles et rapides](https://lawsie.github.io/guizero/)

---

*Pipeline développé pour la reproduction artistique d'images photographiques au traceur en quadrichromie.*