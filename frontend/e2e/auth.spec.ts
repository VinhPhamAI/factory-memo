import { expect, test } from "@playwright/test";

// Runs against a real backend with PLAYWRIGHT_SUITE=auth; see frontend/README.md.
const password = "correct horse battery staple";

test("signup, login, me, refresh and logout; the last access token then fails", async ({
  page,
}) => {
  const username = `e2e-${Date.now().toString(36)}`;
  let lastAccess = "";
  page.on("request", (request) => {
    const header = request.headers().authorization;
    if (header?.startsWith("Bearer ")) lastAccess = header.slice(7);
  });

  await page.goto("/ask");
  await page
    .getByRole("button", { name: "Chưa có tài khoản? Đăng ký" })
    .click();
  await page.getByLabel("Tên đăng nhập").fill(username);
  await page.getByLabel("Tên hiển thị").fill("E2E User");
  await page.getByLabel("Mật khẩu").fill(password);
  await page.getByRole("button", { name: "Tạo tài khoản" }).click();
  await expect(page.getByRole("status")).toContainText("Đã tạo tài khoản");

  await page.getByLabel("Mật khẩu").fill(password);
  await page.getByRole("button", { name: "Đăng nhập", exact: true }).click();
  await expect(page.getByTestId("signed-in-user")).toContainText("E2E User");
  const cookie = (await page.context().cookies()).find(
    (item) => item.name === "refresh_token",
  );
  expect(cookie).toMatchObject({
    httpOnly: true,
    sameSite: "Strict",
    path: "/auth",
  });

  // A reload keeps no token in storage; the refresh cookie restores identity.
  await page.reload();
  await expect(page.getByTestId("signed-in-user")).toContainText("E2E User");
  const storage = await page.evaluate(
    () => localStorage.length + sessionStorage.length,
  );
  expect(storage).toBe(0);
  expect(lastAccess).not.toBe("");

  await page.getByTestId("sign-out").click();
  await expect(
    page.getByRole("button", { name: "Đăng nhập", exact: true }),
  ).toBeVisible();
  const me = await page.request.get("/auth/me", {
    headers: { Authorization: `Bearer ${lastAccess}` },
  });
  expect(me.status()).toBe(401);
});

test("two tabs share one session; a stale refresh leaves the winning cookie", async ({
  browser,
  playwright,
  baseURL,
}) => {
  const context = await browser.newContext();
  const username = `e2e-${Date.now().toString(36)}`;
  const api = await playwright.request.newContext({ baseURL });
  const signup = await api.post("/auth/signup", {
    data: { username, display_name: "Tab User", password },
  });
  expect(signup.status()).toBe(201);
  const refreshCookie = async () =>
    (await context.cookies()).find((item) => item.name === "refresh_token")
      ?.value;

  const first = await context.newPage();
  await first.goto("/ask");
  await first.getByLabel("Tên đăng nhập").fill(username);
  await first.getByLabel("Mật khẩu").fill(password);
  await first.getByRole("button", { name: "Đăng nhập", exact: true }).click();
  await expect(first.getByTestId("signed-in-user")).toContainText("Tab User");
  const stale = await refreshCookie();

  // Opening a second tab restores the session by rotating the shared cookie.
  const second = await context.newPage();
  await second.goto("/ask");
  await expect(second.getByTestId("signed-in-user")).toContainText("Tab User");
  const winner = await refreshCookie();
  expect(winner).not.toBe(stale);

  const rejected = await api.post("/auth/refresh", {
    headers: { Cookie: `refresh_token=${stale}` },
  });
  expect(rejected.status()).toBe(401);
  expect(rejected.headers()["set-cookie"]).toBeUndefined();
  expect(await refreshCookie()).toBe(winner);

  // Logout in one tab signs out the other.
  await first.getByTestId("sign-out").click();
  await expect(
    second.getByRole("button", { name: "Đăng nhập", exact: true }),
  ).toBeVisible();
  await api.dispose();
  await context.close();
});
