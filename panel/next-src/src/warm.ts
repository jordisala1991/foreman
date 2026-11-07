// What each screen reads, so a switch can have it before the screen draws (the router's loaders) and the page can read
// the other screens' data once it is quiet. The paths are the ones the screens ask for (feed.ts shares them).
import { queryClient, warm } from "./feed";
import type { Projects } from "./lib";

export const SECTIONS = ["home", "needs", "agents", "how"] as const;
export const MORE = ["backlog", "wiki", "system"] as const;
export const TABS = [...SECTIONS, "more"] as const;
export type Tab = (typeof TABS)[number] | (typeof MORE)[number];
export const ALL = "*"; // the picker's «All products»
export const KEPT = "next-product";

export const isTab = (s: string): s is Tab => (TABS as readonly string[]).includes(s) || (MORE as readonly string[]).includes(s);
export const tabOf = (splat: string | undefined): Tab => {
  const r = (splat ?? "").split("/")[0] ?? "";
  return isTab(r) ? r : "home";
};

interface Entry {
  name: string;
  next?: boolean;
  broken?: boolean;
  wiki?: boolean;
}

// the products the panel shows, from the shell's own feed
export function shown(): Entry[] {
  const d = queryClient.getQueryData<Projects>(["feed", "/api/projects"]);
  return ((d?.projects ?? []) as Entry[]).filter((p) => p.next && !p.broken);
}

// the product the owner last picked (the shell starts from the same answer)
export function chosen(products: { name: string }[]): string | undefined {
  const kept = localStorage.getItem(KEPT);
  return kept === ALL || products.some((p) => p.name === kept) ? (kept ?? undefined) : products[0]?.name;
}

export function pathsOf(tab: Tab, name: string | undefined): string[] {
  const all = shown();
  const everyone = name === ALL && all.length > 1;
  const scope = everyone ? all : all.filter((p) => p.name === name).concat(all).slice(0, 1);
  const q = (n: string) => encodeURIComponent(n);
  const one = scope[0];
  if (!one) return [];
  switch (tab) {
    case "home":
      return scope.flatMap((p) => [`/api/state?p=${q(p.name)}`, `/api/metrics?p=${q(p.name)}&days=7`, `/api/backlog?p=${q(p.name)}&closed=recent`]);
    case "needs":
      return scope.flatMap((p) => [`/api/state?p=${q(p.name)}`, `/api/backlog?p=${q(p.name)}&closed=recent`]);
    case "agents":
      return [`/api/agents?p=${q(everyone ? ALL : one.name)}`];
    case "how":
      return everyone ? [] : [`/api/metrics?p=${q(one.name)}&days=7`];
    case "backlog":
      return everyone ? [] : [`/api/backlog?p=${q(one.name)}&closed=recent`, `/api/state?p=${q(one.name)}`];
    case "wiki":
      return everyone || !one.wiki ? [] : [`/api/wiki?p=${q(one.name)}`];
    case "system":
      return everyone ? [] : ["/api/projects", `/api/state?p=${q(one.name)}`];
    default:
      return [];
  }
}

// Resolves when the data of `tab` for the picked product is in the cache (at once if it is), or after `LIMIT` ms: a read
// that does not come must not hold the old screen for ever, the new one then says so itself.
const LIMIT = 3000;
export function warmTab(tab: Tab, name?: string): Promise<void> {
  const paths = pathsOf(tab, name ?? chosen(shown()));
  if (!paths.length) return Promise.resolve();
  return Promise.race([warm(paths), new Promise<void>((r) => setTimeout(r, LIMIT))]);
}

// the other screens' data, one screen at a time, once the page has been quiet for a moment
export function warmRest(from: Tab, name: string | undefined): () => void {
  const todo = [...SECTIONS, ...MORE].filter((x) => x !== from);
  let id: ReturnType<typeof setTimeout> | undefined;
  const next = () => {
    const tab = todo.shift();
    if (!tab) return;
    if (document.visibilityState !== "hidden") void warm(pathsOf(tab, name));
    id = setTimeout(next, 400);
  };
  id = setTimeout(next, 1500);
  return () => clearTimeout(id);
}
