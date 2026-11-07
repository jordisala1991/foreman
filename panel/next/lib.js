// What every screen of the new panel shares: html`` (htm bound to Preact), the texts, the panel's API with its key,
// the theme and the number formats. No build step: these modules are served as written (import map in index.html).
import { h } from "preact";
import htm from "htm";

export const html = htm.bind(h);

let S = {};
const lang = () => document.documentElement.lang || "en";

// the texts of strings.<language>.json ({name} placeholders filled from `vars`); a missing key shows its name
export function t(key, vars) {
  return String(S[key] ?? key).replace(/\{(\w+)\}/g, (m, k) => (vars && k in vars ? vars[k] : m));
}

const key = () => new URLSearchParams(location.search).get("t") || "";
const withKey = (path) => path + (path.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(key());

export async function api(path) {
  const r = await fetch(withKey(path), { cache: "no-cache" });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

export async function loadStrings() {
  for (const l of [lang(), "en"]) {
    try {
      S = await api(`/next/strings.${l}.json`);
      return;
    } catch (e) {
      /* the next language */
    }
  }
}

// the panel of today, at the same key (the screens not built yet send there)
export const oldPanel = () => withKey("/");

// theme: "auto" follows the system; "light" or "dark" is the owner's choice, kept in this browser
const dark = () => globalThis.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches;
export const themeChoice = () => localStorage.getItem("next-theme") || "auto";
export function setTheme(choice) {
  if (choice === "auto") localStorage.removeItem("next-theme");
  else localStorage.setItem("next-theme", choice);
  applyTheme();
}
export function applyTheme() {
  const c = themeChoice();
  document.documentElement.dataset.theme = c === "auto" ? (dark() ? "dark" : "light") : c;
}
export const theme = () => document.documentElement.dataset.theme || "light";
export function onSystemTheme(fn) {
  if (globalThis.matchMedia) matchMedia("(prefers-color-scheme: dark)").addEventListener("change", fn);
}

// $ at list price: whole dollars from 100, one decimal below
export const usd = (x) => "$" + (Math.abs(x) >= 100 ? Math.round(x).toString() : x.toFixed(1));

const day = (iso) => new Date(iso + "T12:00:00");
export const dayLabel = (iso, opts) => new Intl.DateTimeFormat(lang(), opts).format(day(iso));
