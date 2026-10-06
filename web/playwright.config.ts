import { defineConfig, devices } from "@playwright/test";

/** End-to-end tests against a real API and a freshly seeded database (dev-oracle backend, so no
 * model calls). Local default: Postgres from docker compose on 5433. CI sets E2E_* variables. */
const owner =
  process.env.E2E_DATABASE_MIGRATION_URL ??
  "postgresql://intake_owner:owner_dev_password@localhost:5433/intakecopilot_e2e";
const app =
  process.env.E2E_DATABASE_URL ??
  "postgresql://intake_app:app_dev_password@localhost:5433/intakecopilot_e2e";
const backendEnv = [
  `DATABASE_URL=${app}`,
  `DATABASE_MIGRATION_URL=${owner}`,
  "LLM_BACKEND=dev-oracle",
  "STORAGE_DIR=../storage-e2e",
  "RUN_WORKER=true",
].join(" ");

export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: { baseURL: "http://localhost:5175", trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `cd ../backend && env ${backendEnv} uv run python -m intake.devdb --demo && env ${backendEnv} uv run uvicorn intake.api.app:app --port 8010`,
      url: "http://localhost:8010/readyz",
      timeout: 180_000,
      reuseExistingServer: false,
    },
    {
      command: "pnpm exec vite --port 5175 --strictPort",
      url: "http://localhost:5175",
      env: { API_PROXY_TARGET: "http://localhost:8010" },
      reuseExistingServer: false,
    },
  ],
});
