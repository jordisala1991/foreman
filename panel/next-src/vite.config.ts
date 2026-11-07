// The build of the new panel: src/main.tsx into one module (../next/app.js) and one stylesheet (../next/app.css),
// with index.html copied as the server's template, the licences of the bundled packages (LICENSES.txt) and BUILD:
// the sha256 of every source file and of every file written, which the selftest checks without node
// (tests/test_panel_next.py). Fixed names, no hashes: the server adds the version and the key to each URL.
import { createHash } from "node:crypto";
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(HERE, "../next");
const OUTPUTS = ["index.html", "app.js", "app.css", "LICENSES.txt"];

const sha256 = (data: string | Buffer) => createHash("sha256").update(data).digest("hex");

// every file of the source but node_modules/ and dot files, as `path` relative to this folder, sorted
function sources(dir = HERE): string[] {
  return readdirSync(dir, { withFileTypes: true })
    .filter((e) => !e.name.startsWith(".") && e.name !== "node_modules")
    .flatMap((e) => (e.isDirectory() ? sources(join(dir, e.name)) : [relative(HERE, join(dir, e.name)).split(sep).join("/")]))
    .sort();
}

// sha256sum-style lines ("<sha256>  <path>\n"), and the sha256 of those lines
function digest(root: string, files: string[]): { lines: string; sum: string } {
  const lines = files.map((f) => `${sha256(readFileSync(join(root, f)))}  ${f}\n`).join("");
  return { lines, sum: sha256(lines) };
}

// the package a bundled module comes from: its folder under node_modules/
function packageDir(id: string): string | null {
  const m = /^(.*[\\/]node_modules[\\/](?:@[^\\/]+[\\/])?[^\\/]+)[\\/]/.exec(id);
  return m && m[1] ? m[1] : null;
}

function shipped(): Plugin {
  return {
    name: "foreman-shipped",
    apply: "build",
    generateBundle(_options, bundle) {
      const dirs = new Set<string>();
      for (const chunk of Object.values(bundle)) {
        if (chunk.type === "chunk") for (const id of Object.keys(chunk.modules)) {
          const d = packageDir(id);
          if (d) dirs.add(d);
        }
      }
      const licences = [...dirs]
        .map((d) => {
          const pkg = JSON.parse(readFileSync(join(d, "package.json"), "utf8")) as { name: string; version: string; license?: string };
          const file = readdirSync(d).find((f) => /^(licen[cs]e|copying)(\.(md|txt))?$/i.test(f));
          const text = file ? readFileSync(join(d, file), "utf8").trim() : `(${pkg.license ?? "no licence"}: no licence file in the package)`;
          return { key: pkg.name, text: `${pkg.name} ${pkg.version} (${pkg.license ?? "?"})\n\n${text}\n` };
        })
        .sort((a, b) => (a.key < b.key ? -1 : 1));
      this.emitFile({
        type: "asset",
        fileName: "LICENSES.txt",
        source: "The packages bundled into app.js, with their licences.\n\n" + licences.map((l) => l.text).join("\n---\n\n"),
      });
      this.emitFile({ type: "asset", fileName: "index.html", source: readFileSync(join(HERE, "index.html"), "utf8") });
    },
    closeBundle() {
      const src = digest(HERE, sources());
      const out = digest(OUT, OUTPUTS);
      writeFileSync(
        join(OUT, "BUILD"),
        "# Written by `npm run build` in panel/next-src: do not edit. The selftest checks that the sources and the\n" +
          "# files built from them still have these sha256 (a source changed without a build, or a built file changed\n" +
          "# by hand, fails it).\n" +
          `source ${src.sum}\n` +
          out.lines,
      );
    },
  };
}

export default defineConfig({
  plugins: [react(), shipped()],
  logLevel: "warn",
  build: {
    outDir: OUT,
    emptyOutDir: true,
    target: "es2022",
    cssCodeSplit: false,
    modulePreload: false,
    reportCompressedSize: false,
    rolldownOptions: {
      input: resolve(HERE, "src/main.tsx"),
      output: { entryFileNames: "app.js", assetFileNames: "app.css", codeSplitting: false },
    },
  },
});
