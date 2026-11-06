import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

// Runs against a real backend with PLAYWRIGHT_SUITE=inquiries; see frontend/README.md.
// The backend must use a disposable scratch database, and INQUIRY_E2E_DATABASE_URL must
// name that same database. The expert role is granted there because signup only creates
// employees. The standalone agent is not running, so the draft is created through
// POST /v1/inquiries; the draft-from-chat route is covered by backend tests.
const password = "correct horse battery staple";
const pdf = Buffer.from("%PDF-1.7\n%e2e\n");
const fileName = "Hướng dẫn bơm.pdf";
const title = "Bơm nước làm mát rung";
const employeeName = "Nhân viên Bơm";
const expertName = "Chuyên gia Bơm";
const scratchUrl = process.env.INQUIRY_E2E_DATABASE_URL ?? "";

/** Refuse any database that is not clearly a disposable scratch database. */
function assertScratchDatabase(url: string): void {
  const name = /\/([^/?]+)(\?|$)/.exec(url)?.[1] ?? "";
  if (!name.includes("scratch") || name === "voice_agent_app" || name === "voice_agent")
    throw new Error(`INQUIRY_E2E_DATABASE_URL must name a scratch database, got "${name}"`);
}

/**
 * Set role = expert by username. The UPDATE must match one row, which also proves the
 * backend under test writes to this database.
 */
function promoteToExpert(username: string): void {
  const script = [
    "import sys, psycopg",
    "url, username = sys.argv[1], sys.argv[2]",
    "with psycopg.connect(url.replace('postgresql+psycopg://', 'postgresql://', 1)) as conn:",
    "    cur = conn.execute(\"UPDATE users SET role = 'expert' WHERE username = %s AND is_active RETURNING id\", (username,))",
    "    if cur.rowcount != 1: sys.exit('expert account not found in the scratch database')",
  ].join("\n");
  // Playwright runs from frontend/, so the repository root is one level up.
  execFileSync("uv", ["run", "python", "-c", script, scratchUrl, username], {
    cwd: path.resolve(".."),
    stdio: "pipe",
  });
}

async function signUp(page: Page, displayName: string): Promise<string> {
  const username = `e2e-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
  const signup = await page.request.post("/auth/signup", {
    data: { username, display_name: displayName, password },
  });
  expect(signup.status()).toBe(201);
  return username;
}

async function signIn(page: Page, username: string, displayName: string, route: string) {
  await page.goto(route);
  await page.getByLabel("Tên đăng nhập").fill(username);
  await page.getByLabel("Mật khẩu").fill(password);
  await page.getByRole("button", { name: "Đăng nhập", exact: true }).click();
  await expect(page.getByTestId("signed-in-user")).toContainText(displayName);
}

/** Bearer token for API calls made outside the page's own session. */
async function accessToken(page: Page, username: string): Promise<string> {
  const login = await page.request.post("/auth/login", { data: { username, password } });
  expect(login.status()).toBe(200);
  return ((await login.json()) as { access_token: string }).access_token;
}

test("employee submits a file-backed inquiry; expert asks, employee replies, expert accepts; evidence outlives the library file", async ({
  browser,
}) => {
  expect(scratchUrl, "INQUIRY_E2E_DATABASE_URL must name the scratch database").not.toBe("");
  assertScratchDatabase(scratchUrl);

  const employeeContext = await browser.newContext();
  const expertContext = await browser.newContext();
  try {
    const employeePage = await employeeContext.newPage();
    const expertPage = await expertContext.newPage();

    const employee = await signUp(employeePage, employeeName);
    const expert = await signUp(expertPage, expertName);
    promoteToExpert(expert);

    // Employee adds the evidence file to the library.
    await signIn(employeePage, employee, employeeName, "/files");
    // Uploads start only after the page has loaded the default knowledge base.
    await expect(employeePage.getByTestId("files-upload")).toBeEnabled();
    await employeePage.getByTestId("files-input").setInputFiles({
      name: fileName,
      mimeType: "application/pdf",
      buffer: pdf,
    });
    await expect(
      employeePage.getByTestId("library-file").filter({ hasText: fileName }),
    ).toHaveCount(1);

    // Conversation and draft are created through the API.
    const token = await accessToken(employeePage, employee);
    const auth = { Authorization: `Bearer ${token}` };
    const conversation = await employeePage.request.post("/v1/conversations", {
      headers: auth,
      data: { title },
    });
    expect(conversation.status()).toBe(201);
    const draft = await employeePage.request.post("/v1/inquiries", {
      headers: auth,
      data: { conversation_id: ((await conversation.json()) as { id: string }).id, title },
    });
    expect(draft.status()).toBe(201);
    const inquiryPath = `/inquiries/${((await draft.json()) as { id: string }).id}`;

    // Employee attaches the file, picks the expert and submits.
    await employeePage.goto(inquiryPath);
    await expect(employeePage.getByTestId("inquiry-status")).toHaveText("Bản nháp");
    await employeePage.getByRole("button", { name: "Đính kèm file" }).click();
    await employeePage
      .getByTestId("file-picker")
      .getByRole("button", { name: fileName })
      .click();
    await expect(
      employeePage.getByTestId("evidence-item").filter({ hasText: fileName }),
    ).toBeVisible();
    await employeePage.getByLabel("Tình huống").fill("Bơm nước làm mát rung mạnh sau khi thay ron.");
    const expertSelect = employeePage.getByRole("combobox", { name: "Chuyên gia" });
    await expect(expertSelect.locator("option", { hasText: expertName })).toHaveCount(1);
    await expertSelect.selectOption({ label: expertName });
    await employeePage.getByRole("button", { name: "Gửi", exact: true }).click();
    await expect(employeePage.getByTestId("inquiry-status")).toHaveText("Đang chờ chuyên gia");

    // Expert finds the request under "Được giao" and asks for clarification.
    await signIn(expertPage, expert, expertName, "/inquiries");
    await expertPage.getByTestId("tab-assigned").click();
    await expertPage
      .getByTestId("inquiry-row")
      .filter({ hasText: title })
      .getByRole("link")
      .click();
    await expect(expertPage.getByTestId("inquiry-status")).toHaveText("Đang chờ chuyên gia");
    await expect(
      expertPage.getByTestId("evidence-item").filter({ hasText: fileName }),
    ).toBeVisible();
    await expertPage.getByRole("button", { name: "Hỏi thêm", exact: true }).click();
    await expertPage
      .getByRole("textbox", { name: "Câu hỏi cho người gửi" })
      .fill("Ron đã thay đúng loại chưa?");
    await expertPage.getByRole("button", { name: "Gửi hỏi thêm", exact: true }).click();
    await expect(expertPage.getByTestId("inquiry-status")).toHaveText("Cần bổ sung");

    // Employee answers the question.
    await employeePage.goto(inquiryPath);
    await expect(employeePage.getByTestId("inquiry-status")).toHaveText("Cần bổ sung");
    await employeePage.getByRole("button", { name: "Trả lời", exact: true }).click();
    await employeePage
      .getByRole("textbox", { name: "Nội dung trả lời" })
      .fill("Đã thay đúng loại ron 45 mm.");
    await employeePage.getByRole("button", { name: "Gửi trả lời", exact: true }).click();
    await expect(employeePage.getByTestId("inquiry-status")).toHaveText("Đang chờ chuyên gia");

    // Expert accepts the answer; the timeline shows all three events in order.
    await expertPage.reload();
    await expect(expertPage.getByTestId("inquiry-status")).toHaveText("Đang chờ chuyên gia");
    await expertPage.getByRole("button", { name: "Chấp nhận", exact: true }).click();
    await expect(expertPage.getByTestId("inquiry-status")).toHaveText("Đã trả lời");
    const events = expertPage.getByTestId("timeline-event");
    await expect(events).toHaveCount(3);
    await expect(events.nth(0)).toContainText("Hỏi thêm");
    await expect(events.nth(0)).toContainText(expertName);
    await expect(events.nth(0)).toContainText("Ron đã thay đúng loại chưa?");
    await expect(events.nth(1)).toContainText("Trả lời");
    await expect(events.nth(1)).toContainText(employeeName);
    await expect(events.nth(1)).toContainText("Đã thay đúng loại ron 45 mm.");
    await expect(events.nth(2)).toContainText("Chấp nhận");
    await expect(events.nth(2)).toContainText(expertName);

    // Employee removes the file from the library; the inquiry keeps its copy.
    // The Library page only removes KB sources, so the file is deleted through the API.
    // A fresh token: the one from the start of the test may have expired.
    const fresh = { Authorization: `Bearer ${await accessToken(employeePage, employee)}` };
    const listed = await employeePage.request.get("/v1/files", { headers: fresh });
    const { files } = (await listed.json()) as { files: { id: string; filename: string }[] };
    const libraryFile = files.find((file) => file.filename === fileName);
    expect(libraryFile).toBeDefined();
    const removed = await employeePage.request.delete(`/v1/files/${libraryFile!.id}`, {
      headers: fresh,
    });
    expect(removed.status()).toBe(204);
    await employeePage.goto("/files");
    await expect(employeePage.getByTestId("files-empty")).toBeVisible();

    // Expert still downloads the evidence and gets the original bytes.
    await expertPage.goto(inquiryPath);
    await expect(expertPage.getByTestId("inquiry-status")).toHaveText("Đã trả lời");
    const download = expertPage.waitForEvent("download");
    await expertPage
      .getByTestId("evidence-item")
      .filter({ hasText: fileName })
      .getByRole("button", { name: "Tải về" })
      .click();
    const saved = await download;
    expect(saved.suggestedFilename()).toBe(fileName);
    const localPath = await saved.path();
    expect(localPath).not.toBeNull();
    expect(readFileSync(localPath!)).toEqual(pdf);
  } finally {
    await employeeContext.close();
    await expertContext.close();
  }
});
