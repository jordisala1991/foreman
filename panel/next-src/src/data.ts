// The data of the home and «Needs you» screens: for the products in scope, each one's state (/api/state, polled while
// the page is visible), the short names of its issues (/api/backlog, once) and, for the home, its metrics
// (/api/metrics, polled). `reload` asks again at once (after a write).
import { useCallback, useMemo } from "react";
import { reloadAll, useFeeds } from "./feed";
import type { Metrics, State } from "./lib";

export interface Short {
  [id: string]: string;
}
export interface Scope {
  state?: State;
  shorts: Short;
  metrics?: Metrics;
}
interface Card {
  id: string;
  title: string;
  short?: string;
}
interface Backlog {
  issues?: Card[];
  projects?: { issues: Card[] }[];
}

const POLL_MS = 10_000;

export function shortsOf(b: Backlog): Short {
  const out: Short = {};
  for (const c of [...(b.issues ?? []), ...(b.projects ?? []).flatMap((p) => p.issues)]) out[c.id] = c.short || c.title;
  return out;
}

export interface Loaded {
  data: Record<string, Scope>;
  failed: boolean; // a state could not be read (the cards may be old, or missing)
  metricsFailed: boolean; // the figures could not be read: only the tiles go blank
  reload: () => void;
}

const NONE: Short = {};

// Each product's state and figures are feeds (feed.ts): read apart, so a failing /api/metrics must not hide what waits,
// and the object of a product stays the same until one of its answers differs.
export function useScope(names: string[], withMetrics: boolean): Loaded {
  const paths = names.flatMap((n) => {
    const q = encodeURIComponent(n);
    return [`/api/state?p=${q}`, withMetrics ? `/api/metrics?p=${q}&days=7` : null];
  });
  const polled = useFeeds<unknown>(paths, POLL_MS);
  const backlogs = useFeeds<Backlog>(
    names.map((n) => `/api/backlog?p=${encodeURIComponent(n)}&closed=recent`),
    0,
  );
  // the screens that use this are keyed by their products, so the number of snapshots never changes under a mount
  const data = useMemo(() => {
    const out: Record<string, Scope> = {};
    names.forEach((n, i) => {
      const s = polled[2 * i]?.data as { project: State } | undefined;
      const m = polled[2 * i + 1]?.data as Metrics | undefined;
      const b = backlogs[i]?.data;
      out[n] = { shorts: b ? shortsOf(b) : NONE, ...(s ? { state: s.project } : {}), ...(m ? { metrics: m } : {}) };
    });
    return out;
  }, [...polled, ...backlogs]); // eslint-disable-line react-hooks/exhaustive-deps
  const failed = names.some((_, i) => polled[2 * i]?.failed);
  const metricsFailed = withMetrics && names.some((_, i) => polled[2 * i + 1]?.failed);
  const reload = useCallback(() => reloadAll(paths.filter((p): p is string => p !== null)), [paths.join("\n")]); // eslint-disable-line react-hooks/exhaustive-deps
  return { data, failed, metricsFailed, reload };
}
