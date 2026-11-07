// What every screen shares: the texts, the panel's API with its key, and the formats of dates and dollars.
import en from "./strings/en.json";
import es from "./strings/es.json";

export type Key = keyof typeof es;
const LANGS: Record<string, Record<Key, string>> = { es, en };

export const lang = (): string => document.documentElement.lang || "en";

// a text of strings/<language>.json, its {name} placeholders filled from `vars`
export function t(key: Key, vars?: Record<string, string | number>): string {
  const text = (LANGS[lang()] ?? en)[key];
  return text.replace(/\{(\w+)\}/g, (m, k: string) => (vars && k in vars ? String(vars[k]) : m));
}

const key = (): string => new URLSearchParams(location.search).get("t") ?? "";
export const withKey = (path: string): string =>
  path + (path.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(key());

export async function api<T>(path: string): Promise<T> {
  const r = await fetch(withKey(path), { cache: "no-cache" });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return (await r.json()) as T;
}

// the panel of today, at the same key (the screens not built yet send there)
export const oldPanel = (): string => withKey("/");

// whole dollars ($219), or with one decimal ($60.8) where a detail is read
export const usd = (x: number): string => "$" + Math.round(x).toString();
export const usd1 = (x: number): string => "$" + x.toFixed(1);

const noon = (iso: string): Date => new Date(iso + "T12:00:00");
export const dayLabel = (iso: string, opts: Intl.DateTimeFormatOptions): string =>
  new Intl.DateTimeFormat(lang(), opts).format(noon(iso)).replace(/\.$/, "").replace(/\. /, " ");
// «sáb 1»: the weekday, short, and the day
export const shortDay = (iso: string): string =>
  `${dayLabel(iso, { weekday: "short" })} ${noon(iso).getDate()}`;
// «1 nov»
export const dayMonth = (iso: string): string => `${noon(iso).getDate()} ${dayLabel(iso, { month: "short" })}`;

// what /api/projects says of a product (the fields the new panel reads)
export interface Product {
  name: string;
  title: string;
  next?: boolean;
  broken?: boolean;
  decisions?: number;
  unread?: number;
  agents?: number;
  paused?: string | null;
  active?: boolean;
  orchestrator_health?: { verdict: string; last_pass?: string | null } | null;
}

export interface Projects {
  projects: Product[];
  language?: string;
}

// /api/metrics (lib/foreman/metrics.py `build`, plus the account's `quota`): the fields the charts read
export type Family = "opus" | "sonnet" | "haiku" | "other";
export type DayCost = Record<Family | "usd", number> & { cheap_share?: number | null };
export interface Delivery {
  integrated: number;
  measured: number;
  ok: number;
  partial: number;
  failed: number;
}
export interface Metrics {
  days: string[];
  cost: Record<string, DayCost>;
  total: number;
  top_issues: { issue: string; short: string; usd: number; runs: number }[];
  by_type: { type: string; usd: number; runs: number }[];
  deliveries: Record<string, Delivery>;
  reviews: Record<string, { clean: number; back: number }>;
  rounds: Record<string, number>;
  guard: Record<string, { agent: number; orchestrator: number; other: number }>;
  guard_rules: { rule: string; band: string; n: number }[];
  // the first day with data of each series (a day, or null)
  since: { cost: string | null; deliveries: string | null; reviews: string | null; guard: string | null };
  quota?: { kind: string; percent_used: number; resets_at?: string | null }[];
}

// «15:20»
export const clock = (d: Date): string => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
