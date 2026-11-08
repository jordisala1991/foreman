// The panel's entry: the projects from /api/projects, then the shell. The language is the browser's (es or en).
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./app.css";
import { refresh } from "./feed";
import { t, type Projects } from "./lib";
import { Shell } from "./Shell";

async function mount(el: HTMLElement) {
  const want = new URLSearchParams(location.search).get("lang") ?? navigator.language;
  document.documentElement.lang = want.toLowerCase().startsWith("es") ? "es" : "en";
  document.title = t("title");
  const root = createRoot(el);
  let data: Projects;
  try {
    data = await refresh<Projects>("/api/projects"); // the shell keeps reading it from the same feed
  } catch {
    root.render(<main className="none card">{t("load_failed")}</main>);
    return;
  }
  root.render(
    <StrictMode>
      <Shell all={data.projects} />
    </StrictMode>,
  );
}

const el = document.getElementById("app");
if (el) void mount(el);
