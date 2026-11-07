// The shell of the new panel, as in the approved mockups: on a wide screen a 248 px sidebar (the product picker, the
// four sections with their badges, the «More» group); on the phone the picker and the product's status pill on top
// and a bottom bar of five tabs. The theme follows the system (app.css); there is no switch.
import { useEffect, useState } from "react";
import { How } from "./How";
import { Backlog } from "./more/Backlog";
import { System } from "./more/System";
import { Wiki } from "./more/Wiki";
import type { MoreProduct } from "./more/types";
import { ScreenHead, StatusPill } from "./head";
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

export function Shell({ all }: { all: Product[] }) {
  const products = all.filter((p) => p.next && !p.broken);
  const [tab, setTab] = useState<Tab>(route);
  const [, setHash] = useState(location.hash); // a screen's own route (#/backlog/AB-1) re-renders it
  const [name, setName] = useState<string | undefined>(() => {
    const kept = localStorage.getItem(KEPT);
    return products.some((p) => p.name === kept) ? (kept ?? undefined) : products[0]?.name;
  });
  useEffect(() => {
    const go = () => {
      setTab(route());
      setHash(location.hash);
      window.scrollTo(0, 0);
    };
    addEventListener("hashchange", go);
    return () => removeEventListener("hashchange", go);
  }, []);
  const p = products.find((x) => x.name === name) ?? products[0];
  if (!p) return <None />;
  const pick = (n: string) => {
    localStorage.setItem(KEPT, n);
    setName(n);
  };
  const badge: Partial<Record<Tab, number>> = { needs: (p.decisions ?? 0) + (p.unread ?? 0), agents: p.agents ?? 0 };
  const current = (k: Tab, bar: boolean) =>
    tab === k || (bar && k === "more" && (MORE as readonly string[]).includes(tab)) ? "page" : undefined;
  const screen =
    tab === "how" ? (
      <How product={p} />
    ) : tab === "backlog" ? (
      <Backlog product={p as MoreProduct} />
    ) : tab === "wiki" ? (
      <Wiki product={p as MoreProduct} products={products as MoreProduct[]} pick={pick} />
    ) : tab === "system" ? (
      <System product={p as MoreProduct} />
    ) : tab === "more" ? (
      <More product={p} />
    ) : (
      <Soon tab={tab} product={p} />
    );
  return (
    <div className="shell">
      <aside className="side">
        <Picker products={products} product={p} pick={pick} />
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
          <Picker products={products} product={p} pick={pick} />
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
