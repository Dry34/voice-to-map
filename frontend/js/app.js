// Assemblage : formulaire, historique, intégrité. Responsable : A.

import { applyEvent, flyTo, initMap, unitColor } from "./map.js";
import { connect } from "./socket.js";

const $ = (id) => document.getElementById(id);
const seen = new Set();

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
  const li = document.createElement("li");
  li.className = "event";
  li.style.setProperty("--unit-color", unitColor(ev.unit));
  li.innerHTML = `
    <div class="meta"><span>${fmtTime(ev.ts)}</span><span>${ev.unit}</span>
      <span class="badge ${ev.status}">${ev.status === "confirmed" ? "placé" : "à confirmer"}</span></div>
    <div class="text"></div>
    <div class="res"></div>`;
  li.querySelector(".text").textContent = `« ${ev.transcript} »`;
  li.querySelector(".res").textContent = describe(ev);
  li.onclick = () => flyTo(ev);
  $("events").appendChild(li);
}

function handle(ev, focus) {
  if (seen.has(ev.id)) return;
  seen.add(ev.id);
  applyEvent(ev, { focus });
  addToHistory(ev);
}

async function main() {
  const config = await (await fetch("/api/config")).json();
  $("node").textContent = `${config.node_mode} · ${config.llm} · ${config.zone_name}`;
  initMap(config.center);

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
      const r = await fetch("/api/reports", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ unit: $("unit").value, text }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const ev = await r.json();
      handle(ev, true);
      $("form-msg").textContent = ev.status === "confirmed"
        ? `Placé : ${ev.resolved_landmark || "position"}`
        : "Lieu non trouvé, annonce à confirmer.";
      $("text").value = "";
    } catch (err) {
      $("form-msg").className = "msg err";
      $("form-msg").textContent = `Échec de l'envoi (${err.message}). Le backend tourne-t-il ?`;
    } finally {
      $("send").disabled = false;
    }
  });

  document.querySelectorAll(".sample").forEach((b) => {
    b.onclick = () => { $("text").value = b.textContent; $("text").focus(); };
  });

  $("verify").onclick = async () => {
    const r = await (await fetch("/api/integrity")).json();
    $("integrity").className = `msg ${r.ok ? "ok" : "err"}`;
    $("integrity").textContent = r.ok
      ? `Chaîne intacte : ${r.count} événements vérifiés.`
      : `Chaîne cassée à l'événement #${r.broken_at}.`;
  };
}

main();
