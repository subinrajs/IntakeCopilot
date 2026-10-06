import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5174,
    // Dev only: same-origin /api, like the Vercel rewrite in production, so the session cookie
    // works. Override with API_PROXY_TARGET when the API runs on another port.
    proxy: { "/api": process.env.API_PROXY_TARGET ?? "http://localhost:8000" },
  },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
