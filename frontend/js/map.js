// Carte Leaflet : un marqueur, un cercle d'incertitude et un tracé par unité. Responsable : A.

const COLORS = ["#4c9aff", "#3fb950", "#a371f7", "#e3b341", "#39c5cf", "#f778ba",
  "#9be564", "#79c0ff", "#c297ff", "#2ea0a0", "#db61a2", "#d4d4d4"];
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
  // maxZoom exigé par le regroupement des ennemis ; zoom en bas à droite (l'escouade est en haut à gauche).
  map = L.map("map", { maxZoom: 19, zoomControl: false }).setView(center, 15);
  L.control.zoom({ position: "bottomright" }).addTo(map);
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

const assigned = new Map(); // couleurs données par le serveur à chaque unité réservée

function fallbackColor(unit) {
  // Unité qui n'est plus réservée (historique) : couleur stable tirée de son nom.
  let h = 0;
  for (const c of unit) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return COLORS[h % COLORS.length];
}

export function unitColor(unit) {
  if (!units.has(unit)) {
    units.set(unit, { color: assigned.get(unit) || fallbackColor(unit), marker: null, circle: null, path: null, points: [] });
  }
  return units.get(unit).color;
}

/** Couleurs du serveur [{unit, color}] : chaque personne a la sienne, la même sur tous les écrans. */
export function setUnitColors(list) {
  for (const { unit, color } of list) {
    if (!color) continue;
    assigned.set(unit, color);
    const u = units.get(unit);
    if (!u || u.color === color) continue;
    u.color = color;
    if (u.circle) u.circle.setStyle({ color });
    if (u.path) u.path.setStyle({ color });
    restyle(unit, u);
  }
}

// --- Itinéraires : un déplacement suit les rues (intent.route) au lieu d'un trait droit ---

function routeOf(ev) {
  const r = ev.intent?.route;
  return Array.isArray(r) && r.length >= 2 ? r : null;
}

function meters(a, b) {
  const k = Math.cos((a[0] * Math.PI) / 180);
  return Math.hypot((b[0] - a[0]) * 111_320, (b[1] - a[1]) * 111_320 * k);
}

/** Point situé à `dist` mètres du départ le long de l'itinéraire. */
function along(route, dist) {
  for (let i = 1; i < route.length; i++) {
    const seg = meters(route[i - 1], route[i]);
    if (dist <= seg) {
      const t = seg ? dist / seg : 0;
      return [route[i - 1][0] + t * (route[i][0] - route[i - 1][0]), route[i - 1][1] + t * (route[i][1] - route[i - 1][1])];
    }
    dist -= seg;
  }
  return route[route.length - 1];
}

const routeLength = (route) => route.slice(1).reduce((sum, p, i) => sum + meters(route[i], p), 0);

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
    finishAnim(e);
    e.ev = ev;
    const route = routeOf(ev);
    if (e.circle) e.circle.setLatLng(pos).setRadius(ev.uncertainty_m || 60);
    if (ev.intent?.sim && route) {
      // Étape simulée : on ne connaît pas la destination d'un ennemi. La carte ne montre que le
      // chemin déjà parcouru, qui s'allonge derrière le losange (même position sur tous les écrans).
      e.anim = { route, total: routeLength(route), start: new Date(ev.ts).getTime(), speed: ev.intent.speed_mps || 2 };
      stepAnim(e);
    } else {
      e.points.push(...(route ? route.slice(1) : [pos]));
      e.path.setLatLngs(e.points);
      moveEnemyMarker(e, pos);
    }
  }
  refreshEnemy(id);
  if (focus && !ev.intent?.sim) map.panTo(pos);
}

function animPosition(a) {
  const dist = ((Date.now() - a.start) / 1000) * a.speed;
  return dist >= a.total ? a.route[a.route.length - 1] : along(a.route, Math.max(0, dist));
}

/** Morceau de l'itinéraire déjà parcouru (jusqu'à la position actuelle). */
function travelled(a) {
  let left = Math.max(0, ((Date.now() - a.start) / 1000) * a.speed);
  const out = [a.route[0]];
  for (let i = 1; i < a.route.length; i++) {
    const seg = meters(a.route[i - 1], a.route[i]);
    if (left < seg) { out.push(along(a.route.slice(i - 1, i + 1), left)); return out; }
    left -= seg;
    out.push(a.route[i]);
  }
  return out;
}

const TRAIL_MAX = 300; // on garde la fin de la trace, pas tout l'historique

function stepAnim(e) {
  const done = travelled(e.anim);
  e.path.setLatLngs(e.points.concat(done.slice(1)).slice(-TRAIL_MAX));
  moveEnemyMarker(e, done[done.length - 1]);
  if (Date.now() - e.anim.start >= (e.anim.total / e.anim.speed) * 1000) finishAnim(e);
}

/** Étape terminée (ou interrompue) : le chemin parcouru devient de la trace. */
function finishAnim(e) {
  if (!e.anim) return;
  const done = travelled(e.anim);
  e.points = e.points.concat(done.slice(1)).slice(-TRAIL_MAX);
  e.path.setLatLngs(e.points);
  moveEnemyMarker(e, done[done.length - 1]);
  e.anim = null;
}

function moveEnemyMarker(e, pos) {
  // Le regroupement ne suit pas un marqueur déplacé : on le retire puis on le remet.
  const clustered = !e.down;
  if (clustered) enemyLayer.removeLayer(e.marker);
  e.marker.setLatLng(pos);
  if (clustered) enemyLayer.addLayer(e.marker);
}

setInterval(() => enemies.forEach((e) => { if (e.anim && !e.down) stepAnim(e); }), 1000);

function killEnemy(ev) {
  const id = ev.intent?.enemy_id;
  const e = enemies.get(id);
  if (!e) return;
  finishAnim(e); // neutralisé en route : il s'arrête là où il est
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
  const route = routeOf(ev);
  u.points.push(...(route && u.points.length ? route.slice(1) : [pos]));

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

/** Encart en haut à gauche de la carte (escouade). Renvoie l'élément à remplir. */
export function squadBox() {
  const box = L.DomUtil.create("div", "squad-box");
  box.hidden = true;
  L.DomEvent.disableClickPropagation(box);
  const ctl = L.control({ position: "topleft" });
  ctl.onAdd = () => box;
  ctl.addTo(map);
  return box;
}
