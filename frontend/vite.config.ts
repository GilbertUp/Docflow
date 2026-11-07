import react from "@vitejs/plugin-react";
import { configDefaults, defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // The API is a separate process in development. The browser still sees one origin.
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  preview: { proxy: { "/api": "http://127.0.0.1:8000" } },
  build: { sourcemap: true, outDir: "dist" },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    css: false,
    restoreMocks: true,
    // The browser journeys under e2e/ are run by Playwright, not by vitest.
    exclude: [...configDefaults.exclude, "e2e/**", "e2e-results/**"],
  },
});
