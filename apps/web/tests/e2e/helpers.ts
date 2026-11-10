import { expect, type Page } from "@playwright/test";

export async function login(page: Page, base = "") {
  await page.goto(`${base}/login`);
  await page.getByLabel("Username").fill("reviewer");
  await page.getByLabel("Password").fill("reviewer-pass");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Job inbox" })).toBeVisible();
}

export async function openFirstPending(page: Page) {
  await page.getByRole("button", { name: "Review proposal" }).first().click();
  await expect(page.getByRole("heading", { name: "Proposal studio" })).toBeVisible();
}
