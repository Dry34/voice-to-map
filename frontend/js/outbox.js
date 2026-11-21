// Boîte d'envoi hors ligne. Responsable : A.
//
// Réseau coupé : l'annonce (texte ou audio) est signée tout de suite, avec l'heure du moment,
// et gardée ici (IndexedDB, survit à un rechargement de la page). Au retour du réseau, elle
// part dans l'ordre ; le serveur l'enregistre avec son heure d'origine, marquée « en différé ».

const DB = "voice-to-map-outbox";
const STORE = "items";

function db() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: "id", autoIncrement: true });
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

export const add = (item) => tx("readwrite", (s) => s.add({ ...item, savedAt: Date.now() }));
export const all = () => tx("readonly", (s) => s.getAll());
export const remove = (id) => tx("readwrite", (s) => s.delete(id));
export const count = () => tx("readonly", (s) => s.count());
