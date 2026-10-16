// The browser version's page: unlock, then show the job-search app's own pages. The pages are rendered by
// the local app's code running in a Web Worker (worker.js); here links and forms become requests to it,
// the way a browser talks to `jobsearch serve`. Pages contain untrusted text (adverts, emails), escaped
// by the templates; scripts and event-handler attributes are dropped and the CSP allows no inline script.

import * as vault from "./vault.js";

const worker = new Worker(new URL("worker.js", import.meta.url), { type: "module" });
const appStyle = document.getElementById("app-style");
let pendingAdvert = null;  // from the "Save this job" extension, kept until unlocked
let current = "/";         // the app path on screen
let unlocked = false;

// ---------------------------------------------------------------- talking to the worker

let nextId = 0;
const waiting = new Map();
worker.onmessage = (e) => {
  const m = e.data;
  if (m.id !== undefined) {
    const w = waiting.get(m.id);
    waiting.delete(m.id);
    return m.error !== undefined ? w.reject(new Error(m.error)) : w.resolve(m.result);
  }
  if (m.type === "progress") say(m.text + "…", "busy");
  else if (m.type === "saving") say("saving…");
  else if (m.type === "saved") say("saved (encrypted, in this browser)", "ok", 2500);
  else if (m.type === "save-error") say(`not saved: ${m.text}`, "err");
};
worker.onerror = (e) => say(`the app stopped: ${e.message || "error"} - reload the page`, "err");

function ask(op, ...args) {
  return new Promise((resolve, reject) => {
    const id = nextId++;
    waiting.set(id, { resolve, reject });
    worker.postMessage({ id, op, args });
  });
}

// ---------------------------------------------------------------- status line (bottom left)

let statusEl = null, statusTimer = null, statusText = "";
function say(text, cls = "", fadeAfter = 0) {
  statusText = text;
  clearTimeout(statusTimer);
  if (!statusEl || !statusEl.isConnected) {
    statusEl = document.createElement("div");
    statusEl.id = "js-status";
    document.body.append(statusEl);
  }
  statusEl.className = cls;
  statusEl.textContent = text;
  statusEl.hidden = !text;
  if (fadeAfter) statusTimer = setTimeout(() => { statusEl.hidden = true; statusText = ""; }, fadeAfter);
}
window.addEventListener("beforeunload", (e) => { if (statusText === "saving…") e.preventDefault(); });

function toast(message) {
  let box = document.querySelector(".toasts");
  if (!box) {
    box = document.createElement("div");
    box.className = "toasts";
    (document.querySelector("main") || document.body).prepend(box);
  }
  const t = document.createElement("div");
  t.className = "flash toast" + (message.startsWith("error") ? " err" : "");
  t.title = "click to dismiss";
  t.textContent = message;
  box.append(t);
}

// ---------------------------------------------------------------- version (bottom left)
// The commit this copy of the app was published from: version.json, written by the Pages workflow. A copy run
// straight from a clone has none.

let version = null;
fetch(new URL("version.json", import.meta.url), { cache: "no-store" })
  .then((r) => (r.ok ? r.json() : { commit: "" }), () => ({ commit: "" }))
  .then((v) => { version = v; showVersion(); });

function showVersion() {
  if (!version) return;
  let tag = document.getElementById("js-version");
  if (!tag) {
    const sha = /^[0-9a-f]{40}$/.test(version.commit || "") ? version.commit : "";
    const repo = /^[\w.-]+\/[\w.-]+$/.test(version.repo || "") ? version.repo : "";
    tag = document.createElement(sha && repo ? "a" : "span");
    tag.id = "js-version";
    tag.textContent = sha ? `version ${sha.slice(0, 7)}` : "version: local copy";
    if (sha && repo) {
      tag.href = `https://github.com/${repo}/commit/${sha}`;
      tag.target = "_blank";
      tag.rel = "noopener noreferrer";
      tag.title = "the commit this page was published from";
    }
  }
  if (!tag.isConnected) document.body.append(tag);
}

// ---------------------------------------------------------------- the extension's advert link

function readAdvertFromHash() {
  const m = location.hash.match(/^#add-advert=([A-Za-z0-9_-]+)$/);
  if (!m) return false;
  try {
    const b64 = m[1].replace(/-/g, "+").replace(/_/g, "/");
    const bytes = Uint8Array.from(atob(b64 + "===".slice((b64.length + 3) % 4)), (c) => c.charCodeAt(0));
    pendingAdvert = JSON.parse(new TextDecoder().decode(bytes));
  } catch (e) { pendingAdvert = null; }
  history.replaceState(null, "", location.pathname + location.search + "#/");
  return true;
}

// As the inbox page's own script does in the local app: fill "Add an advert"; you check it and add it.
function fillAdvert() {
  const box = document.getElementById("add");
  const form = box && box.querySelector("form");
  if (!pendingAdvert || !form) return;
  const advert = pendingAdvert;
  pendingAdvert = null;
  for (const k of ["url", "company", "title", "location", "posted", "text"]) {
    if (typeof advert[k] === "string" && form.elements[k]) form.elements[k].value = advert[k].slice(0, 500000);
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(form.elements.posted.value)) form.elements.posted.value = "";
  box.open = true;
  box.scrollIntoView();
  const missing = ["company", "title"].find((k) => !form.elements[k].value);
  (missing ? form.elements[missing] : form.querySelector("button.primary")).focus();
}

// ---------------------------------------------------------------- showing pages

const APP = "http://app";  // base for resolving the pages' links (they are written for the local app)
const appPath = (href) => { const u = new URL(href, APP + current); return u.pathname + u.search; };

function adopt(body) {
  for (const el of body.querySelectorAll("script, meta, link, base, object, embed")) el.remove();
  for (const el of body.querySelectorAll("*")) {
    for (const a of [...el.attributes]) {
      if (!a.name.startsWith("on")) continue;
      if (a.name === "onchange" && a.value.includes("submit()")) el.dataset.autosubmit = "";
      el.removeAttribute(a.name);
    }
  }
  for (const a of body.querySelectorAll("a[href]")) {
    const href = a.getAttribute("href");
    if (href.startsWith("#")) { a.dataset.anchor = href.slice(1); continue; }
    if (/^(https?:|mailto:)/i.test(href)) { a.target = "_blank"; a.rel = "noopener noreferrer"; continue; }
    if (/^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith("//")) { a.removeAttribute("href"); continue; }
    const path = appPath(href);
    a.dataset.app = path;
    if (a.target === "_blank") a.dataset.newtab = "";
    a.removeAttribute("target");
    a.setAttribute("href", "#" + path);
  }
  for (const f of body.querySelectorAll("form")) f.dataset.action = appPath(f.getAttribute("action") || current);
  for (const fr of body.querySelectorAll("iframe[src]")) {
    const src = fr.getAttribute("src");
    fr.removeAttribute("src");
    if (!/^[a-z][a-z0-9+.-]*:/i.test(src)) fr.dataset.src = appPath(src.split("#")[0]);
  }
}

function show(res, { keepScroll = false } = {}) {
  const doc = new DOMParser().parseFromString(res.html, "text/html");
  const y = window.scrollY;
  appStyle.textContent = [...doc.head.querySelectorAll("style")].map((s) => s.textContent).join("\n");
  document.title = doc.title || "Job search";
  current = res.path;  // before adopt: the page's relative links resolve against it
  adopt(doc.body);
  document.body.className = "";
  document.body.replaceChildren(...doc.body.childNodes);
  if (statusText) say(statusText, statusEl && statusEl.className);
  showVersion();
  for (const fr of document.querySelectorAll("iframe[data-src]")) frameFile(fr);
  const target = res.fragment && document.getElementById(res.fragment);
  if (target) target.scrollIntoView();
  else window.scrollTo(0, keepScroll ? y : 0);
  fillAdvert();
}

async function frameFile(frame) {
  try {
    const r = await ask("request", "GET", frame.dataset.src, []);
    if (r.kind === "file") frame.src = blobUrl(r) + "#view=FitH";
  } catch (e) { /* the page still works without the preview */ }
}

// Files are shown from blob: URLs, which carry this page's origin - so only types the browser displays
// inertly (PDF, images, plain text) are opened; anything else is downloaded.
const VIEWABLE = { pdf: "application/pdf", png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif" };
const TEXT = /\.(md|txt|log|yaml|yml|csv|json|eml|html?)$/i;

function blobUrl(r) {
  const ext = (r.filename.split(".").pop() || "").toLowerCase();
  const type = VIEWABLE[ext] || (TEXT.test(r.filename) ? "text/plain;charset=utf-8" : "application/octet-stream");
  const url = URL.createObjectURL(new Blob([r.body], { type }));
  setTimeout(() => URL.revokeObjectURL(url), 5 * 60000);
  return url;
}

function download(r) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([r.body], { type: "application/octet-stream" }));
  a.download = r.filename;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 60000);
}

function openFile(r, win) {
  const ext = (r.filename.split(".").pop() || "").toLowerCase();
  if (r.download || !(VIEWABLE[ext] || TEXT.test(r.filename))) {
    if (win) win.close();
    return download(r);
  }
  const url = blobUrl(r);
  if (win) win.location.href = url;
  else if (!window.open(url, "_blank")) download(r);
}

// ---------------------------------------------------------------- navigation

let busyTimer = null, inflight = 0;
async function request(method, path, fields = [], { win = null, push = true } = {}) {
  document.documentElement.dataset.busy = ++inflight;  // (for tests and styling: a request is running)
  clearTimeout(busyTimer);
  busyTimer = setTimeout(() => { if (!statusText.endsWith("…")) say("working…", "busy"); }, 400);
  const from = current;
  try {
    const r = await ask("request", method, path, fields);
    if (r.kind === "wiped") return location.replace(location.pathname);
    if (r.kind === "file") return openFile(r, win);
    if (win) win.close();
    const same = r.path === from;
    if (push && !same) history.pushState(null, "", "#" + r.path);
    else history.replaceState(null, "", "#" + r.path);
    show(r, { keepScroll: same && method === "POST" });
  } catch (e) {
    if (win) win.close();
    toast(`error: ${e.message}`);
  } finally {
    document.documentElement.dataset.busy = --inflight;
    clearTimeout(busyTimer);
    if (statusText === "working…" || (statusEl && statusEl.className === "busy")) say("");
  }
}

const hashPath = () => (location.hash.startsWith("#/") ? location.hash.slice(1) : "/");

async function lock() {
  say("saving…");
  await ask("flush");
  location.replace(location.pathname);  // a fresh page: the key and the decrypted data are gone
}

document.addEventListener("click", (e) => {
  if (e.target.closest(".toast")) return e.target.closest(".toast").remove();
  const a = e.target.closest("a");
  if (!a || !unlocked || e.defaultPrevented) return;
  if (a.dataset.anchor !== undefined) {
    const el = document.getElementById(a.dataset.anchor);
    e.preventDefault();
    if (el) { if (el.tagName === "DETAILS") el.open = true; el.scrollIntoView(); }
    return;
  }
  if (a.dataset.app === undefined || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return;
  e.preventDefault();
  if (a.dataset.app === "/browser/lock") return lock();
  const win = a.dataset.newtab !== undefined ? window.open("", "_blank") : null;  // now, while the click counts
  request("GET", a.dataset.app, [], { win });
});

document.addEventListener("submit", (e) => {
  const form = e.target;
  if (!unlocked || form.dataset.action === undefined) return;
  e.preventDefault();
  const data = new FormData(form, e.submitter);
  const action = form.dataset.action;
  if ((form.getAttribute("method") || "get").toLowerCase() === "get") {
    const q = new URLSearchParams([...data].filter(([, v]) => typeof v === "string")).toString();
    return request("GET", action.split("?")[0] + (q ? "?" + q : ""));
  }
  for (const b of form.querySelectorAll("button")) b.disabled = true;  // no double submits while it runs
  request("POST", action, [...data]);
});

document.addEventListener("change", (e) => {
  if (unlocked && e.target.matches("select[data-autosubmit]") && e.target.form) e.target.form.requestSubmit();
});

window.addEventListener("hashchange", () => {
  if (!unlocked) return;
  if (readAdvertFromHash()) return request("GET", "/");  // the extension's link, in a tab that's already open
  if (hashPath() !== current) request("GET", hashPath(), [], { push: false });
});

// ---------------------------------------------------------------- unlock and set-up screens

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) node.setAttribute(k, v === true ? "" : v);
  }
  node.append(...children.flat().filter((c) => c != null && c !== false));
  return node;
}

function screen(...nodes) {
  appStyle.textContent = "";
  document.body.className = "shell";
  document.body.replaceChildren(el("header", {}, el("b", {}, "Job search")), el("main", {}, ...nodes),
    el("footer", { class: "muted small" }, "Your data is decrypted only in this browser tab and saved encrypted in this browser. Nothing is sent anywhere."));
  if (statusText) say(statusText, statusEl && statusEl.className);
  showVersion();
}

async function enter(firstPath) {
  unlocked = true;
  say("");
  await request("GET", pendingAdvert ? "/" : firstPath, [], { push: false });
}

function unlockScreen(error = "") {
  const pass = el("input", { type: "password", autocomplete: "current-password", required: true, id: "passphrase" });
  screen(el("form", { class: "card", onsubmit: async (e) => {
    e.preventDefault();
    try {
      await ask("unlock", pass.value);
      await enter(hashPath());
    } catch (err) { say(""); unlockScreen(err.message); }
  } },
  el("h2", {}, "Unlock"),
  pendingAdvert ? el("p", {}, "Unlock to add the advert you saved: ", el("b", {}, pendingAdvert.title || "untitled")) : null,
  el("label", {}, "Passphrase", pass),
  error ? el("p", { class: "err" }, error) : null,
  el("button", { class: "primary" }, "Unlock")),
  // Without the passphrase the data can't be decrypted by anyone, so the only way on is to delete it here and
  // start again (from a bundle, or empty). Two steps, so it can't happen by a slip.
  el("details", { class: "card danger", id: "forgot" },
    el("summary", {}, "Forgot your passphrase?"),
    el("p", {}, "Your data in this browser is encrypted with it and can't be recovered without it - not by anyone. ",
      "You can delete it from this browser and set up again: import your records from your git repository, or start empty."),
    el("p", { class: "err" }, "Anything not exported or pulled into your git repository is lost."),
    el("button", { class: "danger", type: "button", id: "clear-data", onclick: async () => {
      try {
        await vault.destroy();
        setupScreen();
        say("the data stored in this browser was deleted", "ok", 4000);
      } catch (err) { say(`couldn't delete it: ${err.message}`, "err"); }
    } }, "Delete the data stored in this browser")));
  pass.focus();
}

// The empty starter records (examples/data-template), published next to the app by the Pages workflow.
const STARTER = new URL("../starter.bundle", location.href);

function setupScreen(error = "") {
  const pass = el("input", { type: "password", autocomplete: "new-password", minlength: 10, required: true, id: "new-passphrase" });
  const again = el("input", { type: "password", autocomplete: "new-password", required: true, id: "new-passphrase-again" });
  const file = el("input", { type: "file", accept: ".bundle,application/octet-stream", id: "bundle" });
  screen(el("form", { class: "card", onsubmit: async (e) => {
    e.preventDefault();
    if (pass.value !== again.value) return setupScreen("the passphrases don't match");
    const starter = e.submitter && e.submitter.value === "starter";
    if (!starter && !file.files[0]) return setupScreen("choose your git bundle, or start with empty records");
    try {
      let bytes;
      if (starter) {
        const r = await fetch(STARTER);
        if (!r.ok) throw new Error(`the starter records aren't available here (${r.status}) - they come with the published site`);
        bytes = new Uint8Array(await r.arrayBuffer());
      } else {
        bytes = new Uint8Array(await file.files[0].arrayBuffer());
      }
      await ask("setup", pass.value, bytes);
      await enter("/");
    } catch (err) { say(""); setupScreen(err.message); }
  } },
  el("h2", {}, "Set up"),
  el("label", {}, "New passphrase (at least 10 characters) - it encrypts your data in this browser; there is no recovery without it", pass),
  el("label", {}, "Passphrase again", again),
  error ? el("p", { class: "err" }, error) : null,
  el("h3", {}, "Just trying it out?"),
  el("p", {}, "Start with empty records: add adverts, your facts and settings as you go. You can export them as a git repository later."),
  el("button", { class: "primary", value: "starter", id: "start-empty" }, "Start with empty records"),
  el("h3", {}, "Already have records?"),
  el("p", {}, "Import your data repository as a git bundle. Make one with:"),
  el("pre", {}, "git -C data bundle create ~/jobsearch.bundle --all"),
  el("label", {}, "Git bundle", file),
  el("button", { value: "import", id: "import-bundle" }, "Import and encrypt")));
}

// ---------------------------------------------------------------- start

const pyBase = new URL(document.querySelector('meta[name="py-base"]').content, location.href).href;
ask("start", pyBase).then(() => { if (!unlocked) say(""); }, (e) => say(`couldn't load the app: ${e.message}`, "err"));
readAdvertFromHash();
(async () => ((await vault.exists()) ? unlockScreen() : setupScreen()))()
  .catch((e) => screen(el("p", { class: "err" }, e.message)));
