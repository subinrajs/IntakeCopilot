import { expect, test } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const gold = (key: string) => path.resolve(here, `../../data/gold/v1/pdfs/${key}.pdf`);

test("an uploaded requisition streams progress and reaches the review queue", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Username").fill("intake");
  await page.getByLabel("Password").fill(process.env.SEED_STAFF_PASSWORD ?? "lakeshore-demo");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Upload requisitions" })).toBeVisible();
  await page.getByTestId("file-input").setInputFiles(gold("gold-v1-060"));
  await expect(page.getByText("gold-v1-060.pdf")).toBeVisible();
  await expect(page.getByRole("link", { name: /Ready for review/ })).toBeVisible({ timeout: 20_000 });
});

test("a file that is not a requisition is refused", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Username").fill("intake");
  await page.getByLabel("Password").fill(process.env.SEED_STAFF_PASSWORD ?? "lakeshore-demo");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByTestId("file-input").setInputFiles({
    name: "notes.txt",
    mimeType: "application/pdf",
    buffer: Buffer.from("not a pdf"),
  });
  await expect(page.getByRole("alert")).toContainText("only PDF, PNG and JPEG");
});
