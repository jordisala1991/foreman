// The shell of the new panel, as in the approved mockups: on a wide screen a 248 px sidebar (the product picker, the
// four sections with their badges, the «More» group); on the phone the picker and the product's status pill on top
// and a bottom bar of five tabs. The theme follows the system (app.css); there is no switch.
import { useEffect, useState } from "react";
import { Agents } from "./Agents";
import { Boundary } from "./Boundary";
import { How } from "./How";
import { Backlog } from "./more/Backlog";
import { System } from "./more/System";
import { Wiki } from "./more/Wiki";
import type { MoreProduct } from "./more/types";
import { Home } from "./Home";
import { Needs } from "./Needs";
import { ScreenHead, StatusPill, productState } from "./head";
import { Chevron, Icon } from "./icons";
import { useFeedPart } from "./feed";
import { oldPanel, t, type Key, type Product, type Projects } from "./lib";

const SECTIONS = ["home", "needs", "agents", "how"] as const;
const MORE = ["backlog", "wiki", "system"] as const;
const TABS = [...SECTIONS, "more"] as const;
type Tab = (typeof TABS)[number] | (typeof MORE)[number];

const isTab = (s: string): s is Tab => (TABS as readonly string[]).includes(s) || (MORE as readonly string[]).includes(s);
const route = (): Tab => {
  const r = location.hash.replace(/^#\/?/, "").split("/")[0] ?? "";
  return isTab(r) ? r : "home";
};

const ALL = "*"; // the picker's «All products»

function Picker({ products, product, all, pick }: { products: Product[]; product: Product; all: boolean; pick: (name: string) => void }) {
  return (
    <label className="picker">
      <span className="name">{all ? t("all_products") : product.title}</span>
      <Chevron />
      <select aria-label={t("product")} value={all ? ALL : product.name} onChange={(e) => pick(e.target.value)}>
        {products.length > 1 ? <option value={ALL}>{t("all_products")}</option> : null}
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

function Soon({ tab, product, all }: { tab: Tab; product: Product; all: boolean }) {
  return (
    <div className="screen">
      <ScreenHead title={t(`tab_${tab}` as Key)} product={product} />
      <div className="card quiet">
        {all ? `${t("pick_one")} ` : null}
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

// the «All» entry of the Agents screen (it asks /api/agents with name «*»): the sums of the products, and a pill that counts those with an orchestrator running
function joint(products: Product[]): Product {
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

const REFRESH_MS = 10_000;

export function Shell({ all: initial }: { all: Product[] }) {
  // the products and their counts, read again while the page is visible (the badges follow what waits)
  const every = useFeedPart<Projects, Product[]>("/api/projects", REFRESH_MS, (d) => d.projects) ?? initial;
  const products = every.filter((p) => p.next && !p.broken);
  const [tab, setTab] = useState<Tab>(route);
  const [, setHash] = useState(location.hash); // a screen's own route (#/backlog/AB-1) re-renders it
  const [name, setName] = useState<string | undefined>(() => {
    const kept = localStorage.getItem(KEPT);
    return kept === ALL || products.some((p) => p.name === kept) ? (kept ?? undefined) : products[0]?.name;
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
  const everyone = name === ALL && products.length > 1;
  const p = products.find((x) => x.name === name) ?? products[0];
  if (!p) return <None />;
  const scope = everyone ? products : [p];
  const pick = (n: string) => {
    localStorage.setItem(KEPT, n);
    setName(n);
  };
  // what waits: the questions, the permissions to approve and the orchestrator's unread messages
  const sum = (f: (x: Product) => number | undefined) => scope.reduce((n, x) => n + (f(x) ?? 0), 0);
  const badge: Partial<Record<Tab, number>> = {
    needs: sum((x) => x.decisions) + sum((x) => x.permissions) + sum((x) => x.unread),
    agents: sum((x) => x.agents),
  };
  const current = (k: Tab, bar: boolean) =>
    tab === k || (bar && k === "more" && (MORE as readonly string[]).includes(tab)) ? "page" : undefined;
  const screen =
    tab === "home" ? (
      <Home key={scope.map((x) => x.name).join()} scope={scope} all={everyone} />
    ) : tab === "needs" ? (
      <Needs key={scope.map((x) => x.name).join()} scope={scope} all={everyone} />
    ) : tab === "how" ? (
      everyone ? <Soon tab={tab} product={p} all /> : <How product={p} />
    ) : tab === "backlog" || tab === "wiki" || tab === "system" ? (
      everyone ? (
        <Soon tab={tab} product={p} all />
      ) : tab === "backlog" ? (
        <Backlog product={p as MoreProduct} />
      ) : tab === "wiki" ? (
        <Wiki product={p as MoreProduct} products={products as MoreProduct[]} pick={pick} />
      ) : (
        <System product={p as MoreProduct} />
      )
    ) : tab === "agents" ? (
      <Agents
        product={everyone ? joint(products) : p}
        titles={Object.fromEntries(products.map((x) => [x.name, x.title]))}
      />
    ) : tab === "more" ? (
      <More product={p} />
    ) : (
      <Soon tab={tab} product={p} all={everyone} />
    );
  return (
    <div className="shell">
      <aside className="side">
        <Picker products={products} product={p} all={everyone} pick={pick} />
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
          <Picker products={products} product={p} all={everyone} pick={pick} />
          <StatusPill product={p} all={everyone ? products : undefined} />
        </div>
        <Boundary key={`${tab}/${everyone ? "*" : p.name}`}>{screen}</Boundary>
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
