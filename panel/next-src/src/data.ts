// The data of the home and «Needs you» screens: for the products in scope, each one's state (/api/state, polled while
// the page is visible), the short names of its issues (/api/backlog, once) and, for the home, its metrics
// (/api/metrics, polled). `reload` asks again at once (after a write).
import { useCallback, useEffect, useState } from "react";
import { api, type Metrics, type State } from "./lib";

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

export function useScope(names: string[], withMetrics: boolean): { data: Record<string, Scope>; failed: boolean; reload: () => void } {
  const key = names.join("\n");
  const [data, setData] = useState<Record<string, Scope>>({});
  const [failed, setFailed] = useState(false);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    const list = key ? key.split("\n") : [];
    const load = async () => {
      const got = await Promise.all(
        list.map(async (n): Promise<[string, Partial<Scope>] | null> => {
          const q = encodeURIComponent(n);
          try {
            const [s, m] = await Promise.all([
              api<{ project: State }>(`/api/state?p=${q}`),
              withMetrics ? api<Metrics>(`/api/metrics?p=${q}&days=7`) : Promise.resolve(undefined),
            ]);
            return [n, { state: s.project, ...(m ? { metrics: m } : {}) }];
          } catch {
            return null;
          }
        }),
      );
      if (!live) return;
      setFailed(got.some((g) => g === null));
      setData((old) => {
        const next: Record<string, Scope> = {};
        for (const g of got) if (g) next[g[0]] = { shorts: {}, ...old[g[0]], ...g[1] };
        for (const n of list) if (!next[n]) next[n] = old[n] ?? { shorts: {} };
        return next;
      });
    };
    void load();
    const loadShorts = () =>
      list.forEach((n) => {
        api<Backlog>(`/api/backlog?p=${encodeURIComponent(n)}&closed=recent`)
          .then((b) => live && setData((old) => ({ ...old, [n]: { ...(old[n] ?? {}), shorts: shortsOf(b) } })))
          .catch(() => undefined);
      });
    loadShorts();
    const id = setInterval(() => {
      if (document.visibilityState !== "hidden") void load();
    }, POLL_MS);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [key, withMetrics, tick]);
  const reload = useCallback(() => setTick((x) => x + 1), []);
  return { data, failed, reload };
}
