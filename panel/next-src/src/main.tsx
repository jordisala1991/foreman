// The new panel's entry: the products from /api/projects, then the shell. index.html loads this module (app.js).
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./app.css";
import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { queryClient, refresh } from "./feed";
import { router } from "./router";
import { t } from "./lib";

async function mount(el: HTMLElement) {
  document.title = t("title");
  const root = createRoot(el);
  try {
    await refresh("/api/projects"); // the shell keeps reading it from the same feed
  } catch {
    root.render(<main className="none card">{t("load_failed")}</main>);
    return;
  }
  root.render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </StrictMode>,
  );
}

const el = document.getElementById("app");
if (el) void mount(el);
