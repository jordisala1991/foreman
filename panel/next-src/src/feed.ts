// The one place the panel reads its API from (TanStack Query): every path is a query in one cache shared by all the
// screens and kept across switches, so a screen visited before shows its data at once and asks again behind it. One
// answer equal to the last (structural sharing) changes nothing, so nothing redraws; one that differs, or a failure,
// replaces the snapshot, and a refresh never empties it. `refetchInterval` is the period (paused while the page is
// hidden); the server's ETag is used by the browser itself (`no-cache` makes it ask with If-None-Match).
import { QueryClient, useQueries, useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { withKey } from "./lib";

export interface Snap<T> {
  data: T | undefined;
  failed: boolean; // the last read failed (`data` is the last good answer, if any)
  since: Date | null; // when `data` was read, only while `failed`
}

export const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 1000, gcTime: Infinity, retry: false } },
});

const read = async (path: string): Promise<unknown> => {
  const r = await fetch(withKey(path), { cache: "no-cache" });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return (await r.json()) as unknown;
};
const feed = (path: string, every: number) => ({
  queryKey: ["feed", path],
  queryFn: () => read(path),
  refetchInterval: every > 0 ? every : (false as const),
});

// a first read that failed shows as failed only once nothing is on its way (a read that starts again is «loading»)
function snap<T>(q: {
  data: unknown;
  isError: boolean;
  isFetching: boolean;
  dataUpdatedAt: number;
}): Snap<T> {
  const failed = q.isError;
  return { data: q.data as T | undefined, failed, since: failed && q.dataUpdatedAt ? new Date(q.dataUpdatedAt) : null };
}

// Reads `path` now; resolves with the answer.
export function refresh<T = unknown>(path: string): Promise<T> {
  return queryClient.fetchQuery({ ...feed(path, 0), staleTime: 0 }) as Promise<T>;
}

// Has an answer of `paths` at most a second old in the cache before a screen opens, so the screen draws once, with
// current data (the old screen stays in view while the reads are on their way; an old cache is never painted and then
// swapped). A failure is the screen's to show: it draws what it had.
export function warm(paths: string[]): Promise<void> {
  return Promise.all(paths.map((p) => queryClient.prefetchQuery({ ...feed(p, 0), staleTime: 1000 }))).then(() => undefined);
}

// The snapshots of several paths (null: nothing to read); the array and each snapshot keep their identity until
// something in them changes. `every`: the period of the timer, 0 for reading once on mount.
export function useFeeds<T>(paths: (string | null)[], every = 0): Snap<T>[] {
  return useQueries({
    queries: paths.map((p) => ({ ...feed(p ?? "", every), enabled: p !== null })),
    combine: (rs) => rs.map((r) => snap<T>(r)),
  });
}

// One part of a feed's answer (what the shell needs of /api/projects, while a screen of the system reads the machine's
// load from the same answer): the part keeps its identity until it differs.
export function useFeedPart<T, S>(path: string, every: number, select: (d: T) => S): S | undefined {
  return useQuery({ ...feed(path, every), select: (d) => select(d as T) }).data;
}

export interface Feed<T> extends Snap<T> {
  reload: () => void;
}

export function useFeed<T>(path: string | null, every = 0): Feed<T> {
  const q = useQuery({ ...feed(path ?? "", every), enabled: path !== null });
  const { refetch } = q;
  const reload = useCallback(() => void refetch(), [refetch]);
  return { ...snap<T>(q), reload };
}

// Reads every path again now (after a write)
export function reloadAll(paths: string[]): void {
  paths.forEach((p) => void queryClient.refetchQueries({ queryKey: ["feed", p] }));
}

// The time, in seconds, for the durations on screen: it moves on when the minute turns, and a tick inside the same
// minute keeps the same value, so nothing redraws.
export function useNow(): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  const shown = useRef(now);
  useEffect(() => {
    const id = setInterval(() => {
      const t = Date.now() / 1000;
      if (Math.floor(t / 60) === Math.floor(shown.current / 60)) return; // no setState: not even a render
      shown.current = t;
      setNow(t);
    }, 15_000);
    return () => clearInterval(id);
  }, []);
  return now;
}
