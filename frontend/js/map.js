// Carte Leaflet : un marqueur, un cercle d'incertitude et un tracé par unité. Responsable : A.

const COLORS = ["#f07a45", "#7fb0de", "#6cc08f", "#e0b04e", "#c58fe0", "#e06c8f"];
const units = new Map();
let map;
let own = null; // unité gérée par ce terminal, mise en avant

function styleFor(unit, color) {
  const mine = unit === own;
  return { radius: mine ? 11 : 7, color: "#fff", weight: mine ? 3 : 2, fillColor: color, fillOpacity: 1 };
}

function label(marker, text, className) {
  // On recrée l'étiquette : Leaflet ne relit pas className sur une étiquette déjà affichée.
  marker.unbindTooltip();
  marker.bindTooltip(text, { permanent: true, direction: "right", offset: [12, 0], className }).openTooltip();
}

function restyle(unit, u) {
  if (!u.marker) return;
  u.marker.setStyle(styleFor(unit, u.color));
  label(u.marker, unit === own ? `${unit} (vous)` : unit, `unit-label${unit === own ? " own" : ""}`);
  u.path.setStyle({ weight: unit === own ? 4 : 2, opacity: unit === own ? 1 : 0.7 });
  if (unit === own) u.marker.bringToFront();
}

/** Met en avant l'unité de ce terminal (null : aucune, poste de commandement). */
export function setOwnUnit(unit) {
  own = unit;
  units.forEach((u, name) => restyle(name, u));
}

// Ennemis vivants regroupés : un rond rouge « 5 » quand on dézoome, séparés quand on zoome.
let enemyLayer;

export function initMap(center) {
  map = L.map("map", { maxZoom: 19 }).setView(center, 15); // maxZoom exigé par le regroupement des ennemis
  enemyLayer = L.markerClusterGroup({
    maxClusterRadius: 45,
    showCoverageOnHover: false,
    spiderfyOnMaxZoom: true,      // au zoom max, encore collés : on les écarte en étoile
    zoomToBoundsOnClick: true,
    iconCreateFunction: (cluster) => L.divIcon({
      html: `${cluster.getChildCount()}`, className: "enemy-cluster", iconSize: [34, 34], iconAnchor: [17, 17],
    }),
  }).addTo(map);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);
  return map;
}

export function unitColor(unit) {
  if (!units.has(unit)) {
    const color = COLORS[units.size % COLORS.length];
    units.set(unit, { color, marker: null, circle: null, path: null, points: [] });
  }
  return units.get(unit).color;
}

// --- Ennemis : un losange rouge par ennemi (E1, E2…), qui pâlit avec le temps -------
// Un nouveau signalement du même ennemi le déplace ; « neutralisé » le grise et le barre.

const enemies = new Map(); // "E3" -> {ev, marker, circle, path, points, down}
const ENEMY_FADE_MIN = 30; // au-delà, la dernière observation est affichée très pâle
let killHandler = null;

/** Fonction appelée par le bouton « Neutraliser » d'un ennemi (null : pas de bouton). */
export function setKillHandler(fn) {
  killHandler = fn;
  enemies.forEach((e, id) => refreshEnemy(id));
}

function enemyText(e, id) {
  const i = e.ev.intent || {};
  const what = i.description || (i.count ? `${i.count} ennemi${i.count > 1 ? "s" : ""}` : "ennemi");
  const t = new Date(e.ev.ts).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  if (e.down) return `✕ ${id}`;
  // Dans un groupe, étiquette courte pour que les 5 losanges restent lisibles.
  return i.group_ids ? `⚠ ${id}` : `⚠ ${id} · ${what} · ${t}`;
}

function enemyOpacity(e) {
  if (e.down) return 0.55;
  const ageMin = (Date.now() - new Date(e.ev.ts).getTime()) / 60000;
  return Math.max(0.3, 1 - ageMin / ENEMY_FADE_MIN);
}

function popupFor(e, id) {
  const box = document.createElement("div");
  box.className = "enemy-popup";
  const title = document.createElement("b");
  title.textContent = enemyText(e, id);
  const who = document.createElement("div");
  who.textContent = e.down ? `Neutralisé par ${e.downBy}` : `Signalé par ${e.ev.unit}`;
  const quote = document.createElement("div");
  quote.className = "quote";
  quote.textContent = `« ${e.ev.transcript} »`;
  box.append(title, who, quote);
  if (!e.down && killHandler && /^E\d+$/.test(id)) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "kill";
    btn.textContent = `Neutraliser ${id}`;
    btn.onclick = async () => {
      btn.disabled = true;
      btn.textContent = "…";
      try { await killHandler(id); map.closePopup(); }
      catch (err) { btn.disabled = false; btn.textContent = `Échec : ${err.message}`; }
    };
    box.append(btn);
  }
  return box;
}

function refreshEnemy(id) {
  const e = enemies.get(id);
  if (!e) return;
  e.marker.setIcon(L.divIcon({ className: `enemy-icon${e.down ? " down" : ""}`, html: "<span></span>", iconSize: [18, 18], iconAnchor: [9, 9] }));
  label(e.marker, enemyText(e, id), `unit-label enemy${e.down ? " down" : ""}`);
  e.marker.setPopupContent(popupFor(e, id));
  const o = enemyOpacity(e);
  e.marker.setOpacity(o);
  if (e.circle) e.circle.setStyle({ opacity: e.down ? 0 : o, fillOpacity: e.down ? 0 : 0.1 * o });
  e.path.setStyle({ opacity: 0.6 * o });
}

function addEnemy(ev, focus) {
  const id = ev.intent?.enemy_id || `?${ev.seq}`;
  const pos = [ev.lat, ev.lon];
  let e = enemies.get(id);
  if (!e) {
    const marker = L.marker(pos, { zIndexOffset: 500 }).bindPopup("");
    enemyLayer.addLayer(marker);
    // Pas de cercle d'incertitude par ennemi d'un groupe : 5 cercles superposés illisibles.
    const circle = ev.intent?.group_ids ? null
      : L.circle(pos, { radius: ev.uncertainty_m || 60, color: "#e5484d", weight: 1, dashArray: "2 4" }).addTo(map);
    const path = L.polyline([pos], { color: "#e5484d", weight: 2, dashArray: "6 6" }).addTo(map);
    e = { ev, marker, circle, path, points: [pos], down: false };
    enemies.set(id, e);
  } else {
    e.ev = ev;
    e.points.push(pos);
    // Le regroupement ne suit pas un marqueur déplacé : on le retire puis on le remet.
    enemyLayer.removeLayer(e.marker);
    e.marker.setLatLng(pos);
    enemyLayer.addLayer(e.marker);
    if (e.circle) e.circle.setLatLng(pos).setRadius(ev.uncertainty_m || 60);
    e.path.setLatLngs(e.points);
  }
  refreshEnemy(id);
  if (focus) map.panTo(pos);
}

function killEnemy(ev) {
  const id = ev.intent?.enemy_id;
  const e = enemies.get(id);
  if (!e) return;
  e.down = true;
  e.downBy = ev.unit;
  // Neutralisé : sort du regroupement (le chiffre du cluster ne compte que les vivants), reste affiché en gris.
  enemyLayer.removeLayer(e.marker);
  e.marker.addTo(map);
  refreshEnemy(id);
}

setInterval(() => enemies.forEach((e, id) => { if (!e.down) refreshEnemy(id); }), 60_000);

/** L'unité a quitté la carte : on retire son marqueur et son tracé (elle repart de zéro si elle revient). */
function removeUnit(unit) {
  const u = units.get(unit);
  if (!u) return;
  for (const layer of [u.marker, u.circle, u.path]) if (layer) layer.remove();
  Object.assign(u, { marker: null, circle: null, path: null, points: [] });
}

/** Remise à zéro (scripts/clear_map.py) : plus aucune unité ni aucun ennemi sur la carte. */
function clearAll() {
  units.forEach((u, name) => removeUnit(name));
  enemyLayer.clearLayers();
  enemies.forEach((e) => [e.marker, e.circle, e.path].forEach((l) => l && l.remove()));
  enemies.clear();
}

export function applyEvent(ev, { focus = false } = {}) {
  if (ev.status === "cleared") { clearAll(); return; }
  if (ev.status === "left") { removeUnit(ev.unit); return; }
  if (ev.status === "enemy_down") { killEnemy(ev); return; }
  if (ev.lat == null || ev.status !== "confirmed") { unitColor(ev.unit); return; }
  if (ev.intent?.subject === "enemy") { addEnemy(ev, focus); return; }
  unitColor(ev.unit);

  const u = units.get(ev.unit);
  const pos = [ev.lat, ev.lon];
  u.points.push(pos);

  if (!u.marker) {
    u.circle = L.circle(pos, { radius: ev.uncertainty_m || 50, color: u.color, weight: 1, dashArray: "4 4", fillOpacity: 0.12 }).addTo(map);
    u.path = L.polyline(u.points, { color: u.color, weight: 2 }).addTo(map);
    u.marker = L.circleMarker(pos, styleFor(ev.unit, u.color)).addTo(map);
    restyle(ev.unit, u);
  } else {
    u.marker.setLatLng(pos);
    u.circle.setLatLng(pos).setRadius(ev.uncertainty_m || 50);
    u.path.setLatLngs(u.points);
  }
  if (focus) map.panTo(pos);
}

export function flyTo(ev) {
  if (ev.lat != null) map.flyTo([ev.lat, ev.lon], 17);
}
