import { expect, type Page, type TestInfo } from "@playwright/test";

/** Attach a screenshot of the page's current state to the test report. */
export async function attachScreenshot(
  page: Page,
  testInfo: TestInfo,
  name: string,
): Promise<void> {
  await testInfo.attach(name, {
    body: await page.screenshot(),
    contentType: "image/png",
  });
}

/**
 * Make the sidebar's organization switcher show ``orgName``. The actor owns the
 * automation through the new org, so the page can only show it from there; a
 * session that already is in that org is left alone.
 */
export async function selectOrganization(
  page: Page,
  orgName: string,
): Promise<void> {
  const switcher = page.getByRole("combobox").first();
  await expect(switcher).toBeVisible();
  if ((await switcher.getAttribute("title")) !== orgName) {
    await switcher.click();
    await page.getByRole("option", { name: orgName }).click();
  }
  await expect(switcher).toHaveAttribute("title", orgName);
}

/**
 * Open the automation's page in ``orgName`` and dispatch a run with **Run
 * now**, the way a user starts one.
 */
export async function dispatchWithRunNow(
  page: Page,
  testInfo: TestInfo,
  orgName: string,
  automation: { id: string; name: string },
): Promise<void> {
  await page.goto("canvas/automations");
  await selectOrganization(page, orgName);
  await page.getByRole("link", { name: automation.name }).first().click();
  await expect(page).toHaveURL(new RegExp(`/automations/${automation.id}`));
  await expect(page.getByTestId("automation-detail-header")).toContainText(
    automation.name,
  );
  await expect(page.getByRole("button", { name: "Run now" })).toBeVisible();
  await attachScreenshot(page, testInfo, `${automation.name}-before-dispatch`);
  await page.getByRole("button", { name: "Run now" }).click();
  await expect(page.getByText("Automation dispatched")).toBeVisible();
}
