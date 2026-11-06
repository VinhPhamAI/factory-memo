import { expect, test, type Page } from "@playwright/test";

// Runs against a real backend with PLAYWRIGHT_SUITE=files; see frontend/README.md.
const password = "correct horse battery staple";
const pdf = Buffer.from("%PDF-1.7\n%e2e\n");

async function signUpAndIn(page: Page, displayName: string) {
  const username = `e2e-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
  const signup = await page.request.post("/auth/signup", {
    data: { username, display_name: displayName, password },
  });
  expect(signup.status()).toBe(201);
  await page.goto("/files");
  await page.getByLabel("Tên đăng nhập").fill(username);
  await page.getByLabel("Mật khẩu").fill(password);
  await page.getByRole("button", { name: "Đăng nhập", exact: true }).click();
  await expect(page.getByTestId("signed-in-user")).toContainText(displayName);
}

test("upload and list own files; wrong types show an error", async ({
  browser,
}) => {
  const owner = await (await browser.newContext()).newPage();
  await signUpAndIn(owner, "File Owner");
  await expect(owner.getByTestId("files-empty")).toBeVisible();
  await expect(owner.getByTestId("files-upload")).toBeEnabled();

  await owner.getByTestId("files-input").setInputFiles([
    { name: "Hướng dẫn máy.pdf", mimeType: "application/pdf", buffer: pdf },
    {
      name: "renamed.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("MZ not a pdf"),
    },
  ]);
  // Results appear as popups that hide themselves without a reload.
  const toasts = owner.getByTestId("toast");
  await expect(
    toasts.filter({ hasText: "Đã tải lên và đưa “Hướng dẫn máy.pdf” vào hàng đợi xử lý." }),
  ).toBeVisible();
  await expect(
    toasts.filter({ hasText: "“renamed.pdf”: Chỉ nhận PDF" }),
  ).toBeVisible();
  await expect(toasts).toHaveCount(0, { timeout: 10_000 });
  const row = owner.getByTestId("library-file");
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("Hướng dẫn máy.pdf");

  // The list survives a reload: it comes from the server, not the page state.
  await owner.reload();
  await expect(owner.getByTestId("library-file")).toHaveCount(1);

  await owner.getByRole("button", { name: "Tùy chọn Hướng dẫn máy.pdf" }).click();
  await expect(owner.getByRole("menuitem", { name: "Open file" })).toBeVisible();
  await expect(owner.getByRole("menuitem", { name: "Knowledge State" })).toBeVisible();
  await owner.keyboard.press("Escape");

  // Another user does not see the file.
  const other = await (await browser.newContext()).newPage();
  await signUpAndIn(other, "Other User");
  await expect(other.getByTestId("files-empty")).toBeVisible();
});
