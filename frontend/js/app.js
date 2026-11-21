// Assemblage : unité du terminal, formulaire, historique, intégrité. Responsable : A.
//
// Chaque terminal gère UNE unité : ses annonces (voix ou texte) sont signées avec sa clé
// et comptent pour cette unité, quel que soit l'indicatif prononcé. La carte montre
// toutes les unités, la sienne mise en avant. Le poste de commandement voit tout sans unité.

import * as identity from "./identity.js";
import { currentLang, onLangChange, setupPrefs } from "./prefs.js";
import { applyEvent, flyTo, flyToPoint, initMap, setKillHandler, setOwnUnit, setUnitColors, squadBox, unitColor, unitPosition } from "./map.js";
import { setupPushToTalk } from "./ptt.js";
import { connect } from "./socket.js";

const $ = (id) => document.getElementById(id);
const tr = (fr, en) => (currentLang() === "en" ? en : fr); // textes construits en JS

// Textes fixes de la page (attributs data-i18n de app.html).
const DICT = {
  "meta.title": { fr: "Voice-to-Map · Carte", en: "Voice-to-Map · Map" },
  "nav.back": { fr: "Voice-to-Map, retour à l'accueil", en: "Voice-to-Map, back to home" },
  "nav.home": { fr: "Accueil", en: "Home" },
  "claim.q": { fr: "Quelle unité ce terminal gère-t-il ?", en: "Which unit does this terminal run?" },
  "claim.take": { fr: "Prendre", en: "Take" },
  "claim.pc": { fr: "Poste de commandement : voir sans unité", en: "Command post: view without a unit" },
  "me.take": { fr: "Prendre une unité", en: "Take a unit" },
  "me.leave": { fr: "Quitter l'unité", en: "Leave the unit" },
  "ptt": { fr: "Maintenir pour parler", en: "Hold to talk" },
  "ptt.hint": { fr: "ou maintenir la barre espace, hors des champs de saisie", en: "or hold the space bar, outside text fields" },
  "text.label": { fr: "Annonce écrite", en: "Written report" },
  "text.placeholder": { fr: "Je suis au sud de l'église.", en: "I am south of the church." },
  "send": { fr: "Envoyer", en: "Send" },
  "samples": { fr: "Exemples", en: "Examples" },
  "s1": { fr: "Je suis à Notre-Dame.", en: "I am at Notre-Dame." },
  "s2": { fr: "Je suis au sud de l'église.", en: "I am south of the church." },
  "s3": { fr: "Je me déplace vers la rue de Rivoli.", en: "I moved towards rue de Rivoli." },
  "s4": { fr: "Je remonte la rue Saint-Denis vers le nord sur 200 mètres.", en: "Moving north along rue Saint-Denis, about 200 meters." },
  "s5": { fr: "Contact, trois ennemis au nord de l'église.", en: "Contact, three hostiles north of the church." },
  "s6": { fr: "Ennemi à 6h, 100 mètres.", en: "Hostile at six o'clock, 100 meters." },
  "s7": { fr: "Je veux aller vers Bravo 2.", en: "I am heading to Bravo 2." },
  "s8": { fr: "E1 au sud du pont.", en: "E1 is south of the bridge." },
  "s9": { fr: "E1 neutralisé.", en: "E1 neutralised." },
  "history": { fr: "Historique", en: "History" },
  "clear": { fr: "Effacer (local)", en: "Clear (local)" },
  "clear.title": {
    fr: "Vide l'historique sur cet écran seulement (la carte et les autres écrans ne changent pas)",
    en: "Clears the history on this screen only (the map and other screens are unchanged)",
  },
  "verify": { fr: "Vérifier l'intégrité", en: "Check integrity" },
  "sos": { fr: "Demander du renfort", en: "Request backup" },
  "sos.clear": { fr: "Situation sécurisée", en: "Situation secured" },
  "conn": { fr: "Connexion", en: "Connection" },
};
const allEvents = []; // pour reconstruire l'historique quand on change de langue

// Dernier état connu de la carte, gardé dans le navigateur : sans réseau, on le réaffiche.
const STATE_KEY = "vtm-last-events";
let saveTimer = null;
function saveState() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    try { localStorage.setItem(STATE_KEY, JSON.stringify(allEvents.slice(-400))); } catch { /* plein ou privé */ }
  }, 1000);
}
function cachedEvents() {
  try { return JSON.parse(localStorage.getItem(STATE_KEY) || "[]"); } catch { return []; }
}
const confirmedSeqs = new Set(); // annonces « à confirmer » qui ont été confirmées depuis
const activeAlerts = new Map(); // unité -> événement d'alerte en cours
const seen = new Set();
let me = null; // identité de cet onglet : {unit, privateKey, publicKey}
let announcePresence = () => {}; // branché sur le WebSocket dans main()
let simListener = () => {};
let groups = {};       // {"G1": ["ALPHA 1", "TOM 2"]}
let squadEl = null;    // encart en haut à gauche de la carte
const PC_KEY = "vtm-pc";
const pcMode = {
  get: () => { try { return sessionStorage.getItem(PC_KEY) === "1"; } catch { return false; } },
  set: (on) => { try { on ? sessionStorage.setItem(PC_KEY, "1") : sessionStorage.removeItem(PC_KEY); } catch {} },
};

function fmtTime(ts) {
  return new Date(ts).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function describe(ev) {
  const i = ev.intent || {};
  const rel = i.relation || "?";
  const where = ev.resolved_landmark || (i.landmark && i.landmark.name) || (i.landmarks || []).join(" × ") || "—";
  const unc = ev.uncertainty_m ? ` ±${Math.round(ev.uncertainty_m)} m` : "";
  return `${i.type || "?"} · ${rel} · ${where}${unc} · ${i.source || ""}`;
}

// Effacement local de l'historique (ce navigateur seulement) : on retient le dernier numéro effacé.
const HIDE_KEY = "vtm-history-hidden-until";
let hiddenUntil = (() => { try { return Number(localStorage.getItem(HIDE_KEY)) || 0; } catch { return 0; } })();
let lastSeq = 0;

function addToHistory(ev) {
  lastSeq = Math.max(lastSeq, ev.seq || 0);
  if (ev.intent?.sim) return; // étapes de la simulation : pas dans l'historique
  // Coéquipiers déplacés avec l'auteur (escouade, « moi et Tom ») : une seule ligne, celle de l'auteur.
  if (ev.intent?.moved_by) return;
  if ((ev.seq || 0) <= hiddenUntil) return; // effacé localement
  if (ev.status === "cleared") $("events").replaceChildren(); // remise à zéro : l'historique repart de zéro
  // Groupe d'ennemis (« 5 ennemis à… ») : une seule ligne dans l'historique, pour le premier.
  const group = ev.intent?.group_ids;
  if (group && ev.intent.enemy_id !== group[0]) return;
  const killed = ev.intent?.kill_ids; // plusieurs ennemis neutralisés par la même annonce
  if (killed && ev.intent.enemy_id !== killed[0]) return;
  const li = document.createElement("li");
  li.className = "event";
  li.style.setProperty("--unit-color", unitColor(ev.unit));
  li.innerHTML = `
    <div class="meta"><span>${fmtTime(ev.ts)}</span><span>${whoLabel(ev)}</span>
      <span class="badge ${ev.status}">${ev.status === "confirmed" ? tr("placé", "placed") : tr("à confirmer", "to confirm")}</span>
      ${ev.intent?.subject === "enemy" ? `<span class="badge enemy">${group ? `${group[0]}–${group[group.length - 1]} (${group.length})` : ev.intent.enemy_id || tr("ennemi", "hostile")}${!group && ev.intent.count ? ` ×${ev.intent.count}` : ""}</span>` : ""}</div>
    <div class="text"></div>
    <div class="res"></div>`;
  if (ev.status === "alert" || ev.status === "alert_clear") {
    const i = ev.intent || {};
    const b = li.querySelector(".meta .badge");
    b.className = ev.status === "alert" ? "badge alert" : "badge confirmed";
    b.textContent = ev.status === "alert" ? tr("renfort demandé", "backup requested") : tr("sécurisé", "secured");
    li.querySelector(".text").textContent = `« ${ev.transcript} »`;
    const who = (i.recipients || []).map((r) => `${r.unit} (${r.distance_m} m)`).join(", ");
    li.querySelector(".res").textContent = ev.status === "alert"
      ? (who ? `${tr("prévenus", "notified")} : ${who}` : tr("aucune unité à moins de 3 km", "no unit within 3 km"))
        + (i.reason ? ` · ${i.reason}` : "")
      : tr("fin de l'alerte", "alert over");
    if (ev.lat != null) li.onclick = () => flyToPoint(ev.lat, ev.lon);
    $("events").appendChild(li);
    return;
  }
  if (ev.status === "cleared") {
    li.querySelector(".meta").innerHTML = `<span>${fmtTime(ev.ts)}</span><span class="badge left">${tr("carte remise à zéro", "map reset")}</span>`;
    li.querySelector(".text").remove();
    li.querySelector(".res").remove();
    $("events").appendChild(li);
    return;
  }
  if (ev.status === "enemy_down") {
    li.querySelector(".meta .badge.enemy")?.remove();
    const b = li.querySelector(".meta .badge");
    b.className = "badge down";
    b.textContent = killed ? `${killed.join(", ")} ${tr("neutralisés", "neutralised")}` : `${ev.intent?.enemy_id} ${tr("neutralisé", "neutralised")}`;
    li.querySelector(".text").textContent = `« ${ev.transcript} »`;
    li.querySelector(".res").remove();
    $("events").appendChild(li);
    return;
  }
  if (ev.status === "facing") {
    const i = ev.intent || {};
    const b = li.querySelector(".meta .badge");
    b.className = "badge group";
    b.textContent = tr("direction", "heading");
    const dirs = currentLang() === "en" ? ["N", "NE", "E", "SE", "S", "SW", "W", "NW"] : ["N", "NE", "E", "SE", "S", "SO", "O", "NO"];
    const card = dirs[Math.round((i.heading_deg || 0) / 45) % 8];
    li.querySelector(".text").textContent = `« ${ev.transcript} »`;
    li.querySelector(".res").textContent = `${tr("regarde vers", "facing")} ${card} (${Math.round(i.heading_deg || 0)}°)`
      + (i.clock ? tr(` · ancien ${i.clock}h = nouveau midi`, ` · old ${i.clock} o'clock = new 12`) : "") + (i.moved_by ? ` · ${tr("avec", "with")} ${i.moved_by}` : "");
    $("events").appendChild(li);
    return;
  }
  if (ev.status === "group") {
    const i = ev.intent || {};
    const b = li.querySelector(".meta .badge");
    b.className = "badge group";
    b.textContent = i.type === "group_join" ? `${tr("escouade", "squad")} ${i.group_id}` : `${tr("quitte", "left")} ${i.group_id}`;
    li.querySelector(".text").textContent = i.type === "group_join"
      ? tr(`${ev.unit} forme l'escouade ${i.group_id} avec ${(i.members || []).filter((m) => m !== ev.unit).join(", ")}.`,
           `${ev.unit} formed squad ${i.group_id} with ${(i.members || []).filter((m) => m !== ev.unit).join(", ")}.`)
      : tr(`${ev.unit} quitte l'escouade ${i.group_id}.`, `${ev.unit} left squad ${i.group_id}.`);
    li.querySelector(".res").remove();
    $("events").appendChild(li);
    return;
  }
  if (ev.status === "left") {
    li.querySelector(".meta .badge").textContent = tr("a quitté", "left");
    li.querySelector(".text").textContent = tr("L'unité a quitté la carte.", "The unit left the map.");
    li.querySelector(".res").remove();
    $("events").appendChild(li);
    return;
  }
  li.dataset.seq = ev.seq;
  if (ev.status === "pending_review" && confirmedSeqs.has(ev.seq)) markConfirmed(li);
  li.querySelector(".text").textContent = `« ${ev.transcript} »`;
  li.querySelector(".res").textContent = describe(ev)
    + (ev.intent?.confirmed_from ? ` · ${tr("confirmée", "confirmed")}` : "")
    + (ev.intent?.delayed ? ` · ${tr("envoyée en différé", "sent late")}` : "")
    + (ev.intent?.callsign_ignored ? tr(` · indicatif « ${ev.intent.callsign} » ignoré`, ` · callsign “${ev.intent.callsign}” ignored`) : "")
    + (ev.intent?.moved_by ? ` · ${tr("déplacé avec", "moved with")} ${ev.intent.moved_by}` : "");
  li.onclick = () => flyTo(ev);
  // « À confirmer » avec un lieu trouvé, pour sa propre unité : bouton Confirmer.
  if (ev.status === "pending_review" && ev.lat != null && me && ev.unit === me.unit && !confirmedSeqs.has(ev.seq)) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "confirm";
    btn.textContent = tr("Confirmer", "Confirm");
    btn.onclick = async (e) => {
      e.stopPropagation();
      btn.disabled = true;
      try { const r = await identity.confirmReport(me, ev.seq); r.queued ? (btn.textContent = await queuedMsg()) : applyAll(r); }
      catch (err) { btn.disabled = false; btn.textContent = err.message; }
    };
    li.appendChild(btn);
  }
  $("events").appendChild(li);
}

// --- Boîte de confirmation aux couleurs du site -----------------------------

/** Demande une confirmation ; renvoie true si l'utilisateur valide. */
function ask({ title, text, ok, danger = false }) {
  const dlg = $("ask");
  $("ask-title").textContent = title;
  $("ask-text").textContent = text;
  $("ask-ok").textContent = ok;
  $("ask-cancel").textContent = tr("Annuler", "Cancel");
  dlg.classList.toggle("danger", danger);
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true });
    dlg.returnValue = "";
    dlg.showModal();
    $("ask-ok").focus();
  });
}

// --- Alerte de détresse -----------------------------------------------------

let bannerFor = null;
let audioCtx = null;

// Les navigateurs bloquent le son tant qu'on n'a pas touché la page : on prépare le son au
// premier clic ou appui de touche, pour que le bip d'alerte passe ensuite.
let speechReady = false;

function unlockAudio() {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") audioCtx.resume();
  } catch { /* pas de son disponible */ }
  if (!speechReady && window.speechSynthesis) {
    // Une phrase vide au premier geste : certains navigateurs exigent un geste avant de parler.
    speechSynthesis.speak(new SpeechSynthesisUtterance(""));
    speechReady = true;
  }
}

/** Meilleure voix disponible pour la langue (les voix « Google », « Premium » ou « Enhanced » sont plus nettes). */
function bestVoice(lang) {
  const voices = speechSynthesis.getVoices().filter((v) => v.lang.toLowerCase().startsWith(lang));
  const score = (v) => (/premium|enhanced|amélioré|google|neural|natural/i.test(v.name) ? 2 : 0) + (v.localService ? 0 : 1);
  return voices.sort((a, b) => score(b) - score(a))[0] || null;
}

/** Annonce vocale, volume maximal, débit lent, phrase par phrase avec une pause entre chaque. */
function say(sentences) {
  if (!window.speechSynthesis) return;
  speechSynthesis.cancel();
  const lang = currentLang() === "en" ? "en" : "fr";
  const voice = bestVoice(lang);
  for (const text of sentences) {
    const u = new SpeechSynthesisUtterance(text);
    u.lang = lang === "en" ? "en-US" : "fr-FR";
    if (voice) u.voice = voice;
    u.volume = 1;   // le maximum de la synthèse ; au-delà, c'est le volume de l'appareil
    u.rate = 0.85;  // plus lent = plus distinct
    u.pitch = 1;
    speechSynthesis.speak(u);
  }
}

/** Nom à annoncer : l'escouade de l'unité si elle en a une (« Escouade G1, ALPHA 1 »), sinon l'unité. */
function teamName(unit) {
  const entry = Object.entries(groups).find(([, ms]) => ms.includes(unit));
  return entry ? tr(`Escouade ${entry[0]}, ${unit}`, `Squad ${entry[0]}, ${unit}`) : unit;
}
["pointerdown", "keydown", "touchstart"].forEach((t) => window.addEventListener(t, unlockAudio, { passive: true }));

function beep() {
  try {
    unlockAudio();
    const ctx = audioCtx;
    [0, 0.25, 0.5].forEach((t) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = "square";      // plus perçant qu'une sinusoïde
      o.frequency.value = 960;
      g.gain.setValueAtTime(0.8, ctx.currentTime + t);
      g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + t + 0.18);
      o.connect(g).connect(ctx.destination);
      o.start(ctx.currentTime + t);
      o.stop(ctx.currentTime + t + 0.2);
    });
  } catch { /* son bloqué par le navigateur */ }
  navigator.vibrate?.([300, 120, 300, 120, 300]);
}

function whereFromMe(ev) {
  const mine = me && unitPosition(me.unit);
  if (!mine) {
    const r = (ev.intent?.recipients || []).find((x) => x.unit === me?.unit);
    return r ? `${r.distance_m} m` : "";
  }
  const [la1, lo1] = mine;
  const dy = (ev.lat - la1) * 111_320;
  const dx = (ev.lon - lo1) * 111_320 * Math.cos((la1 * Math.PI) / 180);
  const dist = Math.round(Math.hypot(dx, dy));
  const deg = (Math.atan2(dx, dy) * 180 / Math.PI + 360) % 360;
  const dirs = currentLang() === "en" ? ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]
    : ["nord", "nord-est", "est", "sud-est", "sud", "sud-ouest", "ouest", "nord-ouest"];
  return tr(`à ${dist} m au ${dirs[Math.round(deg / 45) % 8]}`, `${dist} m to the ${dirs[Math.round(deg / 45) % 8]}`);
}

function showBanner(ev) {
  bannerFor = ev.unit;
  const name = teamName(ev.unit);
  const where = whereFromMe(ev);
  $("alert-title").textContent = tr(`${name} a besoin de vous`, `${name} needs you`);
  // Alerte envoyée en différé (l'unité était hors ligne) : depuis combien de temps elle a été lancée.
  const ageMin = Math.round((Date.now() - new Date(ev.ts).getTime()) / 60000);
  const late = ev.intent?.delayed && ageMin >= 1 ? tr(`lancée il y a ${ageMin} min`, `raised ${ageMin} min ago`) : "";
  $("alert-where").textContent = [where, ev.intent?.reason, late].filter(Boolean).join(" · ");
  $("alert-go").textContent = tr("J'y vais", "On my way");
  $("alert-see").textContent = tr("Voir sur la carte", "Show on map");
  $("alert-close").textContent = tr("Fermer", "Dismiss");
  $("alert-see").onclick = () => flyToPoint(ev.lat, ev.lon);
  $("alert-close").onclick = hideBanner;
  $("alert-go").onclick = async () => {
    $("alert-go").disabled = true;
    try { applyAll(await identity.sendText(me, tr(`Je vais vers ${ev.unit}.`, `I am heading to ${ev.unit}.`))); hideBanner(); }
    catch (err) { $("alert-where").textContent = err.message; }
    finally { $("alert-go").disabled = false; }
  };
  $("alert-banner").hidden = false;
  beep();
  // Après les bips : l'annonce à voix haute.
  // Après les bips : l'annonce à voix haute, phrases courtes, le nom répété pour être sûr de l'entendre.
  setTimeout(() => say([
    tr("Alerte renfort.", "Backup alert."),
    tr(`${name} a besoin de vous.`, `${name} needs you.`),
    ...(where ? [where.replace(/^à /, tr("À ", "")) + "."] : []),
    ...(ev.intent?.reason ? [`${ev.intent.reason}.`] : []),
    tr(`Je répète : ${name} a besoin de vous.`, `I repeat: ${name} needs you.`),
  ]), 1100);
}

function hideBanner() {
  bannerFor = null;
  $("alert-banner").hidden = true;
}

function renderSosButtons() {
  const mine = me && activeAlerts.has(me.unit);
  $("sos").hidden = !!mine;
  $("sos-clear").hidden = !mine;
}

/** Auteur d'une ligne d'historique : l'unité, ou l'escouade quand elle s'est déplacée ensemble. */
function whoLabel(ev) {
  const i = ev.intent || {};
  if (!i.squad_members) return ev.unit;
  const head = i.squad ? `${tr("Escouade", "Squad")} ${i.squad}` : tr("Ensemble", "Together");
  return `${head} · ${i.squad_members.join(", ")}`;
}

function markConfirmed(li) {
  const b = li.querySelector(".meta .badge");
  if (b) { b.className = "badge confirmed"; b.textContent = tr("confirmé", "confirmed"); }
  li.querySelector("button.confirm")?.remove();
}

let connected = false;

function handle(ev, live) {
  if (seen.has(ev.id)) return;
  seen.add(ev.id);
  allEvents.push(ev);
  saveState();
  if (ev.status === "cleared") activeAlerts.clear();
  if (ev.status === "alert") {
    activeAlerts.set(ev.unit, ev);
    if (live && me && ev.unit !== me.unit && (ev.intent?.recipients || []).some((r) => r.unit === me.unit)) showBanner(ev);
  }
  if (ev.status === "alert_clear") {
    activeAlerts.delete(ev.unit);
    if (bannerFor === ev.unit) hideBanner();
  }
  if (["alert", "alert_clear", "cleared"].includes(ev.status)) renderSosButtons();
  const from = ev.intent?.confirmed_from;
  if (from) {
    confirmedSeqs.add(from);
    const li = document.querySelector(`#events li[data-seq="${from}"]`);
    if (li) markConfirmed(li);
  }
  if (live && ["group", "left", "cleared"].includes(ev.status)) refreshGroups();
  // On ne recentre la carte que sur sa propre unité (ou sur tout le monde au poste de commandement).
  // Un ennemi signalé recentre tout le monde ; une position, seulement le terminal concerné.
  const enemy = ev.intent?.subject === "enemy";
  applyEvent(ev, { focus: live && (enemy || !me || ev.unit === me.unit) });
  addToHistory(ev);
}

// --- Hors ligne : annonces gardées, envoyées au retour du réseau --------------

async function queuedMsg() {
  const n = await refreshPending();
  return tr(`Hors ligne : annonce gardée (${n} en attente). Elle partira dès le retour du réseau.`,
            `Offline: report kept (${n} pending). It will be sent as soon as the network is back.`);
}

async function refreshPending() {
  const n = await identity.pendingCount();
  const el = $("pending");
  el.hidden = n === 0;
  el.textContent = tr(`${n} en attente`, `${n} pending`);
  return n;
}

async function flushPending() {
  if (!(await identity.pendingCount())) return;
  let sent = 0;
  await identity.flushOutbox({
    onSent: (ev) => { sent += 1; applyAll(ev); },
    onError: (msg) => { $("form-msg").className = "msg err"; $("form-msg").textContent = `${tr("Annonce gardée refusée", "Kept report refused")} : ${msg}`; },
  });
  const left = await refreshPending();
  if (sent) {
    $("form-msg").className = "msg ok";
    $("form-msg").textContent = tr(`Réseau revenu : ${sent} annonce(s) envoyée(s) en différé.`, `Network back: ${sent} report(s) sent late.`)
      + (left ? tr(` ${left} encore en attente.`, ` ${left} still pending.`) : "");
  }
}

/** Une annonce peut contenir plusieurs faits (ev.also) : on les applique tous et on les résume. */
function applyAll(ev) {
  const all = [ev, ...(ev.also || [])];
  all.forEach((e) => handle(e, true));
  return all.map((e) => {
    const i = e.intent || {};
    if (e.status === "alert") return tr(`renfort demandé : ${(i.recipients || []).length} unité(s) prévenue(s)`, `backup requested: ${(i.recipients || []).length} unit(s) notified`);
    if (e.status === "alert_clear") return tr("situation sécurisée", "situation secured");
    if (e.status === "facing") return `${e.unit} ${tr("regarde vers", "facing")} ${Math.round(i.heading_deg || 0)}°`;
    if (e.status === "group") return i.type === "group_join" ? `${tr("escouade", "squad")} ${i.group_id}: ${(i.members || []).join(", ")}` : `${tr("quitte", "left")} ${i.group_id}`;
    if (e.status === "enemy_down") return `${(i.kill_ids || [i.enemy_id]).join(", ")} ${tr(i.kill_ids ? "neutralisés" : "neutralisé", "neutralised")}`;
    if (e.status === "pending_review" && e.lat != null) return tr(`à confirmer : ${e.resolved_landmark} (bouton ou « confirmé »)`, `to confirm: ${e.resolved_landmark} (button or “confirmed”)`);
    if (e.status !== "confirmed") return tr("lieu non trouvé, à confirmer", "place not found, to confirm");
    if (i.confirmed_from) return `${tr("confirmé", "confirmed")}: ${e.resolved_landmark || "position"}`;
    if (i.subject === "enemy") return `${i.group_ids ? `${i.group_ids.length} ${tr("ennemis", "hostiles")}` : i.enemy_id}: ${e.resolved_landmark}`;
    return e.intent?.moved_by ? `${e.unit} ${tr("aussi", "too")}` : `${tr("placé", "placed")}: ${e.resolved_landmark || "position"}`;
  }).join(" · ");
}

// --- Escouade : encart en haut à gauche de la carte ------------------------

async function refreshGroups() {
  try { groups = await (await fetch("/api/groups")).json(); } catch { return; }
  renderSquad();
}

function renderSquad() {
  if (!squadEl) return;
  squadEl.replaceChildren();
  const mine = me && Object.entries(groups).find(([, ms]) => ms.includes(me.unit));
  const shown = me ? (mine ? [mine] : []) : (pcMode.get() ? Object.entries(groups) : []);
  squadEl.hidden = shown.length === 0;
  for (const [gid, members] of shown) {
    const title = document.createElement("div");
    title.className = "squad-title";
    title.textContent = mine ? tr(`Escouade ${gid} · vous êtes relié`, `Squad ${gid} · you are linked`) : `${tr("Escouade", "Squad")} ${gid}`;
    const list = document.createElement("div");
    list.className = "squad-members";
    for (const m of members) {
      const chip = document.createElement("span");
      chip.className = `squad-chip${me && m === me.unit ? " me" : ""}`;
      chip.style.setProperty("--unit-color", unitColor(m));
      chip.textContent = m;
      list.append(chip);
    }
    squadEl.append(title, list);
  }
  if (mine) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "link";
    btn.textContent = tr("Quitter l'escouade", "Leave the squad");
    btn.onclick = async () => {
      btn.disabled = true;
      try { const r = await identity.leaveGroup(me); if (r.queued) btn.textContent = await queuedMsg(); }
      catch (err) { btn.textContent = err.message; }
    };
    squadEl.append(btn);
  }
}

async function refreshColors() {
  try { setUnitColors(await (await fetch("/api/units")).json()); renderSquad(); } catch { /* pas grave */ }
}

async function refreshTaken() {
  try {
    const list = await (await fetch("/api/units")).json();
    $("taken").textContent = list.length ? `${tr("Déjà prises", "Already taken")}: ${list.map((u) => u.unit).join(", ")}` : "";
  } catch { /* pas grave */ }
}

function render() {
  const pc = !me && pcMode.get();
  $("claim-view").hidden = Boolean(me) || pc;
  $("me-view").hidden = !me && !pc;
  $("me-view").classList.toggle("pc", pc);
  $("report-form").hidden = !me;
  $("samples").hidden = !me;
  $("me-label").textContent = pc ? tr("Poste de commandement", "Command post") : tr("Ce terminal gère", "This terminal runs");
  $("me").textContent = pc ? tr("lecture seule", "read only") : me?.unit || "";
  $("release").hidden = pc;
  $("leave").hidden = !pc;
  setOwnUnit(me?.unit || null);
  announcePresence();
  renderSquad();
  renderSosButtons();
  // Seul un terminal qui tient une unité peut neutraliser (l'action est signée par sa clé).
  setKillHandler(me ? async (id) => {
    const r = await identity.killEnemy(me, id);
    if (r.queued) { $("form-msg").className = "msg err"; $("form-msg").textContent = await queuedMsg(); }
    return r;
  } : null);
  if (!me && !pc) refreshTaken();
}

function relang() {
  // Nouvelle langue : on reconstruit ce qui a été écrit en JS.
  $("conn-label").textContent = connected ? tr("Connecté", "Connected") : tr("Déconnecté", "Disconnected");
  $("events").replaceChildren();
  allEvents.forEach(addToHistory);
  render();
  refreshTaken();
}

async function main() {
  setupPrefs(DICT);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
  onLangChange(relang);
  // Hors ligne : configuration gardée de la dernière visite.
  let config;
  try {
    config = await (await fetch("/api/config")).json();
    try { localStorage.setItem("vtm-config", JSON.stringify(config)); } catch {}
  } catch {
    try { config = JSON.parse(localStorage.getItem("vtm-config")); } catch {}
    config = config || { node_mode: "central", llm: "mistral", zone_name: "Paris centre", center: [48.8584, 2.347], sim: false };
  }
  $("node").textContent = `${config.node_mode} · ${config.llm} · ${config.zone_name}`;
  initMap(config.center);
  await refreshColors(); // avant de rejouer l'historique : les tracés prennent tout de suite la bonne couleur
  squadEl = squadBox();
  refreshGroups();

  if (!(await identity.supported())) {
    $("claim-msg").className = "msg err";
    $("claim-msg").textContent = tr("Ce navigateur ne gère pas les signatures Ed25519. Mettez à jour Chrome, Safari ou Firefox.", "This browser does not support Ed25519 signatures. Update Chrome, Safari or Firefox.");
    $("claim").disabled = true;
  }
  me = await identity.current().catch(() => null);
  // Unités remises à zéro côté serveur (scripts/clear_units.py) : l'onglet repasse au choix d'unité.
  if (me) {
    // Hors ligne, on ne peut pas vérifier : on garde l'unité (les annonces partiront au retour du réseau).
    const taken = await fetch("/api/units").then((r) => r.json()).catch(() => null);
    // Unité libérée, ou reprise par un autre terminal (autre clé) : on repasse au choix.
    if (taken && !taken.some((u) => u.unit === me.unit && u.public_key === me.publicKey)) { identity.leave(); me = null; }
  }
  render();

  const doClaim = async () => {
    const wanted = $("unit").value.trim();
    if (!wanted) return;
    $("claim").disabled = true;
    $("claim-msg").className = "msg";
    $("claim-msg").textContent = tr("Réservation…", "Reserving…");
    try {
      me = await identity.claim(wanted);
      pcMode.set(false);
      $("claim-msg").textContent = "";
      render();
    } catch (err) {
      $("claim-msg").className = "msg err";
      $("claim-msg").textContent = err.message;
    } finally {
      $("claim").disabled = false;
    }
  };
  $("claim").onclick = doClaim;
  $("unit").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); doClaim(); } });
  $("pc-mode").onclick = () => { pcMode.set(true); render(); };
  $("leave").onclick = () => { identity.leave(); me = null; pcMode.set(false); render(); };
  $("release").onclick = async () => {
    if (!me || !(await ask({
      title: tr(`Quitter ${me.unit} ?`, `Leave ${me.unit}?`),
      text: tr("Son point disparaît de la carte et un autre terminal pourra la prendre.",
               "Its point disappears from the map and another terminal can take it."),
      ok: tr("Quitter l'unité", "Leave the unit"),
    }))) return;
    try {
      await identity.release(me);
      me = null;
      render();
    } catch (err) {
      $("claim-msg").className = "msg err";
      $("claim-msg").textContent = `${tr("Impossible de libérer", "Could not release")} : ${err.message}`;
    }
  };

  // Dire au serveur quelle unité cet onglet tient : tant qu'il est connecté, personne ne peut la reprendre.
  let socket = null;
  let gotSnapshot = false;
  // Pas de réponse du serveur au chargement : on réaffiche le dernier état connu de la carte.
  setTimeout(() => {
    if (gotSnapshot) return;
    const saved = cachedEvents();
    if (!saved.length) return;
    saved.forEach((ev) => handle(ev, false));
    $("form-msg").className = "msg err";
    $("form-msg").textContent = tr("Hors ligne : carte affichée telle qu'à la dernière connexion. Vos annonces seront gardées.",
                                   "Offline: map shown as of the last connection. Your reports will be kept.");
  }, 2500);
  announcePresence = () => socket?.send({ type: "presence", unit: me?.unit || null });
  socket = connect({
    onOpen: () => { announcePresence(); flushPending(); },
    onStatus: (up) => {
      $("conn").className = `dot ${up ? "on" : "off"}`;
      connected = up;
      $("conn-label").textContent = up ? tr("Connecté", "Connected") : tr("Déconnecté", "Disconnected");
    },
    onMessage: (msg) => {
      if (msg.type === "snapshot") { gotSnapshot = true; msg.events.forEach((ev) => handle(ev, false)); }
      if (msg.type === "event") handle(msg.event, true);
      if (msg.type === "sim") simListener(msg.on);
      if (msg.type === "units") refreshColors();
      if (msg.type === "unit_owner" && me && msg.unit === me.unit && msg.public_key !== me.publicKey) {
        const lost = me.unit;
        identity.leave();
        me = null;
        render();
        $("claim-msg").className = "msg err";
        $("claim-msg").textContent = tr(`${lost} a été reprise par un autre terminal.`, `${lost} was taken over by another terminal.`);
      }
    },
  });

  $("report-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("text").value.trim();
    if (!text) return;
    $("send").disabled = true;
    $("form-msg").className = "msg";
    $("form-msg").textContent = tr("Analyse en cours…", "Analysing…");
    try {
      const ev = await identity.sendText(me, text);
      $("form-msg").textContent = ev.queued ? await queuedMsg() : applyAll(ev);
      $("text").value = "";
    } catch (err) {
      $("form-msg").className = "msg err";
      $("form-msg").textContent = `${tr("Échec de l'envoi", "Sending failed")}: ${err.message}`;
    } finally {
      $("send").disabled = false;
    }
  });

  const setMsg = (kind, text) => {
    $("form-msg").className = `msg ${kind}`;
    $("form-msg").textContent = text;
  };
  setupPushToTalk({
    button: $("ptt"),
    upload: (blob, filename) => identity.sendAudio(me, blob, filename),
    onStatus: setMsg,
    onEvent: async (ev) => ev.queued ? setMsg("err", await queuedMsg())
      : setMsg(ev.status === "pending_review" && !ev.also ? "" : "ok", `« ${ev.transcript} » → ${applyAll(ev)}`),
  });

  document.querySelectorAll(".sample").forEach((b) => {
    b.onclick = () => { $("text").value = b.textContent; $("text").focus(); };
  });

  // Simulation : les ennemis actifs marchent tout seuls dans les rues.
  const showSim = (on) => {
    $("sim").textContent = on ? "Simulation ennemis : en marche" : "Simulation ennemis : arrêtée";
    $("sim").classList.toggle("on", on);
    $("sim").dataset.on = on ? "1" : "";
  };
  showSim(config.sim);
  $("sim").onclick = async () => {
    const r = await fetch("/api/sim", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ on: !$("sim").dataset.on }) });
    if (r.ok) showSim((await r.json()).on);
  };
  simListener = showSim;

  $("sos").onclick = async () => {
    if (!me || !(await ask({
      title: tr("Demander du renfort ?", "Request backup?"),
      text: tr("Toutes les unités à moins de 3 km recevront une alerte avec votre position.",
               "Every unit within 3 km will get an alert with your position."),
      ok: tr("Envoyer l'alerte", "Send the alert"),
      danger: true,
    }))) return;
    try {
      const r = await identity.raiseAlert(me);
      if (r.queued) { $("form-msg").className = "msg err"; $("form-msg").textContent = await queuedMsg(); }
      else applyAll(r);
    } catch (err) { $("form-msg").className = "msg err"; $("form-msg").textContent = err.message; }
  };
  $("sos-clear").onclick = async () => {
    try {
      const r = await identity.clearAlert(me);
      if (r.queued) $("form-msg").textContent = await queuedMsg(); else applyAll(r);
    } catch (err) { $("form-msg").textContent = err.message; }
  };

  window.addEventListener("online", flushPending);
  setInterval(flushPending, 15_000);
  refreshPending();

  $("clear-history").onclick = () => {
    hiddenUntil = lastSeq;
    try { localStorage.setItem(HIDE_KEY, String(hiddenUntil)); } catch { /* navigation privée */ }
    $("events").replaceChildren();
  };

  $("verify").onclick = async () => {
    const r = await (await fetch("/api/integrity")).json();
    $("integrity").className = `msg ${r.ok ? "ok" : "err"}`;
    $("integrity").textContent = r.ok
      ? tr(`Chaîne intacte : ${r.count} événements vérifiés, dont ${r.signed} signés par leur terminal.`,
           `Chain intact: ${r.count} events checked, ${r.signed} signed by their terminal.`)
      : tr(`${r.reason === "signature" ? "Signature invalide" : "Chaîne cassée"} à l'événement #${r.broken_at}.`,
           `${r.reason === "signature" ? "Invalid signature" : "Broken chain"} at event #${r.broken_at}.`);
  };
}

main();
