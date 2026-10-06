import { type Page, expect, test } from "@playwright/test";

const PASSWORD = process.env.SEED_STAFF_PASSWORD ?? "lakeshore-demo";

async function signIn(page: Page, user: "intake" | "radiologist" | "admin") {
  await page.goto("/login");
  await page.getByLabel("Username").fill(user);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}

async function openCase(page: Page, exam: RegExp) {
  await page.goto("/queue");
  await page.getByRole("row").filter({ hasText: exam }).first().getByRole("link").click();
  await expect(page.getByRole("heading", { name: "Requisition fields" })).toBeVisible();
}

/** Radiologist opened the case and holds the soft lock: decision controls are live. */
async function startReview(page: Page, exam: RegExp) {
  await openCase(page, exam);
  await expect(page.getByRole("radiogroup", { name: "Final priority" })).toBeVisible();
}

test("queue pins red-flag cases above everything else", async ({ page }) => {
  await signIn(page, "radiologist");
  await page.goto("/queue");
  const rows = page.getByRole("row");
  await expect(rows.nth(1)).toContainText("P1");
  await expect(rows.nth(1).getByLabel("Red flag")).toBeVisible();
  const priorities = await rows.locator("td:first-child").allInnerTexts();
  const ranks = priorities.map((p) => Number(/P(\d)/.exec(p)?.[1] ?? 0));
  expect(ranks).toEqual([...ranks].sort((a, b) => a - b));
});

test("a case is reviewed and approved by keyboard alone", async ({ page }) => {
  await signIn(page, "radiologist");
  await startReview(page, /Knee/);
  await page.keyboard.press("a");
  await expect(page.getByText("Case: accept")).toBeVisible();
  await expect(page.getByRole("button", { name: "Export and close" })).toBeVisible();
});

test("overriding the priority requires a reason", async ({ page }) => {
  await signIn(page, "radiologist");
  await startReview(page, /Shoulder/);
  await page.keyboard.press("1"); // P1 differs from the suggestion -> override
  await expect(page.getByPlaceholder(/Reason for overriding the suggested priority/)).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByText("Give a reason for overriding the priority.")).toBeVisible();
  await expect(page.getByRole("button", { name: /Approve/ })).toBeDisabled();
  await page.getByPlaceholder(/Reason for overriding the suggested priority/).fill("Acute locking, cannot weight bear");
  await page.getByRole("button", { name: /Approve/ }).click();
  await expect(page.getByText(/priority: override → P1/i)).toBeVisible();
});

test("approval is blocked until every contrast flag is acknowledged", async ({ page }) => {
  await signIn(page, "radiologist");
  await startReview(page, /Chest, abdomen and pelvis/); // eGFR 25 with IV contrast
  await expect(page.getByText(/eGFR 25 is below the review threshold/)).toBeVisible();
  await expect(page.getByRole("button", { name: /Approve/ })).toBeDisabled();
  await page.keyboard.press("f");
  await expect(page.getByRole("checkbox")).toBeChecked();
  await page.getByRole("button", { name: /Approve/ }).click();
  await expect(page.getByText(/contrast: acknowledge → egfr_low/i)).toBeVisible();
});

test("intake staff cannot approve, and a second radiologist sees the lock", async ({ browser }) => {
  const first = await browser.newPage();
  await signIn(first, "radiologist");
  await startReview(first, /Prostate/);
  const intake = await browser.newPage();
  await signIn(intake, "intake");
  await intake.goto(first.url());
  await expect(intake.getByText("A radiologist approves, overrides or rejects every case.")).toBeVisible();
  await expect(intake.getByRole("button", { name: /Approve/ })).toHaveCount(0);
});
