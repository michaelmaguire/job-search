// Python (Pyodide) and git (isomorphic-git) sharing one in-memory file system, in the Web Worker. The data
// repo is checked out at /data in Pyodide's file system: the jobsearch package reads and writes it as it
// would on disk, and isomorphic-git runs on the same files through a small Node-style fs adapter.

import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.mjs";

export const DIR = "/data";
const SETTINGS = "/settings";
// What the browser loads of the jobsearch package (tests/test_web_bridge.py keeps this list complete).
export const PY_FILES = ["__init__", "blocklist", "check", "conversations", "correspondence", "datarepo", "drafter",
  "efforts", "fetch", "gmail", "gmail_imap", "jobs", "paths", "render", "report", "snapshot", "sources", "web"].map((m) => `${m}.py`);
export const TEMPLATES = ["_add_company", "base", "companies", "effort_edit", "efforts", "facts", "inbox", "job", "recruiter", "recruiters",
  "report", "sources"].map((t) => `templates/${t}.html`);
export const FORMS = ["forms/716.007-i.pdf"];
const WEB_FILES = ["bridge.py", "letterdoc.py", "templates/browser_data.html", "templates/browser_edit.html"];
// From Pyodide's own repository (pinned with Pyodide), then exact versions from PyPI.
const PYODIDE_PACKAGES = ["pyyaml", "micropip", "jinja2", "markupsafe", "click", "requests", "pyodide-http", "beautifulsoup4"];
const PYPI = ["flask==3.1.3", "werkzeug==3.1.9", "itsdangerous==2.2.0", "blinker==1.9.0"];
// Loaded the first time they're needed: letter PDFs, the official URC form.
export const LATER = { fpdf2: ["fpdf2==2.8.1"], pypdf: ["pypdf==6.20.1"] };

let py, FS, bridge;
const loaded = new Set();

async function fetchTo(url, path, binary) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`can't load ${url} (${r.status})`);
  FS.mkdirTree(path.slice(0, path.lastIndexOf("/")));
  FS.writeFile(path, binary ? new Uint8Array(await r.arrayBuffer()) : await r.text());
}

export async function start(pyBase, onProgress = () => {}) {
  onProgress("loading Python");
  py = await loadPyodide();
  FS = py.FS;
  onProgress("loading Python packages");
  await py.loadPackage(PYODIDE_PACKAGES);
  const micropip = py.pyimport("micropip");
  await micropip.install(PYPI);
  onProgress("loading the app");
  FS.mkdirTree(DIR);
  FS.mkdirTree(SETTINGS);
  await Promise.all([
    ...[...PY_FILES, ...TEMPLATES].map((f) => fetchTo(new URL(f, pyBase), `/py/jobsearch/${f}`)),
    ...FORMS.map((f) => fetchTo(new URL(f, pyBase), `/py/jobsearch/${f}`, true)),
    ...WEB_FILES.map((f) => fetchTo(new URL(f, import.meta.url), `/py/${f}`)),
  ]);
  py.runPython("import sys, pyodide_http; sys.path.insert(0, '/py'); pyodide_http.patch_all()");
  bridge = py.pyimport("bridge");
  bridge.start();
}

// Install packages from LATER (e.g. "fpdf2") once, before a request that needs them.
export async function need(name) {
  if (loaded.has(name)) return;
  await py.pyimport("micropip").install(LATER[name]);
  loaded.add(name);
}

// Call a bridge function; it returns JSON. Python exceptions become JS errors with the Python message.
export function call(name, ...args) {
  try {
    const out = bridge[name](...args);
    return typeof out === "string" ? JSON.parse(out) : out;
  } catch (e) {
    const msg = String(e.message || e).trim().split("\n").filter(Boolean).pop();
    throw new Error(msg);
  }
}

// The last response body, as bytes.
export function lastBody() {
  const proxy = bridge.LAST_BODY;
  const buf = proxy.getBuffer("u8");
  try { return buf.data.slice(); } finally { buf.release(); proxy.destroy(); }
}

// ---------------------------------------------------------------- Node-style fs for isomorphic-git

function nodeError(e, path) {
  if (e && typeof e.errno === "number") {
    const code = Object.keys(py.ERRNO_CODES).find((k) => py.ERRNO_CODES[k] === e.errno) || "EIO";
    const err = new Error(`${code}: ${path}`);
    err.code = code;
    return err;
  }
  return e;
}

function stats(st) {
  return {
    // ino and ctime change whenever the vault is reopened; leaving them out keeps git's index stats valid
    mode: st.mode, size: st.size, ino: 0, uid: 1, gid: 1, dev: 1,
    mtimeMs: st.mtime.getTime(), ctimeMs: st.mtime.getTime(),
    isFile: () => FS.isFile(st.mode), isDirectory: () => FS.isDir(st.mode), isSymbolicLink: () => FS.isLink(st.mode),
  };
}

const wrap = (fn) => async (path, ...rest) => {
  try { return fn(path, ...rest); } catch (e) { throw nodeError(e, path); }
};

export const fs = {
  promises: {
    readFile: wrap((path, opts) => {
      const enc = typeof opts === "string" ? opts : opts && opts.encoding;
      return enc === "utf8" ? FS.readFile(path, { encoding: "utf8" }) : FS.readFile(path);
    }),
    writeFile: wrap((path, data) => { FS.writeFile(path, data); }),
    unlink: wrap((path) => FS.unlink(path)),
    readdir: wrap((path) => FS.readdir(path).filter((n) => n !== "." && n !== "..")),
    mkdir: wrap((path) => FS.mkdir(path)),
    rmdir: wrap((path) => FS.rmdir(path)),
    stat: wrap((path) => stats(FS.stat(path))),
    lstat: wrap((path) => stats(FS.lstat(path))),
    readlink: wrap((path) => FS.readlink(path)),
    symlink: wrap((target, path) => FS.symlink(target, path)),
    chmod: async () => {},
  },
};

// ---------------------------------------------------------------- files

export const readBytes = (path) => FS.readFile(path);
export function writeBytes(path, data) {
  FS.mkdirTree(path.slice(0, path.lastIndexOf("/")));
  FS.writeFile(path, data);
}
export const exists = (path) => { try { FS.lstat(path); return true; } catch (e) { return false; } };

export function readJSON(path, fallback) {
  try { return JSON.parse(FS.readFile(path, { encoding: "utf8" })); } catch (e) { return fallback; }
}
export const writeJSON = (path, value) => FS.writeFile(path, JSON.stringify(value));
export const settingsPath = (name) => `${SETTINGS}/${name}.json`;

export function listDir(path) {
  try { return FS.readdir(path).filter((n) => n !== "." && n !== ".."); } catch (e) { return []; }
}

export function clear(path = DIR) {
  for (const name of listDir(path)) {
    const p = `${path}/${name}`;
    const st = FS.lstat(p);
    if (FS.isDir(st.mode)) { clear(p); FS.rmdir(p); } else FS.unlink(p);
  }
}

// ---------------------------------------------------------------- the blob (what the vault encrypts)
// Format: "JSB2", then per entry: u8 type (0 file, 1 dir, 2 symlink), u32 path length, path (UTF-8),
// f64 mtime (ms), u32 data length, data. Covers /data (the repo, .git included) and /settings.
// "JSB1" (no mtime) is still read.

export function serialize() {
  const parts = [new TextEncoder().encode("JSB2")];
  const enc = new TextEncoder();
  const add = (type, path, mtime, data = new Uint8Array(0)) => {
    const p = enc.encode(path);
    const head = new Uint8Array(17 + p.length);
    const view = new DataView(head.buffer);
    head[0] = type;
    view.setUint32(1, p.length);
    head.set(p, 5);
    view.setFloat64(5 + p.length, mtime);
    view.setUint32(13 + p.length, data.length);
    parts.push(head, data);
  };
  const walk = (dir) => {
    for (const name of listDir(dir)) {
      const p = `${dir}/${name}`;
      const st = FS.lstat(p);
      const mtime = st.mtime.getTime();
      if (FS.isDir(st.mode)) { add(1, p, mtime); walk(p); } else if (FS.isLink(st.mode)) add(2, p, mtime, enc.encode(FS.readlink(p)));
      else add(0, p, mtime, FS.readFile(p));
    }
  };
  walk(DIR);
  walk(SETTINGS);
  const total = parts.reduce((n, b) => n + b.length, 0);
  const out = new Uint8Array(total);
  let o = 0;
  for (const b of parts) { out.set(b, o); o += b.length; }
  return out;
}

export function deserialize(bytes) {
  const dec = new TextDecoder();
  const magic = dec.decode(bytes.subarray(0, 4));
  if (magic !== "JSB1" && magic !== "JSB2") throw new Error("not a job-search vault");
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  clear(DIR);
  clear(SETTINGS);
  const times = [];
  let o = 4;
  while (o < bytes.length) {
    const type = bytes[o]; o += 1;
    const plen = view.getUint32(o); o += 4;
    const path = dec.decode(bytes.subarray(o, o + plen)); o += plen;
    let mtime = null;
    if (magic === "JSB2") { mtime = view.getFloat64(o); o += 8; }
    const dlen = view.getUint32(o); o += 4;
    const data = bytes.subarray(o, o + dlen); o += dlen;
    if (!path.startsWith(`${DIR}/`) && !path.startsWith(`${SETTINGS}/`)) continue;
    if (type === 1) FS.mkdirTree(path);
    else if (type === 2) FS.symlink(dec.decode(data), path);
    else FS.writeFile(path, data);
    if (mtime !== null && type !== 2) times.push([path, mtime]);
  }
  for (const [path, mtime] of times.reverse()) FS.utime(path, mtime, mtime);  // children before their folders
}
