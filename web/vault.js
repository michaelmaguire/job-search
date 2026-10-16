// The encrypted blob: the whole working copy (including .git) serialized, encrypted with AES-GCM under a
// key derived from your passphrase (PBKDF2-SHA256), and kept in this browser's IndexedDB. The key is
// non-extractable and lives only in memory while the app is unlocked; nothing leaves the device.

const DB = "jobsearch", STORE = "vault", KEY = "main", ITERATIONS = 600000;

function db() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function tx(mode, fn) {
  const d = await db();
  return new Promise((resolve, reject) => {
    const t = d.transaction(STORE, mode);
    const req = fn(t.objectStore(STORE));
    t.oncomplete = () => resolve(req && req.result);
    t.onerror = () => reject(t.error);
  });
}

export const exists = async () => !!(await tx("readonly", (s) => s.getKey(KEY)));
export const destroy = () => tx("readwrite", (s) => s.delete(KEY));

async function derive(passphrase, salt) {
  const material = await crypto.subtle.importKey("raw", new TextEncoder().encode(passphrase), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey({ name: "PBKDF2", salt, iterations: ITERATIONS, hash: "SHA-256" },
    material, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
}

// A new vault: returns the key for later saves.
export async function create(passphrase) {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  return { key: await derive(passphrase, salt), salt };
}

// Open the existing vault: returns { key, salt, plaintext } or throws "wrong passphrase".
export async function open(passphrase) {
  const rec = await tx("readonly", (s) => s.get(KEY));
  if (!rec) throw new Error("no saved data in this browser");
  const key = await derive(passphrase, rec.salt);
  try {
    const plaintext = new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv: rec.iv }, key, rec.data));
    return { key, salt: rec.salt, plaintext };
  } catch (e) {
    throw new Error("wrong passphrase");
  }
}

export async function save(vault, plaintext) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const data = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, vault.key, plaintext);
  await tx("readwrite", (s) => s.put({ v: 1, salt: vault.salt, iv, data, saved: new Date().toISOString() }, KEY));
}
