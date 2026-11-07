// node tests/next_dom.mjs PANEL_NEXT_DIR SCENARIO.json
// Mounts the built new panel (PANEL_NEXT_DIR/app.js, one ES module with React inside: no node_modules needed) over a
// minimal DOM, with fetch answered from the scenario ({hash, width, answers: {path without the key: JSON}, hang:
// [paths that never answer], click: text of a button to press once mounted, tap: a day whose chart column to tap,
// tick: how many times the page's setInterval callbacks run once mounted, fail: paths whose answers stop before the
// ticks (the connection drops), down: paths that fail from the start, heal: paths that answer again at the ticks}).
// Prints one line of JSON: the text, the tabs and their badges, the sidebar, the products of the picker, the status
// pill, each chart as drawn (columns with their segments, labels, legend, figure) and the API paths fetched.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [dir, scenarioPath] = process.argv.slice(2);
const scenario = JSON.parse(fs.readFileSync(scenarioPath, "utf8"));

class Node {
  constructor(type, name, ns) {
    Object.assign(this, { nodeType: type, nodeName: name.toUpperCase(), localName: name, tagName: name.toUpperCase() });
    Object.assign(this, { namespaceURI: ns || "http://www.w3.org/1999/xhtml", childNodes: [], parentNode: null });
    Object.assign(this, { attributes: {}, style: {}, _on: [] });
  }
  get ownerDocument() { return document; }
  get parentElement() { return this.parentNode && this.parentNode.nodeType === 1 ? this.parentNode : null; }
  get firstChild() { return this.childNodes[0] || null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
  get nextSibling() { const s = this.parentNode && this.parentNode.childNodes; return s ? s[s.indexOf(this) + 1] || null : null; }
  get previousSibling() { const s = this.parentNode && this.parentNode.childNodes; return s ? s[s.indexOf(this) - 1] || null : null; }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  get options() { return all(this, (n) => n.localName === "option"); }
  get clientWidth() { return this.attributes.class === "plot" ? scenario.width || 0 : 0; }
  get nodeValue() { return this.nodeType === 1 ? null : this.data; }
  set nodeValue(v) { this.data = String(v); }
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
  setAttributeNS(_ns, k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; }
  removeAttribute(k) { delete this.attributes[k]; }
  hasAttribute(k) { return k in this.attributes; }
  addEventListener(type, f, o) { this._on.push({ type, f, capture: o === true || !!(o && o.capture) }); }
  removeEventListener(type, f) { this._on = this._on.filter((l) => l.type !== type || l.f !== f); }
  focus() {}
  blur() {}
  // a click as the browser sends it: capture from the top down, then bubbling up from the target
  click() { this.dispatch("click"); }
  dispatch(type) {
    const path = [];
    for (let n = this; n; n = n.parentNode) path.push(n);
    let stopped = false;
    const e = {
      type, target: this, bubbles: true, cancelable: true, defaultPrevented: false, timeStamp: Date.now(),
      button: 0, detail: 1, isTrusted: true, preventDefault() { this.defaultPrevented = true; },
      stopPropagation() { stopped = true; },
    };
    const run = (n, capture) => n._on.filter((l) => l.type === type && l.capture === capture).forEach((l) => {
      e.currentTarget = n;
      l.f.call(n, e);
    });
    for (const n of [...path].reverse()) { if (stopped) return; run(n, true); }
    for (const n of path) { if (stopped) return; run(n, false); }
  }
  get textContent() { return this.nodeType === 1 ? this.childNodes.map((c) => c.textContent).join("") : this.data; }
  set textContent(v) {
    if (this.nodeType !== 1) { this.data = String(v); return; }
    this.childNodes.forEach((c) => { c.parentNode = null; });
    this.childNodes = [];
    if (v !== "" && v != null) this.appendChild(document.createTextNode(String(v)));
  }
}
const all = (n, ok, out = []) => { if (ok(n)) out.push(n); n.childNodes.forEach((c) => all(c, ok, out)); return out; };
const words = (n) => (n.nodeType === 3 ? String(n.data) : n.childNodes.map(words).join(" ")).replace(/\s+/g, " ").trim();
const cls = (n, c) => (n.attributes.class || "").split(" ").includes(c);
const one = (root, ok) => all(root, ok)[0] || null;

const html = new Node(1, "html");
html.lang = scenario.lang || "es";
const body = html.appendChild(new Node(1, "body"));
const app = body.appendChild(new Node(1, "div"));
app.attributes.id = "app";
const document = {
  nodeType: 9, documentElement: html, body, title: "", activeElement: body,
  defaultView: null,
  createElement: (n) => new Node(1, n),
  createElementNS: (ns, n) => new Node(1, n, ns),
  createTextNode: (s) => Object.assign(new Node(3, "#text"), { data: String(s) }),
  createComment: (s) => Object.assign(new Node(8, "#comment"), { data: String(s) }),
  getElementById: (id) => one(html, (n) => n.attributes.id === id),
  addEventListener() {}, removeEventListener() {},
};
const store = { ...(scenario.store || {}) };
const fetched = [];
const down = new Set(scenario.down || []); // paths that fail from the start
const timers = [];
const windows = [];
class HTMLIFrameElement {}
Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
});
Object.assign(globalThis, {
  document, window: globalThis, HTMLIFrameElement,
  location: { search: "?t=k3y", hash: scenario.hash || "" },
  addEventListener() {}, removeEventListener() {},
  setInterval: (f) => { timers.push(f); return timers.length; }, clearInterval() {},
  open: (url) => { const w = { location: { href: String(url || "") }, closed: false, close() { w.closed = true; } }; windows.push(w); return scenario.blockPopups ? null : w; },
  fetch: async (url, opts) => {
    const p = String(url).replace(/[?&]t=[^&]*/, "").replace(/^([^?]*)&/, "$1?");
    fetched.push(opts && opts.method === "POST" ? "POST " + p : p);
    if ((scenario.hang || []).includes(p)) await new Promise(() => {}); // an answer that never comes
    if (down.has(p)) return { ok: false, status: 503, json: async () => ({}) };
    if ((scenario.delay || {})[p]) await new Promise((r) => setTimeout(r, scenario.delay[p])); // a slow one
    const body = scenario.answers[(opts && opts.method === "POST" ? "POST " : "") + p];
    return { ok: body !== undefined, status: body === undefined ? 404 : 200, json: async () => body };
  },
});
document.defaultView = globalThis;

const settle = async () => { for (let i = 0; i < 20; i++) await new Promise((r) => setTimeout(r, 5)); };
const errors = [];
const origError = console.error;
console.error = (...a) => { errors.push(a.map(String).join(" ")); };
await import(pathToFileURL(path.join(dir, "app.js")).href);
await settle();
if (scenario.click) {
  const b = one(app, (n) => n.localName === "button" && words(n) === scenario.click);
  if (!b) throw new Error("no button " + scenario.click);
  b.click();
  await settle();
}
if (scenario.tick) {
  (scenario.fail || []).forEach((p) => down.add(p));
  (scenario.heal || []).forEach((p) => down.delete(p)); // the server is back at the first tick
  for (let i = 0; i < scenario.tick; i++) { timers.forEach((f) => f()); await settle(); }
}
if (scenario.pick) { // the product picker moved to another entry, then the slow answers arrive
  const sel = one(app, (n) => n.localName === "select");
  sel.value = scenario.pick;
  sel.dispatch("change");
  await new Promise((r) => setTimeout(r, scenario.wait || 300));
}
if (scenario.tap) {
  const g = one(app, (n) => n.localName === "g" && n.attributes["data-day"] === scenario.tap);
  if (!g) throw new Error("no column " + scenario.tap);
  one(g, (n) => cls(n, "hit")).click();
  await settle();
}
console.error = origError;

const chart = (c) => ({
  name: c.attributes["data-chart"],
  title: words(one(c, (n) => n.localName === "h2")),
  figure: words(one(c, (n) => cls(n, "figure"))),
  legend: all(c, (n) => n.parentNode && cls(n.parentNode, "legend")).map(words),
  ylabels: all(c, (n) => cls(n, "ylabel")).map(words),
  xlabels: (one(c, (n) => cls(n, "xlabels")) || { children: [] }).children.map(words),
  empty: words(one(c, (n) => cls(n, "empty-in-plot")) || { nodeType: 3, data: "" }),
  rules: all(c, (n) => n.localName === "li" && n.parentNode.parentNode && cls(n.parentNode.parentNode, "rules")).map(words),
  bars: all(c, (n) => cls(n, "hbar")).map((n) => n.style.width),
  tasks: all(c, (n) => n.localName === "li" && cls(n.parentNode, "hbars")).map(words),
  types: all(c, (n) => cls(n, "types")).flatMap((t) => t.children.slice(1).map(words)),
  meters: all(c, (n) => cls(n, "meter")).map((n) => ({
    kind: n.attributes["data-window"], text: words(n),
    fill: one(n, (x) => cls(x, "fill")).attributes.class, width: one(n, (x) => cls(x, "fill")).style.width,
  })),
  line: all(c, (n) => cls(n, "line") && n.localName === "polyline").map((n) => n.attributes.points),
  dots: all(c, (n) => cls(n, "dot")).map((n) => n.attributes["data-day"]),
  today: words(one(c, (n) => cls(n, "today")) || { nodeType: 3, data: "" }),
  aria: (one(c, (n) => cls(n, "plot")) || { attributes: {} }).attributes["aria-label"],
  tip: words(one(c, (n) => cls(n, "tip")) || { nodeType: 3, data: "" }),
  columns: all(c, (n) => n.localName === "g" && "data-day" in n.attributes).map((g) => ({
    day: g.attributes["data-day"],
    kind: g.attributes["data-kind"],
    top: words(one(g, (n) => n.localName === "text") || { nodeType: 3, data: "" }),
    segs: all(g, (n) => "data-key" in n.attributes).map((s) => [s.attributes["data-key"], Number(s.attributes["data-value"])]),
  })),
});
const bar = all(app, (n) => n.localName === "a" && cls(n, "tab"));
const tab = (k) => bar.find((n) => n.attributes.href === "#/" + k);
const badge = (k) => { const b = tab(k) && one(tab(k), (n) => cls(n, "badge")); return b ? words(b) : null; };
const side = one(app, (n) => cls(n, "side"));
console.log(JSON.stringify({
  text: words(app),
  tabs: bar.map(words),
  current: bar.filter((n) => n.attributes["aria-current"] === "page").map(words),
  badges: bar.length ? { needs: badge("needs"), agents: badge("agents") } : {},
  badgeColours: bar.length ? { needs: one(tab("needs"), (n) => cls(n, "badge"))?.attributes.class, agents: one(tab("agents"), (n) => cls(n, "badge"))?.attributes.class } : {},
  side: side ? { nav: all(side, (n) => cls(n, "nav")).map(words), group: words(one(side, (n) => cls(n, "group"))) } : null,
  products: [...new Set(all(app, (n) => n.localName === "option").map(words))],
  pills: all(app, (n) => cls(n, "pill")).map((n) => [n.attributes.class, words(n)]),
  buttons: all(app, (n) => n.localName === "button").map(words),
  charts: all(app, (n) => "data-chart" in n.attributes).map(chart),
  links: all(app, (n) => n.localName === "a" && n.attributes.href).map((n) => [words(n), n.attributes.href]),
  rows: all(app, (n) => cls(n, "row") || cls(n, "prow") || cls(n, "grouphead") || cls(n, "srow")).map(words),
  offline: words(one(app, (n) => cls(n, "offline")) || { nodeType: 3, data: "" }),
  dim: !!one(app, (n) => cls(n, "dim")),
  agents: all(app, (n) => n.localName === "article" && cls(n, "agent")).map((a) => ({
    cls: a.attributes.class,
    avatar: words(one(a, (n) => cls(n, "avatar"))),
    task: words(one(a, (n) => n.localName === "h3")),
    id: words(one(a, (n) => cls(n, "id")) || { nodeType: 3, data: "" }),
    meta: words(one(a, (n) => cls(n, "meta"))),
    state: words(one(a, (n) => cls(n, "state"))),
    stateTone: one(a, (n) => cls(n, "dot")).attributes.class,
    msg: words(one(a, (n) => cls(n, "msg")) || { nodeType: 3, data: "" }),
    watch: !!one(a, (n) => cls(n, "watch")),
  })),
  done: all(app, (n) => n.localName === "li" && n.parentNode && cls(n.parentNode, "done")).map(words),
  nobody: words(one(app, (n) => cls(n, "nobody")) || { nodeType: 3, data: "" }),
  windows: windows.map((w) => ({ href: w.location.href, closed: w.closed })),
  fetched,
  errors,
}));
process.exit(0); // the screens poll: their timers would keep node alive
