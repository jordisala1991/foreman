// The panel's build: index.html and src/main.tsx into dist/ (index.html, app.js, app.css), fixed names, no hashes:
// fm/panel.py serves them with an ETag.
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  logLevel: "warn",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    target: "es2022",
    cssCodeSplit: false,
    modulePreload: false,
    reportCompressedSize: false,
    rolldownOptions: {
      output: { entryFileNames: "app.js", assetFileNames: "app.css", codeSplitting: false },
    },
  },
});
