// Assemblage : unité du terminal, formulaire, historique, intégrité. Responsable : A.
//
// Chaque terminal gère UNE unité : ses annonces (voix ou texte) sont signées avec sa clé
// et comptent pour cette unité, quel que soit l'indicatif prononcé. La carte montre
// toutes les unités, la sienne mise en avant. Le poste de commandement voit tout sans unité.

import * as identity from "./identity.js";
import { applyEvent, flyTo, initMap, setKillHandler, setOwnUnit, unitColor } from "./map.js";
import { setupPushToTalk } from "./ptt.js";
import { connect } from "./socket.js";

const $ = (id) => document.getElementById(id);
const seen = new Set();
let me = null; // identité de cet onglet : {unit, privateKey, publicKey}
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

function addToHistory(ev) {
  // Groupe d'ennemis (« 5 ennemis à… ») : une seule ligne dans l'historique, pour le premier.
  const group = ev.intent?.group_ids;
  if (group && ev.intent.enemy_id !== group[0]) return;
  const li = document.createElement("li");
  li.className = "event";
  li.style.setProperty("--unit-color", unitColor(ev.unit));
  li.innerHTML = `
    <div class="meta"><span>${fmtTime(ev.ts)}</span><span>${ev.unit}</span>
      <span class="badge ${ev.status}">${ev.status === "confirmed" ? "placé" : "à confirmer"}</span>
      ${ev.intent?.subject === "enemy" ? `<span class="badge enemy">${group ? `${group[0]}–${group[group.length - 1]} (${group.length})` : ev.intent.enemy_id || "ennemi"}${!group && ev.intent.count ? ` ×${ev.intent.count}` : ""}</span>` : ""}</div>
    <div class="text"></div>
    <div class="res"></div>`;
  if (ev.status === "cleared") {
    li.querySelector(".meta").innerHTML = `<span>${fmtTime(ev.ts)}</span><span class="badge left">carte remise à zéro</span>`;
    li.querySelector(".text").remove();
    li.querySelector(".res").remove();
    $("events").appendChild(li);
    return;
  }
  if (ev.status === "enemy_down") {
    const b = li.querySelector(".meta .badge");
    b.className = "badge down";
    b.textContent = `${ev.intent?.enemy_id} neutralisé`;
    li.querySelector(".text").textContent = `« ${ev.transcript} »`;
    li.querySelector(".res").remove();
    $("events").appendChild(li);
    return;
  }
  if (ev.status === "left") {
    li.querySelector(".meta .badge").textContent = "a quitté";
    li.querySelector(".text").textContent = "L'unité a quitté la carte.";
    li.querySelector(".res").remove();
    $("events").appendChild(li);
    return;
  }
  li.querySelector(".text").textContent = `« ${ev.transcript} »`;
  li.querySelector(".res").textContent = describe(ev) + (ev.intent?.callsign_ignored ? ` · indicatif « ${ev.intent.callsign} » ignoré` : "");
  li.onclick = () => flyTo(ev);
  $("events").appendChild(li);
}

function handle(ev, live) {
  if (seen.has(ev.id)) return;
  seen.add(ev.id);
  // On ne recentre la carte que sur sa propre unité (ou sur tout le monde au poste de commandement).
  // Un ennemi signalé recentre tout le monde ; une position, seulement le terminal concerné.
  const enemy = ev.intent?.subject === "enemy";
  applyEvent(ev, { focus: live && (enemy || !me || ev.unit === me.unit) });
  addToHistory(ev);
}

async function refreshTaken() {
  try {
    const list = await (await fetch("/api/units")).json();
    $("taken").textContent = list.length ? `Déjà prises : ${list.map((u) => u.unit).join(", ")}` : "";
  } catch { /* pas grave */ }
}

function render() {
  const pc = !me && pcMode.get();
  $("claim-view").hidden = Boolean(me) || pc;
  $("me-view").hidden = !me && !pc;
  $("me-view").classList.toggle("pc", pc);
  $("report-form").hidden = !me;
  $("samples").hidden = !me;
  $("me-label").textContent = pc ? "Poste de commandement" : "Ce terminal gère";
  $("me").textContent = pc ? "lecture seule" : me?.unit || "";
  $("release").hidden = pc;
  $("leave").hidden = !pc;
  setOwnUnit(me?.unit || null);
  // Seul un terminal qui tient une unité peut neutraliser (l'action est signée par sa clé).
  setKillHandler(me ? (id) => identity.killEnemy(me, id) : null);
  if (!me && !pc) refreshTaken();
}

async function main() {
  const config = await (await fetch("/api/config")).json();
  $("node").textContent = `${config.node_mode} · ${config.llm} · ${config.zone_name}`;
  initMap(config.center);

  if (!(await identity.supported())) {
    $("claim-msg").className = "msg err";
    $("claim-msg").textContent = "Ce navigateur ne gère pas les signatures Ed25519. Mettez à jour Chrome, Safari ou Firefox.";
    $("claim").disabled = true;
  }
  me = await identity.current().catch(() => null);
  // Unités remises à zéro côté serveur (scripts/clear_units.py) : l'onglet repasse au choix d'unité.
  if (me) {
    const taken = await (await fetch("/api/units")).json().catch(() => []);
    if (!taken.some((u) => u.unit === me.unit)) { identity.leave(); me = null; }
  }
  render();

  const doClaim = async () => {
    const wanted = $("unit").value.trim();
    if (!wanted) return;
    $("claim").disabled = true;
    $("claim-msg").className = "msg";
    $("claim-msg").textContent = "Réservation…";
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
    if (!me || !confirm(`Quitter ${me.unit} ? Son point disparaît de la carte et un autre terminal pourra la prendre.`)) return;
    try {
      await identity.release(me);
      me = null;
      render();
    } catch (err) {
      alert(`Impossible de libérer : ${err.message}`);
    }
  };

  connect({
    onStatus: (up) => {
      $("conn").className = `dot ${up ? "on" : "off"}`;
      $("conn-label").textContent = up ? "Connecté" : "Déconnecté";
    },
    onMessage: (msg) => {
      if (msg.type === "snapshot") msg.events.forEach((ev) => handle(ev, false));
      if (msg.type === "event") handle(msg.event, true);
    },
  });

  $("report-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("text").value.trim();
    if (!text) return;
    $("send").disabled = true;
    $("form-msg").className = "msg";
    $("form-msg").textContent = "Analyse en cours…";
    try {
      const ev = await identity.sendText(me, text);
      handle(ev, true);
      $("form-msg").textContent = ev.status === "confirmed"
        ? `Placé : ${ev.resolved_landmark || "position"}`
        : "Lieu non trouvé, annonce à confirmer.";
      $("text").value = "";
    } catch (err) {
      $("form-msg").className = "msg err";
      $("form-msg").textContent = `Échec de l'envoi : ${err.message}`;
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
    onEvent: (ev) => {
      handle(ev, true);
      setMsg(ev.status === "confirmed" ? "ok" : "",
        `« ${ev.transcript} » → ${ev.status === "confirmed" ? `placé : ${ev.resolved_landmark || "position"}` : "lieu non trouvé, à confirmer"}`);
    },
  });

  document.querySelectorAll(".sample").forEach((b) => {
    b.onclick = () => { $("text").value = b.textContent; $("text").focus(); };
  });

  $("verify").onclick = async () => {
    const r = await (await fetch("/api/integrity")).json();
    $("integrity").className = `msg ${r.ok ? "ok" : "err"}`;
    $("integrity").textContent = r.ok
      ? `Chaîne intacte : ${r.count} événements vérifiés, dont ${r.signed} signés par leur terminal.`
      : `${r.reason === "signature" ? "Signature invalide" : "Chaîne cassée"} à l'événement #${r.broken_at}.`;
  };
}

main();
