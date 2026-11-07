// node tests/next_dom.mjs PANEL_NEXT_DIR SCENARIO.json
// Mounts the new panel (panel/next/app.js, with the import map of its index.html pointed at the files on disk) over
// a minimal DOM, with fetch answered from the scenario ({hash, answers: {path without the key: JSON}, click: text of
// a button to press once mounted}) and from the files of PANEL_NEXT_DIR for /next/…, and Chart.js replaced by a
// recorder. Prints one line of JSON: the text, the tabs and their badges, the products of the picker, the charts'
// configurations (their callbacks run once, so a broken one fails here) and the API paths fetched.
import fs from "node:fs";
import path from "node:path";
import { register } from "node:module";
import { pathToFileURL } from "node:url";

const [dir, scenarioPath] = process.argv.slice(2);
const scenario = JSON.parse(fs.readFileSync(scenarioPath, "utf8"));

class Style {
  setProperty(k, v) { this[k] = v; }
  removeProperty(k) { delete this[k]; }
}
class Node {
  constructor(type, name, ns) {
    Object.assign(this, { nodeType: type, nodeName: name, localName: name, namespaceURI: ns || null });
    Object.assign(this, { childNodes: [], parentNode: null, attributes: {}, style: new Style(), dataset: {}, _on: {} });
  }
  get ownerDocument() { return document; }
  get firstChild() { return this.childNodes[0] || null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
  get nextSibling() { const s = this.parentNode && this.parentNode.childNodes; return s ? s[s.indexOf(this) + 1] || null : null; }
  get previousSibling() { const s = this.parentNode && this.parentNode.childNodes; return s ? s[s.indexOf(this) - 1] || null : null; }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  appendChild(c) { return this.insertBefore(c, null); }
  insertBefore(c, ref) {
    if (c.parentNode) c.parentNode.removeChild(c);
    const i = ref ? this.childNodes.indexOf(ref) : -1;
    if (i < 0) this.childNodes.push(c); else this.childNodes.splice(i, 0, c);
    c.parentNode = this;
    return c;
  }
  removeChild(c) { this.childNodes.splice(this.childNodes.indexOf(c), 1); c.parentNode = null; return c; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  contains(n) { for (; n; n = n.parentNode) if (n === this) return true; return false; }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; }
  removeAttribute(k) { delete this.attributes[k]; }
  hasAttribute(k) { return k in this.attributes; }
  addEventListener(t, f) { (this._on[t] = this._on[t] || []).push(f); }
  removeEventListener(t, f) { this._on[t] = (this._on[t] || []).filter((g) => g !== f); }
  dispatch(type) {
    const e = { type, target: this, currentTarget: this, preventDefault() {}, stopPropagation() {} };
    (this._on[type] || []).forEach((f) => f.call(this, e));
  }
  get textContent() { return this.nodeType === 3 ? this.data : this.childNodes.map((c) => c.textContent).join(""); }
  set textContent(v) { this.childNodes = []; if (v !== "" && v != null) this.appendChild(document.createTextNode(String(v))); }
}
const all = (n, ok, out = []) => { if (ok(n)) out.push(n); n.childNodes.forEach((c) => all(c, ok, out)); return out; };
const words = (n) => (n.nodeType === 3 ? String(n.data) : n.childNodes.map(words).join(" ")).replace(/\s+/g, " ").trim();
const cls = (n, c) => (n.attributes.class || "").split(" ").includes(c);

const html = new Node(1, "html");
html.lang = "es";
const body = html.appendChild(new Node(1, "body"));
const document = {
  documentElement: html, body, title: "",
  createElement: (n) => new Node(1, n),
  createElementNS: (ns, n) => new Node(1, n, ns),
  createTextNode: (s) => Object.assign(new Node(3, "#text"), { data: s }),
  createComment: (s) => Object.assign(new Node(8, "#comment"), { data: s }),
};
const store = {};
const on = {};
const charts = [];
const fetched = [];
Object.assign(globalThis, {
  document, window: globalThis,
  location: { search: "?t=k3y", hash: scenario.hash || "" },
  localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  addEventListener: (t, f) => { on[t] = f; }, removeEventListener() {},
  requestAnimationFrame: (f) => setTimeout(f, 0), cancelAnimationFrame: clearTimeout,
  Chart: class { constructor(canvas, cfg) { charts.push(cfg); } destroy() {} },
  fetch: async (url) => {
    const p = String(url).replace(/[?&]t=[^&]*/, "").replace(/^([^?]*)&/, "$1?");
    let body;
    if (p.startsWith("/next/")) body = JSON.parse(fs.readFileSync(path.join(dir, p.slice(6)), "utf8"));
    else { fetched.push(p); body = scenario.answers[p]; }
    return { ok: body !== undefined, status: body === undefined ? 404 : 200, json: async () => body };
  },
});

// the import map of index.html, each {{U file}} pointed at the file on disk
const index = fs.readFileSync(path.join(dir, "index.html"), "utf8");
const raw = /<script type="importmap">([\s\S]*?)<\/script>/.exec(index)[1];
const map = JSON.parse(raw.replace(/\{\{U ([\w./-]+)\}\}/g, (m, f) => pathToFileURL(path.join(dir, f)).href)).imports;
const hooks = `let M; export async function initialize(d) { M = d; }
export async function resolve(s, c, next) { return s in M ? { url: M[s], shortCircuit: true } : next(s, c); }`;
register("data:text/javascript," + encodeURIComponent(hooks), { data: map });

const settle = () => new Promise((r) => setTimeout(r, 60));
const { mount } = await import(map["next/app.js"]);
const root = body.appendChild(new Node(1, "div"));
await mount(root);
for (let i = 0; i < 5; i++) await settle();
if (scenario.click) {
  const b = all(root, (n) => n.localName === "button" && words(n) === scenario.click)[0];
  if (!b) throw new Error("no button " + scenario.click);
  b.dispatch("click");
  for (let i = 0; i < 5; i++) await settle();
}

// run each callback of a chart's configuration once, as Chart.js would
function exercise(cfg) {
  const n = cfg.data.labels.length;
  const o = cfg.options;
  for (let i = 0; i < n; i++) {
    cfg.data.datasets.forEach((d) => typeof d.borderRadius === "function" && d.borderRadius({ dataIndex: i }));
    o.scales.x.ticks.color({ index: i });
    o.scales.x.ticks.font({ index: i });
    const items = [{ dataIndex: i, dataset: cfg.data.datasets[0], parsed: { y: cfg.data.datasets[0].data[i] } }];
    o.plugins.tooltip.callbacks.title(items);
    o.plugins.tooltip.callbacks.footer(items);
    if (items[0].parsed.y) o.plugins.tooltip.callbacks.label(items[0]);
    o.plugins.tooltip.filter(items[0]);
  }
  o.scales.y.ticks.callback(10);
}
charts.forEach(exercise);

const tab = (k) => all(root, (n) => n.localName === "a" && cls(n, "tab") && n.attributes.href === "#/" + k)[0];
const badge = (k) => { const b = all(tab(k), (n) => cls(n, "badge"))[0]; return b ? words(b) : null; };
const bar = all(root, (n) => n.localName === "a" && cls(n, "tab"));
console.log(JSON.stringify({
  text: words(root),
  tabs: bar.map(words),
  badges: bar.length ? { needs: badge("needs"), agents: badge("agents") } : {},
  products: [...new Set(all(root, (n) => n.localName === "option").map(words))],
  charts: charts.map((c) => ({
    type: c.type, labels: c.data.labels, stacked: c.options.scales.x.stacked,
    datasets: c.data.datasets.map((d) => ({ label: d.label, data: d.data })),
  })),
  fetched,
}));
