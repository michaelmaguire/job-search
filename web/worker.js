// The browser version's "server", in a Web Worker so long jobs (fetching adverts, packing a bundle) never
// freeze the page. It holds the decrypted data and the key, runs the jobsearch web app in Pyodide
// (bridge.py), does git with isomorphic-git, and saves everything encrypted after each change.
// The page (app.js) only sends requests - like a browser talking to `jobsearch serve` - and shows the HTML.

import * as rt from "./runtime.js";
import * as vault from "./vault.js";
import * as gitops from "./gitops.js";

const GIT = "https://cdn.jsdelivr.net/npm/isomorphic-git@1.27.1/index.umd.min.js";  // sets self.git

let ready = null;        // resolves when Python and git are loaded
let session = null;      // { key, salt } once unlocked
let repoStale = true;    // git status must be recomputed before the next page
const saved = { at: null, size: 0 };

const post = (msg, transfer = []) => self.postMessage(msg, transfer);
const progress = (text) => post({ type: "progress", text });

// ---------------------------------------------------------------- saving

let saveTimer = null;
let saving = Promise.resolve();
function scheduleSave() {
  clearTimeout(saveTimer);
  post({ type: "saving" });
  saveTimer = setTimeout(() => { saveTimer = null; saving = save(); }, 300);
}
async function save() {
  try {
    const blob = rt.serialize();
    await vault.save(session, blob);
    saved.at = new Date();
    saved.size = blob.length;
    post({ type: "saved" });
  } catch (e) {
    post({ type: "save-error", text: e.message });
  }
}
async function flush() {
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; saving = save(); }
  await saving;
}

// ---------------------------------------------------------------- requests

const flash = (message) => rt.call("flash_later", message);
const fieldsOf = (fields) => Object.fromEntries(fields.filter(([, v]) => typeof v === "string"));

async function refreshRepo() {
  if (!repoStale) return;
  const [changes, branch] = await Promise.all([gitops.porcelain(), gitops.branch()]);
  rt.call("set_repo", JSON.stringify({ ok: true, changes, branch, ahead: 0 }));
  repoStale = false;
}

async function page(method, path, fields = []) {
  if (/^\/job\/[^/?]+\/render$/.test(path)) { progress("loading the PDF tools (first time only)"); await rt.need("fpdf2"); }
  if (/^\/report\?(.*&)?format=pdf/.test(path)) { progress("loading the PDF tools (first time only)"); await rt.need("pypdf"); }
  if (method === "POST" && path === "/fetch") progress("fetching new adverts - this takes a minute or two");
  await refreshRepo();
  if (path.startsWith("/browser/data")) {
    const log = (await gitops.log(10)).map((c) => ({
      oid: c.oid, message: c.commit.message.split("\n")[0], author: c.commit.author.name,
      date: new Date(c.commit.author.timestamp * 1000).toISOString().slice(0, 16).replace("T", " "),
    }));
    rt.call("set_info", JSON.stringify({ log, saved: saved.at && saved.at.toLocaleString(),
      size: saved.size && `${(saved.size / 1e6).toFixed(1)} MB` }));
  }
  const r = rt.call("handle", method, path, JSON.stringify(fields.filter(([, v]) => typeof v === "string")));
  if (method === "POST" || /[?&]format=pdf/.test(path)) { repoStale = true; scheduleSave(); }
  if (r.text !== undefined) return { kind: "html", html: r.text, path: r.path, fragment: r.fragment, status: r.status };
  return { kind: "file", body: rt.lastBody(), type: r.type, filename: r.filename, download: r.download, status: r.status };
}

function checkCsrf(fields) {
  if (fieldsOf(fields).csrf !== rt.call("csrf")) throw new Error("stale form - reload the page");
}

// Actions that need git or uploaded files, which the Python side can't do on its own.
const ACTIONS = {
  "POST /data/commit": async (fields) => {
    checkCsrf(fields);
    const f = fieldsOf(fields);
    const author = rt.readJSON("/settings/author.json", null);
    if (!author || !author.email) {
      flash("error: set your name and email for commits first");
      return page("GET", "/browser/data");
    }
    const n = (await gitops.changes()).length;
    const message = (f.message || "").trim() || "Update from the browser";
    const oid = await gitops.commit(message, author);
    flash(oid ? `committed ${n} file(s): ${message}` : "nothing to commit - everything is committed");
    repoStale = true;
    scheduleSave();
    return page("GET", f.next || "/");
  },
  "GET /browser/export": async () => {
    progress("packing the bundle");
    const blob = await gitops.exportBundle();
    return { kind: "file", body: new Uint8Array(await blob.arrayBuffer()), type: "application/octet-stream",
      filename: "jobsearch.bundle", download: true };
  },
  "POST /browser/import": async (fields) => {
    checkCsrf(fields);
    const file = fields.find(([k, v]) => k === "bundle" && typeof v !== "string");
    if (!file) throw new Error("choose a bundle file");
    progress("importing");
    const r = await gitops.importBundle(new Uint8Array(await file[1].arrayBuffer()));
    flash(`imported ${r.branch.replace("refs/heads/", "")} - this browser now has that bundle's data`);
    repoStale = true;
    scheduleSave();
    return page("GET", "/browser/data");
  },
  "POST /browser/upload": async (fields) => {
    checkCsrf(fields);
    const f = fieldsOf(fields);
    const file = fields.find(([k, v]) => k === "file" && typeof v !== "string");
    const name = file && file[1].name.split(/[\\/]/).pop();
    if (!["raw", "profile", "search"].includes(f.folder) || !name || name.startsWith(".")) throw new Error("choose a file and a folder");
    rt.writeBytes(`${rt.DIR}/${f.folder}/${name}`, new Uint8Array(await file[1].arrayBuffer()));
    flash(`added ${f.folder}/${name}`);
    repoStale = true;
    scheduleSave();
    return page("GET", "/browser/data");
  },
  "POST /browser/wipe": async (fields) => {
    checkCsrf(fields);
    clearTimeout(saveTimer);
    saveTimer = null;
    await saving;
    await vault.destroy();
    session = null;
    rt.clear();
    return { kind: "wiped" };
  },
};

// ---------------------------------------------------------------- operations the page can ask for

const ops = {
  async start(pyBase) {
    ready = (async () => {
      progress("loading git");
      await import(GIT);
      await rt.start(pyBase, progress);
    })();
    await ready;
    return true;
  },
  async setup(passphrase, bundle) {
    await ready;
    progress("importing");
    const r = await gitops.importBundle(bundle);
    session = await vault.create(passphrase);
    progress("encrypting");
    await save();
    repoStale = true;
    return { branch: r.branch };
  },
  async unlock(passphrase) {
    await ready;
    progress("unlocking");
    const opened = await vault.open(passphrase);
    rt.deserialize(opened.plaintext);
    session = { key: opened.key, salt: opened.salt };
    repoStale = true;
    return true;
  },
  async request(method, path, fields = []) {
    if (!session) throw new Error("locked");
    const action = ACTIONS[`${method} ${path.split("?")[0]}`];
    return action ? action(fields) : page(method, path, fields);
  },
  flush,
};

let queue = Promise.resolve();  // one operation at a time: Python and git share the file system
self.onmessage = (e) => {
  const { id, op, args } = e.data;
  queue = queue.then(() => ops[op](...(args || []))).then(
    (result) => post({ id, result }, result && result.body instanceof Uint8Array ? [result.body.buffer] : []),
    (err) => post({ id, error: String((err && err.message) || err) }));
};
