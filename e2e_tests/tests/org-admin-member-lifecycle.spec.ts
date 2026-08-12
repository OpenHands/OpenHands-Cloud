import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import fs from "fs";
import path from "path";

const AUTH_STATE = path.resolve(import.meta.dirname, "../fixtures/auth.json");

test.use({ storageState: AUTH_STATE });

type Organization = {
  id: string;
  is_personal?: boolean;
};

type Member = {
  user_id: string;
  email: string;
  role: "member" | "admin" | "owner";
};

async function requireOwnerOrganization(page: Page): Promise<Organization> {
  if (!fs.existsSync(AUTH_STATE)) {
    throw new Error(
      `Missing owner authentication fixture: ${AUTH_STATE}. Create e2e_tests/fixtures/auth.json for an organization owner before running this test.`,
    );
  }

  const organizationsResponse = await page.request.get("/api/organizations");
  expect(organizationsResponse.ok()).toBe(true);
  const organizations = (await organizationsResponse.json()) as {
    items: Organization[];
    current_org_id: string | null;
  };
  const organization = organizations.items.find(
    ({ id }) => id === organizations.current_org_id,
  );
  if (!organization || organization.is_personal) {
    throw new Error(
      "The owner auth fixture must select a non-personal organization.",
    );
  }

  const meResponse = await page.request.get(
    `/api/organizations/${organization.id}/me`,
    { headers: { "X-Org-Id": organization.id } },
  );
  expect(meResponse.ok()).toBe(true);
  const me = (await meResponse.json()) as { role?: string };
  if (me.role !== "owner") {
    throw new Error(
      `This lifecycle requires an organization owner so role changes can always be restored; received role "${me.role ?? "unknown"}".`,
    );
  }

  return organization;
}

async function removeMemberIfPresent(
  page: Page,
  organizationId: string,
  email: string,
): Promise<void> {
  const headers = { "X-Org-Id": organizationId };
  const response = await page.request.get(
    `/api/organizations/${organizationId}/members?limit=100&email=${encodeURIComponent(email)}`,
    { headers },
  );
  if (!response.ok()) return;

  const body = (await response.json()) as { items: Member[] };
  const member = body.items.find((item) => item.email === email);
  if (!member) return;

  if (member.role !== "member") {
    await page.request.patch(
      `/api/organizations/${organizationId}/members/${member.user_id}`,
      { data: { role: "member" }, headers },
    );
  }
  await page.request.delete(
    `/api/organizations/${organizationId}/members/${member.user_id}`,
    { headers },
  );
}

async function revokeInvitationIfPresent(
  page: Page,
  organizationId: string,
  email: string,
): Promise<void> {
  const headers = { "X-Org-Id": organizationId };
  const response = await page.request.get(
    `/api/organizations/${organizationId}/members/invite`,
    { headers },
  );
  if (!response.ok()) return;

  const body = (await response.json()) as {
    items: { id: number; email: string }[];
  };
  await Promise.all(
    body.items
      .filter((invitation) => invitation.email === email)
      .map((invitation) =>
        page.request.delete(
          `/api/organizations/${organizationId}/members/invite/${invitation.id}`,
          { headers },
        ),
      ),
  );
}

test("organization owner can add a user, change their role, and remove them", async ({
  browser,
  page,
  baseURL,
}) => {
  const organization = await requireOwnerOrganization(page);
  const secondaryAuthState = process.env.SECONDARY_AUTH_STATE;
  const memberEmail = process.env.TEST_MEMBER_EMAIL;

  if (!secondaryAuthState) {
    throw new Error(
      "SECONDARY_AUTH_STATE must point to a Playwright storage-state file for the invited user.",
    );
  }
  if (!fs.existsSync(secondaryAuthState)) {
    throw new Error(
      `SECONDARY_AUTH_STATE does not exist: ${secondaryAuthState}`,
    );
  }
  if (!memberEmail) {
    throw new Error("TEST_MEMBER_EMAIL must identify the secondary user.");
  }
  if (!baseURL) {
    throw new Error(
      "Playwright baseURL is required for the secondary context.",
    );
  }

  let secondaryContext: BrowserContext | undefined;
  try {
    await removeMemberIfPresent(page, organization.id, memberEmail);
    await revokeInvitationIfPresent(page, organization.id, memberEmail);

    await page.goto("/settings/org-members");
    await expect(
      page.getByTestId("manage-organization-members-settings"),
    ).toBeVisible();
    await page.getByRole("button", { name: /invite/i }).click();

    const inviteModal = page.getByTestId("invite-modal");
    const emailInput = inviteModal.getByPlaceholder(/enter email addresses/i);
    await emailInput.fill(memberEmail);
    await emailInput.press("Enter");

    const invitationResponsePromise = page.waitForResponse(
      (response) =>
        response
          .url()
          .includes(`/api/organizations/${organization.id}/members/invite`) &&
        response.request().method() === "POST",
    );
    await inviteModal.getByRole("button", { name: "Add" }).click();
    const invitationResponse = await invitationResponsePromise;
    expect(invitationResponse.status()).toBe(201);
    const invitationBody = (await invitationResponse.json()) as {
      successful: { email: string; invite_url?: string | null }[];
      failed: { email: string; error: string }[];
    };
    expect(invitationBody.failed).toEqual([]);
    const invitation = invitationBody.successful.find(
      ({ email }) => email === memberEmail,
    );
    if (!invitation?.invite_url) {
      throw new Error(
        "Invitation response did not include invite_url; the owner fixture must have invitation-link access.",
      );
    }
    const token = new URL(invitation.invite_url).searchParams.get("token");
    if (!token) {
      throw new Error("Invitation URL did not contain an acceptance token.");
    }

    secondaryContext = await browser.newContext({
      baseURL,
      storageState: secondaryAuthState,
    });
    const acceptResponse = await secondaryContext.request.post(
      "/api/organizations/members/invite/accept",
      { data: { token } },
    );
    expect(acceptResponse.ok()).toBe(true);

    await page.reload();
    await page.getByTestId("email-filter-input").fill(memberEmail);
    const memberRow = page.getByTestId("member-item").filter({
      hasText: memberEmail,
    });
    await expect(memberRow).toBeVisible();

    await memberRow.getByText("member", { exact: true }).click();
    await page.getByTestId("admin-option").click();
    await page.getByTestId("confirm-button").click();
    await expect(memberRow.getByText("admin", { exact: true })).toBeVisible();

    await memberRow.getByText("admin", { exact: true }).click();
    await page.getByTestId("member-option").click();
    await page.getByTestId("confirm-button").click();
    await expect(memberRow.getByText("member", { exact: true })).toBeVisible();

    await memberRow.getByText("member", { exact: true }).click();
    await page.getByTestId("remove-option").click();
    await page.getByTestId("confirm-button").click();
    await expect(memberRow).toHaveCount(0);
  } finally {
    await secondaryContext?.close();
    await removeMemberIfPresent(page, organization.id, memberEmail);
    await revokeInvitationIfPresent(page, organization.id, memberEmail);
  }
});
