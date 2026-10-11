import { defineConfig } from "vite";

// Built assets are served by FastAPI from /app/static; relative URLs keep that working.
export default defineConfig({
  base: "./",
  build: { outDir: "dist", emptyOutDir: true, sourcemap: false },
  server: { proxy: { "/ask": "http://127.0.0.1:8000", "/health": "http://127.0.0.1:8000" } },
});
