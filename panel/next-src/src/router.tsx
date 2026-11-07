// The new panel's routes (TanStack Router, hash history: #/agents, #/backlog/AB-1, the key stays in the query before the
// hash). Every screen is one route that, before it draws, has its data in the cache (the loader, warm.ts): the old
// screen stays until then, and only a wait longer than `pendingMs` shows the new screen's header with a block of its
// size (Pending). A switch is one change.
import { createHashHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { Pending, Screen, Shell } from "./Shell";
import { tabOf, warmTab, type Tab } from "./warm";

// The first load does not wait for data: the page draws its shell at once and the screen its header and a block where its
// content comes (it asks for it itself); only a switch inside the page waits, with the old screen in view.
let first = true;
const warmed = (tab: Tab): Promise<void> | undefined => {
  const done = warmTab(tab);
  if (!first) return done;
  first = false;
  return undefined;
};

const root = createRootRoute({ component: Shell });
const screen = { component: Screen, pendingComponent: Pending, pendingMs: 250, pendingMinMs: 200 };
const home = createRoute({ getParentRoute: () => root, path: "/", loader: () => warmed("home"), ...screen });
const other = createRoute({ getParentRoute: () => root, path: "$", loader: ({ params }) => warmed(tabOf(params._splat)), ...screen });

export const router = createRouter({
  routeTree: root.addChildren([home, other]),
  history: createHashHistory(),
  scrollRestoration: false,
  defaultPreload: false,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
