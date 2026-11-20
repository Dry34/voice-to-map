// Carte Leaflet : un marqueur, un cercle d'incertitude et un tracé par unité. Responsable : A.

const COLORS = ["#f07a45", "#7fb0de", "#6cc08f", "#e0b04e", "#c58fe0", "#e06c8f"];
const units = new Map();
let map;

export function initMap(center) {
  map = L.map("map").setView(center, 15);
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

export function applyEvent(ev, { focus = false } = {}) {
  unitColor(ev.unit);
  if (ev.lat == null || ev.status !== "confirmed") return;

  const u = units.get(ev.unit);
  const pos = [ev.lat, ev.lon];
  u.points.push(pos);

  if (!u.marker) {
    u.marker = L.circleMarker(pos, { radius: 8, color: "#fff", weight: 2, fillColor: u.color, fillOpacity: 1 })
      .bindTooltip(ev.unit, { permanent: true, direction: "right", offset: [10, 0], className: "unit-label" })
      .addTo(map);
    u.circle = L.circle(pos, { radius: ev.uncertainty_m || 50, color: u.color, weight: 1, dashArray: "4 4", fillOpacity: 0.12 }).addTo(map);
    u.path = L.polyline(u.points, { color: u.color, weight: 3 }).addTo(map);
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
