// The shell, as in foreman's panel: on a wide screen a 248 px sidebar (the project picker and the three sections with
// their badges); on the phone the picker and the loop's pill on top and a bottom bar of three tabs. A task's page is
// #/issue/ID. The theme follows the system (app.css).
import { useEffect, useState } from "react";
import { Boundary } from "./Boundary";
import { useFeedPart } from "./feed";
import { StatusPill } from "./head";
import { Chevron, Icon } from "./icons";
import { Issue } from "./Issue";
import { t, type Key, type Product, type Projects } from "./lib";
import { Needs } from "./Needs";
import { System } from "./System";
import { Tasks } from "./Tasks";

const TABS = ["tasks", "needs", "system"] as const;
type Tab = (typeof TABS)[number] | "issue";

const route = (): { tab: Tab; id: string } => {
  const [r = "", id = ""] = location.hash.replace(/^#\/?/, "").split("/");
  return { tab: (TABS as readonly string[]).includes(r) || r === "issue" ? (r as Tab) : "tasks", id: decodeURIComponent(id) };
};

function Picker({ products, product, pick }: { products: Product[]; product: Product; pick: (name: string) => void }) {
  return (
    <label className="picker">
      <span className="name">{product.name}</span>
      <Chevron />
      <select aria-label={t("product")} value={product.name} onChange={(e) => pick(e.target.value)}>
        {products.map((p) => (
          <option key={p.name} value={p.name}>
            {p.name}
          </option>
        ))}
      </select>
    </label>
  );
}

const Badge = ({ n, red }: { n: number; red: boolean }) => (n ? <span className={red ? "badge red" : "badge"}>{n}</span> : null);

export function None() {
  return (
    <main className="none card">
      <h1>{t("none_title")}</h1>
      <p>{t("none_how")}</p>
      <pre>{"fm init --repo PATH --prefix AB"}</pre>
    </main>
  );
}

const KEPT = "fm-product";
const REFRESH_MS = 10_000;

export function Shell({ all: initial }: { all: Product[] }) {
  const every = useFeedPart<Projects, Product[]>("/api/projects", REFRESH_MS, (d) => d.projects) ?? initial;
  const products = every.filter((p) => !p.broken);
  const [r, setRoute] = useState(route);
  const [name, setName] = useState<string | undefined>(() => localStorage.getItem(KEPT) ?? undefined);
  useEffect(() => {
    const go = () => {
      setRoute(route());
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
    location.hash = "#/tasks";
  };
  const badge: Partial<Record<Tab, number>> = { needs: p.needs ?? 0, tasks: p.running ?? 0 };
  const current = (k: Tab) => (r.tab === k || (k === "tasks" && r.tab === "issue") ? "page" : undefined);
  const screen =
    r.tab === "needs" ? <Needs product={p} />
    : r.tab === "system" ? <System product={p} />
    : r.tab === "issue" && r.id ? <Issue product={p} id={r.id} />
    : <Tasks product={p} />;
  return (
    <div className="shell">
      <aside className="side">
        <Picker products={products} product={p} pick={pick} />
        <nav aria-label={t("sections")} className="navs">
          {TABS.map((k) => (
            <a key={k} className="nav" href={`#/${k}`} aria-current={current(k)}>
              <Icon name={k} size={20} />
              <span className="label">{t(`tab_${k}` as Key)}</span>
              <Badge n={badge[k] ?? 0} red={k === "needs"} />
            </a>
          ))}
        </nav>
      </aside>
      <main>
        <div className="top">
          <Picker products={products} product={p} pick={pick} />
          <StatusPill product={p} />
        </div>
        <Boundary key={`${r.tab}/${r.id}/${p.name}`}>{screen}</Boundary>
      </main>
      <nav className="bar" aria-label={t("sections")}>
        {TABS.map((k) => (
          <a key={k} className="tab" href={`#/${k}`} aria-current={current(k)}>
            <span className="icon">
              <Icon name={k} />
              <Badge n={badge[k] ?? 0} red={k === "needs"} />
            </span>
            {t(`tab_${k}` as Key)}
          </a>
        ))}
      </nav>
    </div>
  );
}
