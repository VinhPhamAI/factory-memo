import { expect, test, type Page } from "@playwright/test";

const bases = [
  { name: "general", description: "Tri thức chung từ hội thoại" },
  { name: "industrial_qa", description: "Tri thức chuyên gia và tài liệu" },
];

// Live screens need a signed-in session; these specs mock the backend.
test.beforeEach(async ({ page }) => {
  await page.route("**/auth/refresh", (route) =>
    route.fulfill({
      json: { access_token: "e2e", token_type: "bearer", expires_in: 900 },
    }),
  );
  await page.route("**/auth/me", (route) =>
    route.fulfill({
      json: {
        id: "e2e",
        username: "e2e",
        display_name: "E2E",
        role: "employee",
      },
    }),
  );
});

async function mockBases(page: Page) {
  await page.route("**/v1/knowledge-agent/knowledge-bases", (route) =>
    route.fulfill({ json: { knowledge_bases: bases } }),
  );
}

test("administrator manages knowledge bases and sets the Library default", async ({ page }) => {
  await page.route("**/auth/me", (route) => route.fulfill({ json: { id: "admin", username: "admin", display_name: "Admin", role: "admin" } }));
  const items = [
    { name: "knowledge_base_seed1", title: "Factory manuals", description: "Tài liệu nhà máy", is_default: true },
    { name: "maintenance", title: "Maintenance", description: "Bảo trì thiết bị", is_default: false },
  ];
  await page.route("**/v1/knowledge-bases**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/default")) {
      items.forEach((item) => { item.is_default = item.name === "maintenance"; });
      return route.fulfill({ status: 204 });
    }
    if (path === "/v1/knowledge-bases") return route.fulfill({ json: { knowledge_bases: items } });
    const item = items.find((entry) => path.endsWith(`/${entry.name}`));
    return route.fulfill({ json: { ...item, template: "general", created_by: "admin", created_at: "2026-11-06T00:00:00Z", updated_at: "2026-11-06T00:00:00Z", source_counts: { received: 2 }, chunk_counts: {} } });
  });
  await page.goto("/admin/knowledge-bases");
  await expect(page.getByTestId("nav-admin-knowledge")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Knowledge bases", exact: true })).toBeVisible();
  await page.getByRole("button", { name: /Maintenance/ }).click();
  await page.getByRole("button", { name: "Đặt làm mặc định" }).click();
  await expect(page.getByText("Đang dùng cho Library").locator(".." )).toContainText("Maintenance");
  await expect(page.getByRole("button", { name: "Xóa knowledge base" })).toBeDisabled();
  await page.getByRole("button", { name: "Tạo knowledge base", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page.getByRole("heading", { name: "Knowledge bases", exact: true })).toBeVisible();
});

test("employee cannot open the administration page", async ({ page }) => {
  await page.route("**/v1/knowledge-bases", (route) => route.fulfill({ json: { knowledge_bases: [] } }));
  await page.route("**/v1/files", (route) => route.fulfill({ json: { files: [] } }));
  await page.goto("/admin/knowledge-bases");
  await expect(page).toHaveURL(/\/files$/);
  await expect(page.getByTestId("nav-admin-knowledge")).toHaveCount(0);
});

test("library grid previews files and filters by name", async ({ page }) => {
  await page.route("**/v1/knowledge-agent/knowledge-bases", (route) =>
    route.fulfill({ json: { knowledge_bases: [{ name: "knowledge_base_seed1", title: "Default", description: "" }] } }),
  );
  await page.route("**/v1/knowledge-bases", (route) => route.fulfill({ json: { knowledge_bases: [{ name: "knowledge_base_seed1", title: "Default", description: "", is_default: true }] } }));
  await page.route("**/v1/files", (route) => route.fulfill({ json: { files: [
    { id: "doc1", filename: "manual.docx", content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", size_bytes: 120, created_at: "2026-11-06T00:00:00Z", sha256: "a" },
    { id: "doc2", filename: "pump.pdf", content_type: "application/pdf", size_bytes: 120, created_at: "2026-11-06T00:00:00Z", sha256: "b" },
  ] } }));
  await page.route("**/v1/files/*/preview", (route) => route.fulfill({ contentType: "text/plain", body: "Preview content" }));
  await page.route("**/v1/knowledge-management/knowledge_base_seed1/sources*", (route) => route.fulfill({ json: { sources: [{ id: "s1", kb: "knowledge_base_seed1", kind: "upload", name: "manual.docx", file_sha256: "a", stage: "raw", review: "none", processing: { status: "queued" } }], warnings: [], next_offset: null, qa_mode: "knowledge" } }));
  await page.goto("/files");
  await expect(page.getByRole("heading", { name: "Library" })).toBeVisible();
  await expect(page.getByTestId("library-file")).toHaveCount(2);
  await expect(page.getByText("Preview content").first()).toBeVisible();
  const card = page.getByTestId("library-file").first();
  await card.hover();
  await expect(card.getByRole("button", { name: "Tùy chọn manual.docx" })).toBeVisible();
  await card.getByRole("button", { name: "Tùy chọn manual.docx" }).click();
  await page.getByRole("menuitem", { name: "Knowledge State" }).click();
  await expect(page.getByRole("dialog")).toContainText("Đang chờ xử lý");
  await page.keyboard.press("Escape");
  await card.getByRole("button", { name: "Chọn manual.docx", exact: true }).click();
  await expect(page.getByText("Đã chọn 1 file")).toBeVisible();
  await page.getByRole("searchbox", { name: "Search library" }).fill("pump");
  await expect(page.getByTestId("library-file")).toHaveCount(1);
  await expect(page.getByTestId("library-file")).toContainText("pump.pdf");
});

const agentEvent = (type: string, data: Record<string, unknown>) =>
  `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;

/** Backend conversation API held in memory for the agent chat specs. */
async function mockConversations(
  page: Page,
  stream: (message: string) => string,
) {
  const chats: {
    id: string;
    title: string;
    created_at: string;
    updated_at: string;
  }[] = [];
  const saved: Record<string, { role: string; text: string }[]> = {};
  await page.route("**/v1/conversations**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/v1/conversations" && request.method() === "GET")
      return route.fulfill({ json: { conversations: chats } });
    if (path === "/v1/conversations" && request.method() === "POST") {
      const now = new Date().toISOString();
      const chat = {
        id: `chat-${chats.length + 1}`,
        title: request.postDataJSON().title,
        created_at: now,
        updated_at: now,
      };
      chats.unshift(chat);
      saved[chat.id] = [];
      return route.fulfill({ status: 201, json: chat });
    }
    const [, id, action] =
      path.match(/^\/v1\/conversations\/([^/]+)\/?(\w*)$/) ?? [];
    if (action === "history")
      return route.fulfill({
        json: {
          conversation_id: id,
          messages: saved[id] ?? [],
          truncated: false,
        },
      });
    if (action === "stream") {
      const message = request.postDataJSON().message;
      const body = stream(message);
      saved[id].push({ role: "user", text: message });
      const complete = body.match(/"answer":"([^"]*)"/);
      if (complete) saved[id].push({ role: "assistant", text: complete[1] });
      return route.fulfill({ contentType: "text/event-stream", body });
    }
    if (request.method() === "DELETE") {
      chats.splice(
        chats.findIndex((chat) => chat.id === id),
        1,
      );
      return route.fulfill({ status: 204 });
    }
    return route.fulfill({
      status: 404,
      json: { detail: "Conversation not found" },
    });
  });
}

test("agent chat creates a conversation, replaces provisional text and reopens saved history", async ({
  page,
}) => {
  await mockConversations(
    page,
    () =>
      agentEvent("status", { sequence: 1, state: "generating" }) +
      agentEvent("text", { sequence: 2, delta: "Bản nháp" }) +
      agentEvent("complete", {
        sequence: 3,
        finish_reason: "stop",
        answer: "Kiểm tra cảm biến áp suất trước.",
      }),
  );
  await page.goto("/ask");
  await page.getByTestId("chat-input").fill("Máy ép số 3 báo lỗi E12");
  await page.getByTestId("chat-send").click();
  const answer = page.getByTestId("chat-answer");
  await expect(answer).toHaveAttribute("data-status", "saved");
  await expect(answer).toHaveText(/Kiểm tra cảm biến áp suất trước\./);
  await expect(answer).not.toHaveText(/Bản nháp/);
  await expect(page).toHaveURL(/\/ask\/chat-1$/);

  await page.reload();
  await expect(page.getByTestId("chat-question")).toHaveText(
    "Máy ép số 3 báo lỗi E12",
  );
  await expect(page.getByTestId("chat-answer")).toHaveText(
    /Kiểm tra cảm biến áp suất trước\./,
  );
  await expect(page.getByTestId("chat-title")).toHaveText(
    "Máy ép số 3 báo lỗi E12",
  );
  await expect(
    page.getByRole("link", { name: "Máy ép số 3 báo lỗi E12" }),
  ).toHaveAttribute("aria-current", "page");

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByTestId("conversation-delete").click();
  await expect(page).toHaveURL(/\/ask$/);
  await expect(
    page.getByRole("link", { name: "Máy ép số 3 báo lỗi E12" }),
  ).toHaveCount(0);
});

test("agent chat shows errors and unconfirmed answers without sending again", async ({
  page,
}) => {
  let streams = 0;
  await mockConversations(page, (message) => {
    streams += 1;
    return message === "error"
      ? agentEvent("text", { delta: "partial" }) +
          agentEvent("error", {
            code: "provider_error",
            message: "The model provider failed.",
            retryable: true,
          })
      : agentEvent("text", { delta: "partial" });
  });
  await page.goto("/ask");
  await page.getByTestId("chat-input").fill("error");
  await page.getByTestId("chat-send").click();
  const answers = page.getByTestId("chat-answer");
  await expect(answers.last()).toHaveAttribute("data-status", "failed");
  await expect(answers.last()).toHaveText(/The model provider failed\./);

  await page.getByTestId("chat-input").fill("eof");
  await page.getByTestId("chat-send").click();
  await expect(answers.last()).toHaveAttribute("data-status", "unconfirmed");
  await expect(
    page.getByRole("button", { name: "Tải lại cuộc trò chuyện" }),
  ).toBeVisible();
  expect(streams).toBe(2);
});

for (const auto of [false, true]) {
  test(`capture upload uses library bytes with Auto=${auto}`, async ({ page }) => {
    await mockBases(page);
    const content = Buffer.from("%PDF-1.7 original library bytes");
    const file = {
      id: "019a5100-0000-7000-8000-000000000001",
      filename: "manual.pdf", content_type: "application/pdf", size_bytes: content.length,
      sha256: "a".repeat(64), created_at: "2026-11-05T00:00:00Z",
    };
    let uploads = 0;
    let attachments = 0;
    await page.route("**/v1/files", async (route) => {
      if (route.request().method() === "POST") {
        uploads++;
        await route.fulfill({ status: 201, json: file });
      } else await route.fulfill({ json: { files: uploads ? [file] : [] } });
    });
    await page.route(`**/v1/files/${file.id}/content`, (route) => route.fulfill({
      body: content, contentType: "application/pdf",
    }));
    await page.route("**/v1/documents/general/files", async (route) => {
      attachments++;
      expect(route.request().postDataJSON()).toEqual({
        file_id: file.id, extract_mode: auto ? "knowledge" : null,
      });
      await route.fulfill({ status: 201, json: {
        id: "019a5100-0000-7000-8000-000000000002", kb: "general", status: "stored",
        ...(auto ? { job: { id: "job1", stage: "convert", status: "queued" } } : {}),
      } });
    });
    await page.goto("/capture");
    await page.getByTestId("mode-documents").click();
    await page.getByTestId("capture-kb").selectOption("general");
    if (auto) await page.getByTestId("processing-auto").click();
    await page.getByTestId("document-files").setInputFiles({
      name: "manual.pdf", mimeType: "application/pdf", buffer: content,
    });
    await page.getByTestId("document-upload").click();
    await expect(page.getByText(/Đã lưu vào general/)).toBeVisible();
    expect(uploads).toBe(1);
    expect(attachments).toBe(1);
    if (auto) await expect(page.getByTestId("upload-job")).toContainText("Đã xếp hàng");
    await page.getByRole("note").getByRole("link", { name: "Quản lý tài liệu", exact: true }).click();
    await expect(page.getByTestId("library-file")).toContainText("manual.pdf");
    const downloading = page.waitForEvent("download");
    await page.getByRole("button", { name: "Tải về manual.pdf", exact: true }).click();
    expect((await downloading).suggestedFilename()).toBe("manual.pdf");
  });
}

for (const width of [1365, 390]) {
  test(`removed knowledge pages are absent at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 950 });
    await mockBases(page);
    await page.route("**/v1/files", (route) => route.fulfill({ json: { files: [] } }));
    await page.goto("/knowledge?tab=conflicts");
    await expect(page).toHaveURL(/\/files$/);
    if (width < 860) await page.getByTestId("open-menu").click();
    await expect(page.getByRole("link", { name: "Quản lý tài liệu", exact: true })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("button", { name: "Quản lý tri thức", exact: true })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Knowledge base", exact: true })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Nguồn dữ liệu", exact: true })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Mâu thuẫn", exact: true })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Tìm kiếm", exact: true })).toHaveCount(0);
  });
}
