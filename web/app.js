// Interface web de img2svg2gcode : JavaScript sans dépendance ni étape de build.
// Le serveur (web.py) expose l'API JSON sous /api ; cette page n'en est qu'un client.

const $ = (selecteur, racine = document) => racine.querySelector(selecteur);

const LIBELLES_ETAT = {
  en_attente: "en attente", en_cours: "en cours", termine: "terminé",
  echec: "échec", annule: "annulé", interrompu: "interrompu",
};
const ETATS_ACTIFS = new Set(["en_attente", "en_cours"]);
const NOMS_COULEURS = { cyan: "cyan", magenta: "magenta", yellow: "jaune", black: "noir" };
const CANAUX_1_CMYK = ["cyan", "magenta", "yellow", "black"]; // image_000000 … 000003
const CLE_COTE_MAX = "img2svg2gcode.cote_max";   // choix de réduction, mémorisé par navigateur
const COTE_MAX_DEFAUT = "2000";

let config = null;          // réponse de /api/parametres (dont la version de la page chargée)
let travail = null;         // travail ouvert (réponse de /api/travaux/<id>)
let flux = null;            // EventSource du journal
let valeurs = {};           // paramètres affichés dans le formulaire
let controles = {};         // clé -> fonction qui affiche une valeur dans le formulaire

// ---------------------------------------------------------------------------
// Utilitaires
// ---------------------------------------------------------------------------
async function api(chemin, options = {}) {
  const reponse = await fetch(chemin, options);
  if (!reponse.ok) {
    let message = `erreur ${reponse.status}`;
    try { message = (await reponse.json()).erreur || message; } catch { /* pas de JSON */ }
    throw new Error(message);
  }
  return reponse.status === 204 ? null : reponse.json();
}

let minuterieNotification = null;
function notifier(message, erreur = false) {
  const boite = $("#notification");
  boite.textContent = message;
  boite.classList.toggle("erreur", erreur);
  boite.hidden = false;
  clearTimeout(minuterieNotification);
  minuterieNotification = setTimeout(() => { boite.hidden = true; }, erreur ? 6000 : 3000);
}

// el("div", {class: "x", onclick: f}, enfant1, "texte", …)
function el(balise, attributs = {}, ...enfants) {
  const noeud = document.createElement(balise);
  for (const [cle, valeur] of Object.entries(attributs)) {
    if (valeur === undefined || valeur === null || valeur === false) continue;
    if (cle.startsWith("on")) noeud.addEventListener(cle.slice(2), valeur);
    else if (cle === "class") noeud.className = valeur;
    else if (valeur === true) noeud.setAttribute(cle, "");
    else noeud.setAttribute(cle, valeur);
  }
  noeud.append(...enfants.flat().filter((e) => e !== null && e !== undefined && e !== false));
  return noeud;
}

function formaterDate(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
}

function arrondir(x, decimales) {
  const f = 10 ** decimales;
  return Math.round(x * f) / f;
}

function estActif(execution) {
  return Boolean(execution && ETATS_ACTIFS.has(execution.etat));
}

// Les fichiers gardent le même nom d'une exécution à l'autre : on ajoute la
// date de fin de la dernière exécution pour que le navigateur les recharge.
function version() {
  return encodeURIComponent(travail.execution?.fin || travail.execution?.debut || travail.cree);
}
function urlFichier(dossier, nom, telecharger = false) {
  return `/api/travaux/${travail.id}/fichiers/${dossier}/${nom}?v=${version()}${telecharger ? "&telecharger" : ""}`;
}
function urlMiniature(dossier, nom, px) {
  return `/api/travaux/${travail.id}/miniatures/${dossier}/${nom}?taille=${px}&v=${version()}`;
}

// ---------------------------------------------------------------------------
// Mise à jour de l'interface : une page restée ouverte pendant une mise à
// jour du serveur ne doit pas continuer avec l'ancien code (par exemple
// envoyer une image sans l'option de réduction qu'elle ne connaît pas).
// ---------------------------------------------------------------------------
async function pageObsolete() {
  try {
    const { version } = await api("/api/version");
    return version !== config.version;
  } catch {
    return false;  // serveur injoignable : on ne recharge pas à l'aveugle
  }
}

async function verifierVersion() {
  if (!(await pageObsolete())) return;
  if (document.querySelector("#vue-travail:not([hidden]) .modifie")) {
    // des réglages non lancés seraient perdus : on prévient au lieu de recharger
    notifier("Nouvelle version de l'interface disponible : recharge la page quand tu auras lancé tes réglages.");
  } else {
    location.reload();
  }
}

// ---------------------------------------------------------------------------
// Navigation : #/ (accueil) ou #/travail/<id>
// ---------------------------------------------------------------------------
function router() {
  const correspondance = location.hash.match(/^#\/travail\/([\w-]+)$/);
  if (correspondance) ouvrirTravail(correspondance[1]);
  else afficherAccueil();
}

// ---------------------------------------------------------------------------
// Accueil : envoi d'image et liste des travaux
// ---------------------------------------------------------------------------
async function afficherAccueil() {
  fermerFlux();
  travail = null;
  document.title = "img2svg2gcode";
  $("#vue-travail").hidden = true;
  $("#vue-accueil").hidden = false;
  let liste = [];
  try { liste = await api("/api/travaux"); } catch (e) { notifier(e.message, true); }
  $("#aucun-travail").hidden = liste.length > 0;
  $("#liste-travaux").replaceChildren(...liste.map(carteTravail));
}

function carteTravail(resume) {
  const execution = resume.execution;
  const image = resume.apercu
    ? `/api/travaux/${resume.id}/miniatures/8-preview/compose.png?taille=400&v=${encodeURIComponent(execution?.fin || "")}`
    : `/api/travaux/${resume.id}/miniatures/entree/entree.png?taille=400`;
  const supprimer = async (evenement) => {
    evenement.preventDefault();
    if (!confirm(`Supprimer le travail « ${resume.nom} » et tous ses fichiers ?`)) return;
    try {
      await api(`/api/travaux/${resume.id}`, { method: "DELETE" });
      afficherAccueil();
    } catch (e) { notifier(e.message, true); }
  };
  return el("li", { class: "carte" },
    el("a", { href: `#/travail/${resume.id}` },
      el("img", { src: image, alt: "", loading: "lazy" }),
      el("div", { class: "infos" },
        el("span", { class: "nom", title: resume.nom }, resume.nom),
        el("div", { class: "ligne" },
          el("span", { class: "discret" }, `${resume.largeur_px}×${resume.hauteur_px} px · ${formaterDate(resume.cree)}`),
          el("span", { class: `badge ${execution?.etat || ""}` }, execution ? LIBELLES_ETAT[execution.etat] : "nouveau")),
        el("div", { class: "ligne" },
          el("span"),
          el("button", { class: "petit danger", onclick: supprimer, disabled: estActif(execution) }, "Supprimer")))));
}

async function envoyer(fichier) {
  if (!fichier) return;
  if (await pageObsolete()) {
    notifier("L'interface vient d'être mise à jour : la page se recharge, renvoie ensuite ton image.");
    setTimeout(() => location.reload(), 2500);
    return;
  }
  if (fichier.size > config.limites.taille_max_envoi) {
    notifier(`Fichier trop gros (maximum ${Math.round(config.limites.taille_max_envoi / 2 ** 20)} Mo)`, true);
    return;
  }
  const barre = $("#progression-envoi");
  barre.hidden = false;
  barre.value = 0;
  const requete = new XMLHttpRequest();
  requete.open("POST", `/api/travaux?cote_max=${encodeURIComponent($("#cote-max").value)}`);
  requete.setRequestHeader("X-Nom-Fichier", encodeURIComponent(fichier.name));
  requete.upload.onprogress = (e) => { if (e.lengthComputable) barre.value = e.loaded / e.total; };
  requete.onload = () => {
    barre.hidden = true;
    let reponse = {};
    try { reponse = JSON.parse(requete.responseText); } catch { /* réponse non JSON */ }
    if (requete.status === 201) location.hash = `#/travail/${reponse.id}`;
    else notifier(reponse.erreur || `envoi refusé (erreur ${requete.status})`, true);
  };
  requete.onerror = () => { barre.hidden = true; notifier("envoi impossible (réseau)", true); };
  requete.send(fichier);
}

function brancherEnvoi() {
  const zone = $("#zone-envoi");
  const champ = $("#champ-fichier");
  const reduction = $("#cote-max");
  let choix = null;
  try { choix = localStorage.getItem(CLE_COTE_MAX); } catch { /* stockage indisponible */ }
  reduction.value = choix ?? COTE_MAX_DEFAUT;
  if (!reduction.value) reduction.value = COTE_MAX_DEFAUT;  // valeur mémorisée inconnue
  reduction.addEventListener("change", () => {
    try { localStorage.setItem(CLE_COTE_MAX, reduction.value); } catch { /* tant pis */ }
  });
  champ.addEventListener("change", () => { envoyer(champ.files[0]); champ.value = ""; });
  zone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); champ.click(); } });
  // glisser-déposer n'importe où sur l'accueil
  document.addEventListener("dragover", (e) => {
    if ($("#vue-accueil").hidden) return;
    e.preventDefault();
    zone.classList.add("survol");
  });
  document.addEventListener("dragleave", (e) => { if (!e.relatedTarget) zone.classList.remove("survol"); });
  document.addEventListener("drop", (e) => {
    if ($("#vue-accueil").hidden) return;
    e.preventDefault();
    zone.classList.remove("survol");
    envoyer(e.dataTransfer.files[0]);
  });
}

// ---------------------------------------------------------------------------
// Vue d'un travail
// ---------------------------------------------------------------------------
async function ouvrirTravail(id) {
  $("#vue-accueil").hidden = true;
  $("#vue-travail").hidden = false;
  try {
    travail = await api(`/api/travaux/${id}`);
  } catch (e) {
    notifier(e.message, true);
    location.hash = "#/";
    return;
  }
  document.title = `${travail.nom} · img2svg2gcode`;
  $("#nom-travail").textContent = travail.nom;
  $("#nom-travail").title = travail.nom;
  const [largeurOrigine, hauteurOrigine] = travail.taille_origine || [travail.largeur_px, travail.hauteur_px];
  $("#taille-travail").textContent = `${travail.largeur_px} × ${travail.hauteur_px} px` +
    (largeurOrigine !== travail.largeur_px ? ` (réduite, originale ${largeurOrigine} × ${hauteurOrigine})` : "");
  $("#vignette-entree").src = `/api/travaux/${id}/miniatures/entree/entree.png?taille=200`;
  $("#lien-journal").href = `/api/travaux/${id}/journal.txt`;
  valeurs = { ...travail.parametres };
  construireEtapes();
  afficherExecution();
  afficherResultats();
  ouvrirFlux();
}

async function rafraichirTravail() {
  if (!travail) return;
  const id = travail.id;
  try {
    const infos = await api(`/api/travaux/${id}`);
    if (travail?.id !== id) return;  // on a changé de travail entre-temps
    travail = { ...infos, parametres: travail.parametres };
  } catch { return; }
  afficherExecution();
  afficherResultats();
}

// --- formulaire des paramètres, une carte par étape ------------------------
function construireEtapes() {
  controles = {};
  const cartes = config.etapes.map((etape, i) => {
    const parametres = config.parametres.filter((p) => p.etape === etape.commande);
    const corps = el("div", { class: "etape-corps" });
    if (etape.commande === "redimensionner") corps.append(champsTaille());
    for (const p of parametres) {
      if (p.cle !== "redimensionner_facteur_echelle") corps.append(controleParametre(p));
    }
    const reglables = parametres.filter((p) => p.cle !== "redimensionner_facteur_echelle");
    return el("section", { class: "carte etape" },
      el("div", { class: "etape-entete" },
        el("span", { class: "etape-numero" }, String(i + 1)),
        el("h3", {}, etape.libelle),
        reglables.length > 0 && el("button", {
          class: "petit secondaire", title: "valeurs par défaut de cette étape",
          onclick: () => reglables.forEach((p) => controles[p.cle](p.defaut)),
        }, "défaut"),
        el("button", {
          class: "petit bouton-executer", title: "exécuter seulement cette étape",
          onclick: () => executer(etape.commande),
        }, "▶")),
      corps);
  });
  $("#etapes").replaceChildren(...cartes);
}

function controleParametre(p) {
  const identifiant = `param-${p.cle}`;
  if (p.type === "booleen") {
    const case_ = el("input", { type: "checkbox", id: identifiant });
    const afficher = (valeur) => {
      case_.checked = valeur;
      valeurs[p.cle] = valeur;
      case_.parentElement?.classList.toggle("modifie", valeur !== travail.parametres[p.cle]);
    };
    case_.addEventListener("change", () => afficher(case_.checked));
    controles[p.cle] = afficher;
    afficher(valeurs[p.cle]);
    return el("div", { class: "parametre booleen" }, el("label", {}, case_, p.libelle));
  }
  const bornes = { min: p.min, max: p.max, step: p.pas };
  const curseur = el("input", { type: "range", ...bornes, "aria-label": p.libelle });
  const nombre = el("input", { type: "number", id: identifiant, ...bornes });
  const afficher = (valeur) => {
    curseur.value = valeur;
    nombre.value = valeur;
    valeurs[p.cle] = valeur;
    nombre.classList.toggle("modifie", valeur !== travail.parametres[p.cle]);
  };
  const lire = (champ) => {
    const valeur = p.type === "entier" ? parseInt(champ.value, 10) : parseFloat(champ.value);
    if (!Number.isNaN(valeur)) afficher(valeur);
  };
  curseur.addEventListener("input", () => lire(curseur));
  nombre.addEventListener("change", () => lire(nombre));
  controles[p.cle] = afficher;
  afficher(valeurs[p.cle]);
  return el("div", { class: "parametre" },
    el("label", { for: identifiant }, p.libelle),
    el("div", { class: "controle" }, curseur, nombre));
}

// Largeur, hauteur et facteur d'échelle se mettent à jour mutuellement,
// comme dans la GUI : facteur = mm par pixel de l'image d'entrée.
function champsTaille() {
  const largeur = el("input", { type: "number", min: 1, step: "any" });
  const hauteur = el("input", { type: "number", min: 1, step: "any" });
  const facteur = el("input", { type: "number", min: 0.001, max: 100, step: "any" });
  const cle = "redimensionner_facteur_echelle";
  const afficher = (f, source = null) => {
    valeurs[cle] = f;
    if (source !== largeur) largeur.value = arrondir(f * travail.largeur_px, 1);
    if (source !== hauteur) hauteur.value = arrondir(f * travail.hauteur_px, 1);
    if (source !== facteur) facteur.value = arrondir(f, 5);
    facteur.classList.toggle("modifie", f !== travail.parametres[cle]);
  };
  largeur.addEventListener("input", () => { const v = parseFloat(largeur.value); if (v > 0) afficher(v / travail.largeur_px, largeur); });
  hauteur.addEventListener("input", () => { const v = parseFloat(hauteur.value); if (v > 0) afficher(v / travail.hauteur_px, hauteur); });
  facteur.addEventListener("input", () => { const v = parseFloat(facteur.value); if (v > 0) afficher(v, facteur); });
  controles[cle] = afficher;
  afficher(valeurs[cle]);
  return el("div", { class: "parametre" },
    el("label", {}, "taille finale du dessin"),
    el("div", { class: "taille-dessin" },
      el("label", {}, "largeur (mm)", largeur),
      el("label", {}, "hauteur (mm)", hauteur),
      el("label", {}, "facteur (mm/px)", facteur)));
}

// --- exécution ----------------------------------------------------------------
async function executer(commande) {
  const id = travail.id;
  try {
    const infos = await api(`/api/travaux/${id}/executer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ commande, parametres: valeurs }),
    });
    travail = infos;
  } catch (e) {
    notifier(e.message, true);
    return;
  }
  valeurs = { ...travail.parametres };
  Object.entries(valeurs).forEach(([cle, valeur]) => controles[cle]?.(valeur));  // efface les marques "modifié"
  $("#journal").textContent = "";
  afficherExecution();
  afficherResultats();
  ouvrirFlux();
}

async function annuler() {
  try {
    await api(`/api/travaux/${travail.id}/annuler`, { method: "POST" });
    notifier("annulation demandée");
  } catch (e) { notifier(e.message, true); }
}

function libelleCommande(commande) {
  if (commande === "tout") return "pipeline complet";
  const i = config.etapes.findIndex((e) => e.commande === commande);
  return i >= 0 ? `étape ${i + 1} : ${config.etapes[i].libelle}` : commande;
}

function afficherExecution() {
  const execution = travail.execution;
  const actif = estActif(execution);
  const badge = $("#badge-etat");
  if (!execution) {
    badge.className = "badge";
    badge.textContent = "jamais exécuté";
  } else {
    badge.className = `badge ${execution.etat}`;
    let texte = `${LIBELLES_ETAT[execution.etat]} · ${libelleCommande(execution.commande)}`;
    if (execution.etat === "en_attente" && travail.position) texte += ` · n° ${travail.position} dans la file`;
    badge.textContent = texte;
    badge.title = [execution.debut && `début ${formaterDate(execution.debut)}`,
                   execution.fin && `fin ${formaterDate(execution.fin)}`].filter(Boolean).join(" · ");
  }
  $("#btn-annuler").hidden = !actif;
  document.querySelectorAll("#btn-tout, .bouton-executer").forEach((b) => { b.disabled = actif; });

  // Frise des 8 étapes : faite = sortie présente sur disque ; active = en cours.
  const numeroActif = !execution ? null
    : execution.commande === "tout" ? execution.etape
    : config.etapes.findIndex((e) => e.commande === execution.commande) + 1;
  const segments = config.etapes.map((etape, i) => {
    const numero = i + 1;
    const classes = [];
    if ((travail.fichiers[etape.dossier] || []).length > 0) classes.push("faite");
    if (actif && execution.etat === "en_cours" && numero === numeroActif) classes.push("active");
    if (actif && execution.commande !== "tout" && numero === numeroActif) classes.push("ciblee");
    return el("li", { class: classes.join(" "), title: `${numero}. ${etape.libelle}` });
  });
  $("#frise").replaceChildren(...segments);
}

function fermerFlux() {
  if (flux) { flux.close(); flux = null; }
}

// Journal en direct (SSE). Le serveur rejoue le journal depuis le début puis
// envoie les nouvelles lignes ; l'évènement "fin" clôt le flux.
function ouvrirFlux() {
  fermerFlux();
  const id = travail.id;
  const journal = $("#journal");
  journal.textContent = "";
  let etapeVue = null;
  const source = new EventSource(`/api/travaux/${id}/journal`);
  flux = source;
  source.onmessage = (evenement) => {
    if (travail?.id !== id) return;
    const message = JSON.parse(evenement.data);
    if (message.reinitialiser) journal.textContent = "";
    if (message.lignes.length) {
      const enBas = journal.scrollHeight - journal.scrollTop - journal.clientHeight < 30;
      journal.append(message.lignes.join("\n") + "\n");
      if (enBas) journal.scrollTop = journal.scrollHeight;
    }
    travail.execution = message.execution.etat ? message.execution : null;
    travail.position = message.position;
    afficherExecution();
    if (message.execution.etape !== etapeVue) {  // nouvelle étape : de nouveaux fichiers
      etapeVue = message.execution.etape;
      rafraichirTravail();
    }
  };
  source.addEventListener("fin", () => {
    source.close();
    if (flux === source) flux = null;
    rafraichirTravail();
  });
}

// --- résultats ------------------------------------------------------------------
function afficherResultats() {
  afficherApercu();
  afficherImagesEtapes();
  afficherTelechargements();
}

function afficherApercu() {
  const fichiers = travail.fichiers["8-preview"] || [];
  const onglet = $("#onglet-apercu");
  if (!fichiers.includes("compose.png")) {
    const message = estActif(travail.execution)
      ? "Calcul en cours : l'aperçu s'affichera à la fin."
      : "Pas encore d'aperçu : lance « Tout exécuter ».";
    onglet.replaceChildren(el("p", { class: "vide" }, message));
    return;
  }
  const couleurs = ["cyan", "magenta", "yellow", "black"].filter((c) => fichiers.includes(`${c}.png`));
  onglet.replaceChildren(
    el("a", { href: urlFichier("8-preview", "compose.png"), target: "_blank", title: "ouvrir en pleine résolution" },
      el("img", { class: "apercu-principal", src: urlMiniature("8-preview", "compose.png", 1600), alt: "aperçu CMJN" })),
    el("div", { class: "galerie" }, couleurs.map((c) =>
      el("figure", {},
        el("a", { href: urlFichier("8-preview", `${c}.png`), target: "_blank" },
          el("img", { src: urlMiniature("8-preview", `${c}.png`, 400), alt: NOMS_COULEURS[c], loading: "lazy" })),
        el("figcaption", {}, el("span", { class: `pastille ${c}` }), NOMS_COULEURS[c])))));
}

// "image_cyan_40.png" -> {couleur: "cyan", texte: "cyan · +40 %"}
function legende(dossier, nom) {
  const canal = nom.match(/^image_00000(\d)\./);
  if (canal) {
    const couleur = CANAUX_1_CMYK[Number(canal[1])];
    return { couleur, texte: NOMS_COULEURS[couleur] };
  }
  const couche = nom.match(/^image_(cyan|magenta|yellow|black)_(\d+)\./);
  if (couche) return { couleur: couche[1], texte: `${NOMS_COULEURS[couche[1]]} · +${couche[2]} %` };
  return { couleur: null, texte: nom };
}

// Les SVG d'AutoTrace n'ont pas de viewBox : affichés dans un <img> réduit,
// ils seraient rognés au lieu d'être mis à l'échelle, avec un trait d'un
// pixel devenu invisible. On les redessine donc dans la page : viewBox
// ajoutée, trait d'épaisseur constante, seuls les attributs "d" sont repris.
const observateurSvg = new IntersectionObserver((entrees) => {
  for (const entree of entrees) {
    if (!entree.isIntersecting) continue;
    observateurSvg.unobserve(entree.target);
    dessinerSvg(entree.target);
  }
}, { rootMargin: "300px" });

async function dessinerSvg(conteneur) {
  try {
    const texte = await (await fetch(conteneur.dataset.src)).text();
    const source = new DOMParser().parseFromString(texte, "image/svg+xml").documentElement;
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    const largeur = parseFloat(source.getAttribute("width")) || 100;
    const hauteur = parseFloat(source.getAttribute("height")) || 100;
    svg.setAttribute("viewBox", source.getAttribute("viewBox") || `0 0 ${largeur} ${hauteur}`);
    for (const chemin of source.querySelectorAll("path")) {
      const trace = document.createElementNS(ns, "path");
      trace.setAttribute("d", chemin.getAttribute("d") || "");
      svg.append(trace);
    }
    conteneur.replaceChildren(svg);
  } catch {
    conteneur.textContent = "illisible";
  }
}

function afficherImagesEtapes() {
  const svgADessiner = [];
  const groupes = config.etapes.slice(0, 5).map((etape, i) => {
    const fichiers = (travail.fichiers[etape.dossier] || []).filter((n) => /\.(png|svg)$/.test(n));
    if (!fichiers.length) return null;
    return el("div", { class: "groupe-etape" },
      el("h3", {}, `${i + 1}. ${etape.libelle}`),
      el("div", { class: "galerie" }, fichiers.map((nom) => {
        const { couleur, texte } = legende(etape.dossier, nom);
        let apercu;
        if (nom.endsWith(".png")) {
          apercu = el("img", { src: urlMiniature(etape.dossier, nom, 300), alt: nom, loading: "lazy" });
        } else {
          apercu = el("div", { class: "svg-miniature", "data-src": urlFichier(etape.dossier, nom), role: "img", "aria-label": nom });
          svgADessiner.push(apercu);
        }
        return el("figure", {},
          el("a", { href: urlFichier(etape.dossier, nom), target: "_blank" }, apercu),
          el("figcaption", {}, couleur && el("span", { class: `pastille ${couleur}` }), texte));
      })));
  }).filter(Boolean);
  $("#onglet-etapes-images").replaceChildren(
    ...(groupes.length ? groupes : [el("p", { class: "vide" }, "Aucune étape exécutée pour l'instant.")]));
  svgADessiner.forEach((conteneur) => observateurSvg.observe(conteneur));
}

function afficherTelechargements() {
  const gcodes = travail.fichiers["7-gcode"] || [];
  const svgs = (travail.fichiers["6-resize"] || []).filter((n) => n.endsWith(".svg"));
  const onglet = $("#onglet-telechargements");
  if (!gcodes.length && !svgs.length) {
    onglet.replaceChildren(el("p", { class: "vide" }, "Rien à télécharger : le G-code est produit à l'étape 7."));
    return;
  }
  const lien = (dossier, nom, description) => el("li", {},
    el("a", { href: urlFichier(dossier, nom, true) }, nom), el("span", { class: "discret" }, description));
  onglet.replaceChildren(
    gcodes.length > 0 && el("a", { class: "bouton-lien", href: `/api/travaux/${travail.id}/gcode.zip` },
      "⬇ Tout le G-code (zip)"),
    el("ul", { class: "telechargements" },
      gcodes.map((n) => lien("7-gcode", n, `stylo ${NOMS_COULEURS[n.replace(".gcode", "")] || ""}`)),
      svgs.map((n) => lien("6-resize", n, "tracé optimisé (SVG, volumineux)"))));
}

function brancherOnglets() {
  document.querySelectorAll(".onglets button").forEach((bouton) => {
    bouton.addEventListener("click", () => {
      document.querySelectorAll(".onglets button").forEach((b) => b.setAttribute("aria-selected", String(b === bouton)));
      document.querySelectorAll(".onglet").forEach((o) => { o.hidden = o.id !== `onglet-${bouton.dataset.onglet}`; });
    });
  });
}

// ---------------------------------------------------------------------------
// Démarrage
// ---------------------------------------------------------------------------
async function demarrer() {
  try {
    config = await api("/api/parametres");
  } catch (e) {
    notifier(`serveur injoignable : ${e.message}`, true);
    return;
  }
  brancherEnvoi();
  brancherOnglets();
  $("#btn-tout").addEventListener("click", () => executer("tout"));
  $("#btn-annuler").addEventListener("click", annuler);
  $("#btn-defaut-tout").addEventListener("click", () => {
    for (const p of config.parametres) {
      if (p.cle !== "redimensionner_facteur_echelle") controles[p.cle]?.(p.defaut);
    }
  });
  window.addEventListener("hashchange", () => { verifierVersion(); router(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) verifierVersion(); });
  setInterval(verifierVersion, 5 * 60 * 1000);
  router();
}

demarrer();
