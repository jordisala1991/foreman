// The shell of the new panel, as in the approved mockups: on a wide screen a 248 px sidebar (the product picker, the
// four sections with their badges, the «More» group); on the phone the picker and the product's status pill on top
// and a bottom bar of five tabs. The theme follows the system (app.css); there is no switch.
import { useEffect, useState } from "react";
import { Agents } from "./Agents";
import { How } from "./How";
import { ScreenHead, StatusPill, productState } from "./head";
import { Chevron, Icon } from "./icons";
import { oldPanel, t, type Key, type Product } from "./lib";

const SECTIONS = ["home", "needs", "agents", "how"] as const;
const MORE = ["backlog", "wiki", "system"] as const;
const TABS = [...SECTIONS, "more"] as const;
type Tab = (typeof TABS)[number] | (typeof MORE)[number];

const isTab = (s: string): s is Tab => (TABS as readonly string[]).includes(s) || (MORE as readonly string[]).includes(s);
const route = (): Tab => {
  const r = location.hash.replace(/^#\/?/, "").split("/")[0] ?? "";
  return isTab(r) ? r : "home";
};

function Picker({ products, product, pick }: { products: Product[]; product: Product; pick: (name: string) => void }) {
  return (
    <label className="picker">
      <span className="name">{product.title}</span>
      <Chevron />
      <select aria-label={t("product")} value={product.name} onChange={(e) => pick(e.target.value)}>
        {products.map((p) => (
          <option key={p.name} value={p.name}>
            {p.title}
          </option>
        ))}
      </select>
    </label>
  );
}

const Badge = ({ n, red }: { n: number; red: boolean }) => (n ? <span className={red ? "badge red" : "badge"}>{n}</span> : null);

function Soon({ tab, product }: { tab: Tab; product: Product }) {
  return (
    <div className="screen">
      <ScreenHead title={t(`tab_${tab}` as Key)} product={product} />
      <div className="card quiet">
        {t("soon")} <a href={oldPanel()}>{t("open_old")}</a>
      </div>
    </div>
  );
}

function More({ product }: { product: Product }) {
  return (
    <div className="screen">
      <ScreenHead title={t("tab_more")} product={product} />
      <nav className="card list" aria-label={t("tab_more")}>
        {MORE.map((m) => (
          <a key={m} href={`#/${m}`}>
            {t(`tab_${m}`)}
            <span className="go" aria-hidden="true">›</span>
          </a>
        ))}
      </nav>
    </div>
  );
}

export function None() {
  return (
    <main className="none card">
      <h1>{t("none_title")}</h1>
      <p>{t("none_how")}</p>
      <pre>{"[panel]\nnext = true"}</pre>
      <a href={oldPanel()}>{t("open_old")}</a>
    </main>
  );
}

const KEPT = "next-product";
const ALL = "*"; // the picker's «All», only on the Agents screen
const KEPT_ALL = "next-all";

// the «All» entry: the sums of the products, and a pill that counts those with an orchestrator running
function everyone(products: Product[]): Product {
  const sum = (k: "decisions" | "unread" | "agents") => products.reduce((n, x) => n + (x[k] ?? 0), 0);
  const running = products.filter((x) => productState(x) === "running").length;
  const pill = running === 1 ? t("pill_all_1") : t("pill_all", { n: running });
  return {
    name: ALL,
    title: t("all"),
    next: true,
    decisions: sum("decisions"),
    unread: sum("unread"),
    agents: sum("agents"),
    ...(running ? { orchestrator_health: { verdict: "ok" }, pill } : {}),
  };
}

export function Shell({ all }: { all: Product[] }) {
  const products = all.filter((p) => p.next && !p.broken);
  const [tab, setTab] = useState<Tab>(route);
  const [name, setName] = useState<string | undefined>(() => {
    const kept = localStorage.getItem(KEPT);
    return products.some((p) => p.name === kept) ? (kept ?? undefined) : products[0]?.name;
  });
  useEffect(() => {
    const go = () => setTab(route());
    addEventListener("hashchange", go);
    return () => removeEventListener("hashchange", go);
  }, []);
  const [wide, setWide] = useState(() => localStorage.getItem(KEPT_ALL) === "1"); // «All» chosen (it counts only on the Agents screen)
  const real = products.find((x) => x.name === name) ?? products[0];
  if (!real) return <None />;
  const several = products.length > 1;
  const joint = several && wide && tab === "agents";
  const p = joint ? everyone(products) : real;
  const choices = several && tab === "agents" ? [everyone(products), ...products] : products;
  const pick = (n: string) => {
    setWide(n === ALL);
    localStorage.setItem(KEPT_ALL, n === ALL ? "1" : "0");
    if (n === ALL) return;
    localStorage.setItem(KEPT, n);
    setName(n);
  };
  const badge: Partial<Record<Tab, number>> = { needs: (p.decisions ?? 0) + (p.unread ?? 0), agents: p.agents ?? 0 };
  const current = (k: Tab, bar: boolean) =>
    tab === k || (bar && k === "more" && (MORE as readonly string[]).includes(tab)) ? "page" : undefined;
  const screen =
    tab === "how" ? (
      <How product={p} />
    ) : tab === "agents" ? (
      <Agents product={p} titles={Object.fromEntries(products.map((x) => [x.name, x.title]))} />
    ) : tab === "more" ? (
      <More product={p} />
    ) : (
      <Soon tab={tab} product={p} />
    );
  return (
    <div className="shell">
      <aside className="side">
        <Picker products={choices} product={p} pick={pick} />
        <nav aria-label={t("sections")} className="navs">
          {SECTIONS.map((k) => (
            <a key={k} className="nav" href={`#/${k}`} aria-current={current(k, false)}>
              <Icon name={k} size={20} />
              <span className="label">{t(`tab_${k}`)}</span>
              <Badge n={badge[k] ?? 0} red={k === "needs"} />
            </a>
          ))}
        </nav>
        <div className="navs">
          <div className="group">{t("tab_more")}</div>
          {MORE.map((k) => (
            <a key={k} className="nav" href={`#/${k}`} aria-current={current(k, false)}>
              {t(`tab_${k}`)}
            </a>
          ))}
        </div>
      </aside>
      <main>
        <div className="top">
          <Picker products={choices} product={p} pick={pick} />
          <StatusPill product={p} />
        </div>
        {screen}
      </main>
      <nav className="bar" aria-label={t("sections")}>
        {TABS.map((k) => (
          <a key={k} className="tab" href={`#/${k}`} aria-current={current(k, true)}>
            <span className="icon">
              <Icon name={k} />
              <Badge n={badge[k] ?? 0} red={k === "needs"} />
            </span>
            {t(`tab_${k}`)}
          </a>
        ))}
      </nav>
    </div>
  );
}
