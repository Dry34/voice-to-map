// Identité du terminal : une unité + une paire de clés Ed25519. Responsable : A.
//
// La clé privée est créée par le navigateur, non exportable, et gardée dans IndexedDB :
// elle ne quitte jamais ce navigateur. Le serveur ne connaît que la clé publique.
// Chaque onglet retient son unité (sessionStorage), on peut donc tester deux unités
// dans deux onglets du même navigateur.

const DB = "voice-to-map";
const STORE = "identities";
const TAB_KEY = "vtm-unit";
const enc = new TextEncoder();

const b64 = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf)));
const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");

function db() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: "unit" });
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function tx(mode, fn) {
  const d = await db();
  return new Promise((resolve, reject) => {
    const t = d.transaction(STORE, mode);
    const req = fn(t.objectStore(STORE));
    t.oncomplete = () => resolve(req?.result);
    t.onerror = () => reject(t.error);
  });
}

const tabUnit = {
  get: () => { try { return sessionStorage.getItem(TAB_KEY); } catch { return null; } },
  set: (u) => { try { u ? sessionStorage.setItem(TAB_KEY, u) : sessionStorage.removeItem(TAB_KEY); } catch {} },
};

export async function supported() {
  try {
    await crypto.subtle.generateKey({ name: "Ed25519" }, false, ["sign", "verify"]);
    return true;
  } catch {
    return false;
  }
}

/** Signe une action pour une unité : renvoie {unit, ts, nonce, signature}. */
async function signWith(identity, action, content) {
  const bytes = typeof content === "string" ? enc.encode(content) : new Uint8Array(content);
  const digest = hex(await crypto.subtle.digest("SHA-256", bytes));
  const ts = Date.now();
  const nonce = hex(crypto.getRandomValues(new Uint8Array(16)));
  const message = `voice-to-map|v1|${action}|${identity.unit}|${ts}|${nonce}|${digest}`;
  const sig = await crypto.subtle.sign({ name: "Ed25519" }, identity.privateKey, enc.encode(message));
  return { unit: identity.unit, ts, nonce, signature: b64(sig) };
}

async function postJson(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
  return data;
}

/** Identité de cet onglet, ou null si l'onglet n'a pas encore d'unité. */
export async function current() {
  const unit = tabUnit.get();
  if (!unit) return null;
  return (await tx("readonly", (s) => s.get(unit))) || null;
}

/** Réserve une unité pour ce terminal (réutilise la clé si on l'a déjà tenue). */
export async function claim(rawUnit) {
  const wanted = rawUnit.trim().toUpperCase();
  let identity = await tx("readonly", (s) => s.get(wanted));
  if (!identity) {
    const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, false, ["sign", "verify"]);
    const publicKey = b64(await crypto.subtle.exportKey("raw", pair.publicKey));
    identity = { unit: wanted, privateKey: pair.privateKey, publicKey };
  }
  const signed = await signWith(identity, "claim", identity.publicKey);
  const { unit } = await postJson("/api/units/claim", { ...signed, public_key: identity.publicKey });
  // Le serveur normalise le nom (« alpha1 » -> « ALPHA 1 ») : on range la clé sous ce nom-là.
  identity = { ...identity, unit };
  await tx("readwrite", (s) => s.put(identity));
  tabUnit.set(unit);
  return identity;
}

/** Libère l'unité côté serveur et oublie la clé. */
export async function release(identity) {
  await postJson("/api/units/release", await signWith(identity, "release", ""));
  await tx("readwrite", (s) => s.delete(identity.unit));
  tabUnit.set(null);
}

/** Quitte l'unité dans cet onglet sans la libérer (on pourra la reprendre avec la même clé). */
export function leave() {
  tabUnit.set(null);
}

export async function sendText(identity, text) {
  return postJson("/api/reports", { ...(await signWith(identity, "report", text)), text });
}

export async function sendAudio(identity, blob, filename) {
  const signed = await signWith(identity, "audio", await blob.arrayBuffer());
  const form = new FormData();
  for (const [k, v] of Object.entries(signed)) form.append(k, String(v));
  form.append("audio", blob, filename);
  const r = await fetch("/api/reports/audio", { method: "POST", body: form });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
  return data;
}

export async function killEnemy(identity, enemyId) {
  return postJson("/api/enemies/kill", { ...(await signWith(identity, "kill", enemyId)), enemy_id: enemyId });
}

export async function leaveGroup(identity) {
  return postJson("/api/groups/leave", await signWith(identity, "group_leave", ""));
}

export async function confirmReport(identity, seq) {
  return postJson("/api/reports/confirm", { ...(await signWith(identity, "confirm", String(seq))), seq });
}
