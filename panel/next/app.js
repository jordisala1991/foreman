// The new panel's shell: navigation (a 248 px sidebar on a wide screen, a bottom bar of five tabs on the phone),
// the product picker (only products with [panel] next = true), the light/dark theme and the screens. index.html
// calls mount(); nothing runs on import.
import { render } from "preact";
import { useEffect, useState } from "preact/hooks";
import { How } from "next/how.js";
import { api, applyTheme, html, loadStrings, oldPanel, onSystemTheme, setTheme, t, theme, themeChoice } from "next/lib.js";

const TABS = ["home", "needs", "agents", "how", "more"];
const MORE = ["backlog", "wiki", "system"];
const ICON = {
  home: "M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
  needs: "M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 0 0 3.4 0",
  agents: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8",
  how: "M4 20V10M10 20V4M16 20v-7M22 20H2",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  backlog: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  wiki: "M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5zM4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5",
  system: "M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6",
};
const Icon = ({ name }) =>
  html`<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d=${ICON[name]} /></svg>`;

const route = () => (location.hash.replace(/^#\/?/, "") || "home").split("/")[0];

function Picker({ products, product, pick }) {
  const cur = products.find((p) => p.name === product);
  return html`<label class="picker">
    <span>${cur ? cur.title : ""}</span><svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true"><path d="M2.5 4.5 6 8l3.5-3.5" fill="none" stroke="currentColor" stroke-width="1.6" /></svg>
    <select aria-label=${t("product")} value=${product} onChange=${(e) => pick(e.target.value)}>
      ${products.map((p) => html`<option value=${p.name}>${p.title}</option>`)}
    </select>
  </label>`;
}

function ThemeSwitch({ choice, choose }) {
  return html`<div class="seg theme" role="group" aria-label=${t("theme")}>
    ${["auto", "light", "dark"].map(
      (c) => html`<button aria-pressed=${choice === c} onClick=${() => choose(c)}>${t("theme_" + c)}</button>`,
    )}
  </div>`;
}

function Badge({ n, red }) {
  return n ? html`<span class=${"badge" + (red ? " red" : "")}>${n}</span>` : null;
}

const Soon = ({ tab }) => html`<div class="screen">
  <div class="screenhead"><h1>${t("tab_" + tab)}</h1></div>
  <div class="card quiet">${t("soon")} <a href=${oldPanel()}>${t("open_old")}</a></div>
</div>`;

function More({ choice, choose }) {
  return html`<div class="screen">
    <div class="screenhead"><h1>${t("tab_more")}</h1></div>
    <nav class="card list">${MORE.map((m) => html`<a href=${"#/" + m}><${Icon} name=${m} />${t("tab_" + m)}</a>`)}</nav>
    <div class="card"><h2>${t("theme")}</h2><${ThemeSwitch} choice=${choice} choose=${choose} /></div>
  </div>`;
}

function Shell({ data }) {
  const products = data.projects.filter((p) => p.next && !p.broken);
  const [tab, setTab] = useState(route());
  const [product, setProduct] = useState(() => {
    const kept = localStorage.getItem("next-product");
    return products.some((p) => p.name === kept) ? kept : products[0] && products[0].name;
  });
  const [choice, setChoice] = useState(themeChoice());
  const [look, setLook] = useState(theme());
  useEffect(() => {
    const go = () => setTab(route());
    addEventListener("hashchange", go);
    onSystemTheme(() => (applyTheme(), setLook(theme())));
    return () => removeEventListener("hashchange", go);
  }, []);
  if (!products.length) {
    return html`<main class="none card"><h1>${t("none_title")}</h1><p>${t("none_how")}</p><pre>[panel]\nnext = true</pre>
      <a href=${oldPanel()}>${t("open_old")}</a></main>`;
  }
  const p = products.find((x) => x.name === product) || products[0];
  const pick = (name) => (localStorage.setItem("next-product", name), setProduct(name));
  const choose = (c) => (setTheme(c), setChoice(c), setLook(theme()));
  const badge = { needs: (p.decisions || 0) + (p.unread || 0), agents: p.agents || 0 };
  const item = (k, cls) => html`<a class=${cls} href=${"#/" + k} aria-current=${tab === k || (k === "more" && cls === "tab" && MORE.includes(tab)) ? "page" : null}>
    <${Icon} name=${k} /><span>${t("tab_" + k)}</span><${Badge} n=${badge[k]} red=${k === "needs"} /></a>`;
  const screen =
    tab === "how" ? html`<${How} product=${p.name} theme=${look} />` : tab === "more" ? html`<${More} choice=${choice} choose=${choose} />` : html`<${Soon} tab=${TABS.includes(tab) || MORE.includes(tab) ? tab : "home"} />`;
  return html`<div class="shell">
    <aside class="side">
      <${Picker} products=${products} product=${p.name} pick=${pick} />
      <nav>${TABS.filter((k) => k !== "more").map((k) => item(k, "nav"))}</nav>
      <h3>${t("tab_more")}</h3>
      <nav>${MORE.map((k) => item(k, "nav"))}</nav>
      <div class="grow"></div>
      <${ThemeSwitch} choice=${choice} choose=${choose} />
    </aside>
    <header class="top"><${Picker} products=${products} product=${p.name} pick=${pick} /></header>
    <main>${screen}</main>
    <nav class="bar">${TABS.map((k) => item(k, "tab"))}</nav>
  </div>`;
}

export async function mount(root) {
  applyTheme();
  await loadStrings();
  document.title = t("title");
  let data;
  try {
    data = await api("/api/projects");
  } catch (e) {
    render(html`<main class="none card">${t("load_failed")}</main>`, root);
    return;
  }
  render(html`<${Shell} data=${data} />`, root);
}
