import { test } from "@playwright/test";
import { login, openFirstPending } from "./helpers";

// Not part of the required suite: captures screenshots for docs/images (run with --grep @screens).
test("capture screens @screens", async ({ page }) => {
  const shot = (n: string) => page.screenshot({ path: `../../docs/images/ui-${n}.png`, fullPage: true });
  await login(page);
  await shot("inbox");
  await openFirstPending(page);
  await page.getByTestId("evidence-letter").locator(".cite button").first().focus();
  await shot("studio");
  await page.goto("/inbox");
  await page.getByRole("button", { name: "Open" }).first().click();
  await page.getByRole("heading", { name: "Requirement → evidence" }).waitFor();
  await shot("job-detail");
  const url = page.url().replace("/jobs/", "/runs/");
  await page.goto(url);
  await page.getByRole("heading", { name: "Live progress" }).waitFor();
  await page.waitForTimeout(1500);
  await shot("run-viewer");
  await page.goto("/settings");
  await shot("settings");
});
