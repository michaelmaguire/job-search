// Git on the in-browser working copy: import/export as a git bundle (`git bundle create x.bundle --all`
// makes one; `git clone x.bundle` reads one back), status and commit. Uses isomorphic-git (globalThis.git, loaded by the worker).

import { DIR, fs, clear, listDir } from "./runtime.js";

const git = () => globalThis.git;
const base = () => ({ fs, dir: DIR });

const hex = (bytes) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");

// ---------------------------------------------------------------- bundle import

export function parseBundle(bytes) {
  const dec = new TextDecoder();
  let o = 0;
  const line = () => {
    const end = bytes.indexOf(10, o);
    if (end < 0) throw new Error("not a git bundle");
    const s = dec.decode(bytes.subarray(o, end));
    o = end + 1;
    return s;
  };
  const sig = line();
  if (sig !== "# v2 git bundle" && sig !== "# v3 git bundle") throw new Error("not a git bundle");
  const refs = [];
  for (let s = line(); s !== ""; s = line()) {
    if (sig.includes("v3") && s.startsWith("@")) continue;  // v3 capabilities
    if (s.startsWith("-")) throw new Error("this bundle is incremental (it needs commits it doesn't contain) - create it with --all");
    const [oid, name] = s.split(" ");
    refs.push({ oid, name });
  }
  const pack = bytes.subarray(o);
  if (dec.decode(pack.subarray(0, 4)) !== "PACK") throw new Error("the bundle has no pack data");
  return { refs, pack };
}

export async function importBundle(bytes) {
  const { refs, pack } = parseBundle(bytes);
  const heads = refs.filter((r) => r.name.startsWith("refs/heads/"));
  if (!heads.length) throw new Error("the bundle has no branches");
  const head = refs.find((r) => r.name === "HEAD");
  const branch = (head && heads.find((r) => r.oid === head.oid)) || heads.find((r) => r.name === "refs/heads/main")
    || heads.find((r) => r.name === "refs/heads/master") || heads[0];

  clear(DIR);
  await git().init({ ...base(), defaultBranch: branch.name.slice("refs/heads/".length) });
  const name = `pack-${hex(pack.subarray(pack.length - 20))}`;
  await fs.promises.writeFile(`${DIR}/.git/objects/pack/${name}.pack`, pack);
  await git().indexPack({ ...base(), filepath: `.git/objects/pack/${name}.pack` });
  for (const r of refs) {
    if (r.name.startsWith("refs/")) await git().writeRef({ ...base(), ref: r.name, value: r.oid, force: true });
  }
  await git().writeRef({ ...base(), ref: "HEAD", value: branch.name, symbolic: true, force: true });
  await git().checkout({ ...base(), ref: branch.name.slice("refs/heads/".length), force: true });
  return { branch: branch.name, refs: refs.length };
}

// ---------------------------------------------------------------- bundle export

async function allObjects(roots) {
  const seen = new Set();
  const trees = async (oid) => {
    if (seen.has(oid)) return;
    seen.add(oid);
    const { tree } = await git().readTree({ ...base(), oid });
    for (const e of tree) {
      if (e.type === "tree") await trees(e.oid);
      else if (e.type === "blob") seen.add(e.oid);  // submodule entries ("commit") aren't in this repo
    }
  };
  for (const root of roots) {
    for (const c of await git().log({ ...base(), ref: root })) {
      if (seen.has(c.oid)) continue;
      seen.add(c.oid);
      await trees(c.commit.tree);
    }
  }
  return [...seen];
}

// The pack for a bundle, fast: the pack the data was imported from, copied as it is (its deltas point at
// offsets relative to each entry, so they stay valid), plus the loose objects written since, as one pack
// with a new header and checksum. Everything the branches reach is in one of the two.
async function quickPack() {
  const objects = `${DIR}/.git/objects`;
  const packs = listDir(`${objects}/pack`).filter((n) => n.endsWith(".pack"));
  if (packs.length !== 1) throw new Error("expected one pack");  // the fallback below copes with anything
  const pack = await fs.promises.readFile(`${objects}/pack/${packs[0]}`);
  const idx = await fs.promises.readFile(`${objects}/pack/${packs[0].replace(/\.pack$/, ".idx")}`);
  const iv = new DataView(idx.buffer, idx.byteOffset, idx.byteLength);
  if (iv.getUint32(0) !== 0xff744f63 || iv.getUint32(4) !== 2) throw new Error("unexpected pack index");
  const inPack = new Set();
  const n = iv.getUint32(8 + 255 * 4);
  for (let i = 0; i < n; i++) inPack.add(hex(idx.subarray(1032 + i * 20, 1052 + i * 20)));
  const parts = [pack.subarray(12, pack.length - 20)];
  let count = new DataView(pack.buffer, pack.byteOffset).getUint32(8);
  const TYPES = { commit: 1, tree: 2, blob: 3, tag: 4 };
  for (const d of listDir(objects).filter((x) => /^[0-9a-f]{2}$/.test(x))) {
    for (const rest of listDir(`${objects}/${d}`)) {
      const oid = d + rest;
      if (inPack.has(oid)) continue;
      const { type, object } = await git().readObject({ ...base(), oid, format: "content" });
      let size = object.length;
      const head = [(TYPES[type] << 4) | (size & 15)];
      size >>= 4;
      while (size) { head[head.length - 1] |= 128; head.push(size & 127); size >>= 7; }
      const deflated = new Uint8Array(await new Response(new Blob([object]).stream().pipeThrough(new CompressionStream("deflate"))).arrayBuffer());
      parts.push(Uint8Array.from(head), deflated);
      count++;
    }
  }
  const header = new Uint8Array(12);
  header.set(new TextEncoder().encode("PACK"));
  new DataView(header.buffer).setUint32(4, 2);
  new DataView(header.buffer).setUint32(8, count);
  const body = new Uint8Array(await new Blob([header, ...parts]).arrayBuffer());
  const sum = new Uint8Array(await crypto.subtle.digest("SHA-1", body));
  return new Blob([body, sum]);
}

export async function exportBundle() {
  const branches = await git().listBranches(base());
  const refs = [];
  for (const b of branches) refs.push({ name: `refs/heads/${b}`, oid: await git().resolveRef({ ...base(), ref: b }) });
  const current = await git().currentBranch(base());
  let packfile;
  try {
    packfile = await quickPack();
  } catch (e) {
    packfile = new Blob([(await git().packObjects({ ...base(), oids: await allObjects(refs.map((r) => r.oid)) })).packfile]);
  }
  const head = refs.find((r) => r.name === `refs/heads/${current}`);
  const header = ["# v2 git bundle", ...(head ? [`${head.oid} HEAD`] : []), ...refs.map((r) => `${r.oid} ${r.name}`), "", ""].join("\n");
  return new Blob([header, packfile], { type: "application/octet-stream" });
}

// ---------------------------------------------------------------- status, commit, log

export async function changes() {
  const rows = await git().statusMatrix(base());
  return rows.filter(([, h, w, s]) => !(h === 1 && w === 1 && s === 1))
    .map(([path, h, w]) => ({ path, change: h === 0 ? "added" : w === 0 ? "deleted" : "modified" }));
}

export async function commit(message, author) {
  const list = await changes();
  if (!list.length) return null;
  for (const c of list) {
    if (c.change === "deleted") await git().remove({ ...base(), filepath: c.path });
    else await git().add({ ...base(), filepath: c.path });
  }
  return git().commit({ ...base(), message, author });
}

// As `git status --porcelain` lines, which is what jobsearch.datarepo.status() gives the pages.
export async function porcelain() {
  const code = { added: "??", modified: " M", deleted: " D" };
  return (await changes()).map((c) => `${code[c.change]} ${c.path}`);
}

export async function log(depth = 8) {
  try { return await git().log({ ...base(), depth }); } catch (e) { return []; }
}

export async function branch() {
  try { return await git().currentBranch(base()); } catch (e) { return null; }
}
