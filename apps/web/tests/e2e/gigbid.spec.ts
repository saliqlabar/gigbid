import { expect, test } from "@playwright/test";
import { login, openFirstPending } from "./helpers";

test("onboarding: a new freelancer signs up, picks a persona, sets rates and budget @mobile", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("button", { name: /Create an account/ }).click();
  await page.getByLabel("Username").fill(`newuser${Date.now() % 100000}`);
  await page.getByLabel("Password").fill("secret-pass");
  await page.getByLabel("Start from a persona").selectOption("ai_ml");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Set up GigBid" })).toBeVisible();
  await page.getByLabel("Hourly rate (USD)").fill("40");
  await page.getByLabel("Weekly Connects budget").fill("50");
  await page.getByLabel(/Saved search keywords/).fill("llm");
  await page.getByRole("button", { name: "Finish setup" }).click();
  await expect(page.getByRole("heading", { name: "Job inbox" })).toBeVisible();
  await page.goto("/settings");
  await expect(page.getByLabel("Weekly Connects budget")).toHaveValue("50");
});

test("approving a proposal submits it once and spends the previewed Connects", async ({ page }) => {
  await login(page);
  await openFirstPending(page);
  await expect(page.getByTestId("dry-run")).toBeVisible();
  // evidence on hover/focus for a cited claim
  const chip = page.getByTestId("evidence-letter").locator(".cite button").first();
  await chip.focus();
  await expect(page.getByRole("tooltip")).toBeVisible();
  await page.getByRole("button", { name: /Approve and submit/ }).click();
  await expect(page.getByText(/Submitted\. Connects spent once/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Pipeline" })).toBeVisible();
  await expect(page.getByRole("listitem", { name: /submitted: [1-9]/ })).toBeVisible();
});

test("rejecting a proposal requires a reason", async ({ page }) => {
  await login(page);
  await openFirstPending(page);
  await page.getByRole("button", { name: "Reject" }).click();
  const confirm = page.getByRole("button", { name: "Confirm reject" });
  await expect(confirm).toBeDisabled();
  await page.getByLabel(/Why are you rejecting it/).fill("client budget is too low");
  await confirm.click();
  await expect(page.getByText("Rejected. Your reason was recorded.")).toBeVisible();
  await page.goto("/analytics");
  await expect(page.getByText("client budget is too low")).toBeVisible();
});

test("editing a draft re-runs Compliance and keeps the approval pending", async ({ page }) => {
  await login(page);
  await openFirstPending(page);
  await page.getByRole("button", { name: "Edit draft" }).click();
  const editor = page.getByTestId("editor");
  const original = await editor.inputValue();
  await editor.fill(`${original}\n\nReach me on WhatsApp +92 300 1234567 to discuss.`);
  await page.getByRole("button", { name: "Save edit and re-check" }).click();
  await expect(page.getByText(/re-checked by Compliance/)).toBeVisible();
  await expect(page.getByRole("alert").filter({ hasText: "Blocked" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Approve and submit/ })).toBeDisabled();
  await page.getByRole("button", { name: "Edit draft" }).click();
  await page.getByTestId("editor").fill(original);
  await page.getByRole("button", { name: "Save edit and re-check" }).click();
  await expect(page.getByRole("button", { name: /Approve and submit/ })).toBeEnabled();
});

test("degraded mode: when the marketplace is down the UI says so @mobile", async ({ page }) => {
  await login(page, "http://localhost:8101");
  await expect(page.getByTestId("degraded-banner")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("degraded-banner")).toContainText("Marketplace unavailable");
});
