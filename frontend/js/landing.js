// Page d'accueil : traduction FR / EN, thème, et démo animée
// (une annonce s'écrit, l'unité se déplace, des hostiles apparaissent).

import { currentLang, onLangChange, setupPrefs } from "./prefs.js";

const DICT = {
  "meta.title": { fr: "Voice-to-Map", en: "Voice-to-Map" },
  "meta.description": {
    fr: "Les unités annoncent leur position à la voix, la carte partagée se met à jour en quelques secondes.",
    en: "Units report their position by voice; the shared map updates within seconds.",
  },
  "nav.home": { fr: "Voice-to-Map, accueil", en: "Voice-to-Map, home" },
  "nav.label": { fr: "Sections", en: "Sections" },
  "nav.how": { fr: "Fonctionnement", en: "How it works" },
  "nav.phrases": { fr: "Ce qu'elle comprend", en: "What it understands" },
  "nav.security": { fr: "Sécurité", en: "Security" },
  "cta.open": { fr: "Ouvrir la carte", en: "Open the map" },
  "hero.title": { fr: "Dites où vous êtes.<br>La carte suit.", en: "Say where you are.<br>The map follows." },
  "hero.lede": {
    fr: "Chaque unité annonce sa position à la radio, en français ou en anglais. En quelques secondes, son symbole bouge sur la carte que toute l'équipe partage, avec l'heure, le trajet et la marge d'erreur.",
    en: "Each unit reports its position over the radio, in English or French. Within seconds its symbol moves on the map the whole team shares, with the time, the route and the margin of error.",
  },
  "hero.how": { fr: "Voir comment ça marche", en: "See how it works" },
  "demo.label": {
    fr: "Démonstration : une annonce vocale déplace une unité sur la carte",
    en: "Demo: a voice report moves a unit on the map",
  },
  "demo.church": { fr: "Église", en: "Church" },
  "demo.bridge": { fr: "Pont", en: "Bridge" },
  "demo.replay": { fr: "Rejouer la démo", en: "Replay the demo" },
  "demo.line1": { fr: "Alpha 1, je suis au sud de l'église.", en: "Alpha 1, I am south of the church." },
  "demo.line2": { fr: "Contact, trois ennemis au nord du pont.", en: "Contact, three hostiles north of the bridge." },
  "steps.title": { fr: "De la voix au point sur la carte", en: "From voice to a point on the map" },
  "steps.1.t": { fr: "Parler", en: "Speak" },
  "steps.1.p": {
    fr: "On maintient le bouton (ou la barre espace) et on fait son annonce, comme à la radio.",
    en: "Hold the button (or the space bar) and make your report, like on the radio.",
  },
  "steps.2.t": { fr: "Comprendre", en: "Understand" },
  "steps.2.p": {
    fr: "La phrase est transcrite, puis analysée : qui parle, où il est, ce qu'il voit, ce qu'il a neutralisé.",
    en: "The sentence is transcribed, then analysed: who is speaking, where they are, what they see, what they took out.",
  },
  "steps.3.t": { fr: "Placer", en: "Place" },
  "steps.3.p": {
    fr: "Le lieu est retrouvé dans OpenStreetMap, avec un rayon d'incertitude et le trajet par les rues.",
    en: "The place is found in OpenStreetMap, with an uncertainty radius and the route through the streets.",
  },
  "steps.4.t": { fr: "Partager", en: "Share" },
  "steps.4.p": {
    fr: "Tous les écrans reçoivent la mise à jour en direct. Chaque annonce reste dans un historique horodaté.",
    en: "Every screen gets the update live. Each report stays in a time-stamped history.",
  },
  "phrases.title": { fr: "Ce que la carte comprend", en: "What the map understands" },
  "phrases.lede": { fr: "Le langage de tous les jours, pas des coordonnées.", en: "Everyday language, not coordinates." },
  "phrases.say": { fr: "On dit", en: "You say" },
  "phrases.map": { fr: "La carte", en: "The map" },
  "p1.s": { fr: "« Je suis au sud de l'église. »", en: "“I am south of the church.”" },
  "p1.m": { fr: "place l'unité 120 m au sud de l'église la plus proche", en: "places the unit 120 m south of the nearest church" },
  "p2.s": { fr: "« Je suis la route et je tourne à droite au prochain bloc. »", en: "“I follow the road and turn right at the next block.”" },
  "p2.m": { fr: "avance d'un pâté de maisons dans le sens de marche, puis tourne", en: "moves one block ahead in the direction of travel, then turns" },
  "p3.s": { fr: "« Contact, cinq ennemis près de l'Hôtel-Dieu. »", en: "“Contact, five hostiles near the Hôtel-Dieu.”" },
  "p3.m": { fr: "pose cinq hostiles numérotés dans la rue la plus proche", en: "drops five numbered hostiles in the nearest street" },
  "p4.s": { fr: "« Ennemi à 6h, 100 mètres. »", en: "“Hostile at six o'clock, 100 metres.”" },
  "p4.m": { fr: "place l'hostile derrière l'unité, d'après son sens de marche", en: "places the hostile behind the unit, based on its direction of travel" },
  "p5.s": { fr: "« Moi et Tom 2, on est en groupe. »", en: "“Tom 2 and I are grouped up.”" },
  "p5.m": { fr: "forme une escouade qui se déplace ensemble", en: "forms a squad that moves together" },
  "p6.s": { fr: "« J'ai éliminé les ennemis autour de moi. »", en: "“I took out the hostiles around me.”" },
  "p6.m": { fr: "marque comme neutralisés tous les hostiles à moins de 300 m", en: "marks every hostile within 300 m as neutralised" },
  "sec.title": { fr: "Une carte à laquelle on peut se fier", en: "A map you can trust" },
  "sec.1.t": { fr: "Un terminal, une unité", en: "One terminal, one unit" },
  "sec.1.p": {
    fr: "Chaque navigateur crée sa propre clé et signe ses annonces. Personne ne peut parler à la place d'ALPHA 1 depuis un autre terminal.",
    en: "Each browser creates its own key and signs its reports. Nobody can speak for ALPHA 1 from another terminal.",
  },
  "sec.2.t": { fr: "Un historique qui ne s'efface pas", en: "A history that can't be erased" },
  "sec.2.p": {
    fr: "Chaque événement est chaîné au précédent. Une modification après coup casse la chaîne, et le bouton de vérification le montre.",
    en: "Each event is chained to the previous one. Any later edit breaks the chain, and the check button shows it.",
  },
  "sec.3.t": { fr: "L'incertitude affichée", en: "Uncertainty on display" },
  "sec.3.p": {
    fr: "Une annonce floue n'est pas placée au hasard : elle reste à confirmer, et chaque point porte sa marge d'erreur.",
    en: "A vague report is never placed at random: it waits for confirmation, and every point carries its margin of error.",
  },
  "cta.title": { fr: "Prenez une unité et faites votre première annonce.", en: "Take a unit and make your first report." },
  "foot.about": {
    fr: "Voice-to-Map, challenge Lysk du hackathon EDTH Paris, novembre 2026.",
    en: "Voice-to-Map, Lysk challenge at the EDTH Paris hackathon, November 2026.",
  },
  "foot.team": { fr: "Réalisé par Thibaut, Tom, Kenzo et Joan.", en: "Built by Thibaut, Tom, Kenzo and Joan." },
  "foot.credits": {
    fr: "Fond de carte et lieux : contributeurs OpenStreetMap. Transcription et analyse : Mistral.",
    en: "Map and places: OpenStreetMap contributors. Transcription and analysis: Mistral.",
  },
};

setupPrefs(DICT);

// --- Démo -------------------------------------------------------------------

const $ = (id) => document.getElementById(id);
const demo = document.querySelector(".demo");
const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const line = (n) => DICT[`demo.line${n}`][currentLang()];

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
let run = 0;

function placeUnit(t) {
  const track = $("track");
  const p = track.getPointAtLength(track.getTotalLength() * t);
  $("unit").setAttribute("transform", `translate(${p.x} ${p.y})`);
  $("unit-label").setAttribute("x", p.x + 22);
  $("unit-label").setAttribute("y", p.y + 5);
  track.setAttribute("stroke-opacity", t >= 1 ? 1 : 0); // le trajet apparaît une fois parcouru
}

async function type(text, id) {
  const el = $("said");
  el.textContent = "";
  for (const ch of text) {
    if (id !== run) return false;
    el.textContent += ch;
    await wait(ch === "," ? 220 : 38);
  }
  return true;
}

function moveUnit(id) {
  const t0 = performance.now();
  const dur = 1400;
  return new Promise((resolve) => {
    const step = (now) => {
      if (id !== run) return resolve(false);
      const t = Math.min(1, (now - t0) / dur);
      placeUnit(1 - Math.pow(1 - t, 3));
      if (t < 1) requestAnimationFrame(step);
      else resolve(true);
    };
    requestAnimationFrame(step);
  });
}

function finalState() {
  placeUnit(1);
  $("unc").setAttribute("r", 34);
  $("hostiles").classList.add("shown");
  $("said").textContent = line(2);
  demo.classList.add("done");
}

async function play() {
  const id = ++run;
  demo.classList.remove("done");
  $("hostiles").classList.remove("shown");
  $("unc").setAttribute("r", 0);
  placeUnit(0);
  if (reduced) return finalState();

  await wait(500);
  if (!(await type(line(1), id))) return;
  await wait(300);
  if (!(await moveUnit(id))) return;
  $("unc").setAttribute("r", 34);
  await wait(900);
  if (!(await type(line(2), id))) return;
  await wait(250);
  $("hostiles").classList.add("shown");
  demo.classList.add("done");
}

$("replay").addEventListener("click", play);
// Changement de langue : la démo affiche la phrase dans la nouvelle langue.
onLangChange(() => { if (demo.classList.contains("done")) $("said").textContent = line(2); });
play();
