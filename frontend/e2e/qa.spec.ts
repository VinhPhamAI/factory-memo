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
    await page.getByRole("note").getByRole("link", { name: "Thư viện file", exact: true }).click();
    await expect(page.getByTestId("library-file")).toContainText("manual.pdf");
    const downloading = page.waitForEvent("download");
    await page.getByRole("button", { name: "Tải về manual.pdf", exact: true }).click();
    expect((await downloading).suggestedFilename()).toBe("manual.pdf");
  });
}

for (const width of [1365, 390]) {
  test(`conflict review compares evidence and supersedes an explicit survivor at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 950 });
    await mockBases(page);
    const record = (id: string, claim: string) => ({
      id,
      subject: "FX5-PLC9001",
      claim,
      type: "rule",
      status: "draft",
      context: {
        machine_id: "FX5-PLC9001",
        configuration: "CONFLICT-DEMO",
        condition: "Mất phản hồi nhưng kết nối vẫn được giữ",
        quotes: [{ turn_id: "T04", quote: claim }],
      },
      stated_by: "Chuyên gia",
      created_at: "2026-10-23T01:00:00Z",
      superseded_by: null,
      verify: null,
      consolidation: null,
      history: [],
      evidence_id: `source-${id}`,
      quote: claim,
    });
    const conflict = {
      id: "pair1",
      a_id: "record-a",
      b_id: "record-b",
      new_id: "record-b",
      status: "open",
      relation: "contradiction",
      aspect: "value",
      confidence: 0.95,
      reason:
        "Hai chuyên gia yêu cầu giá trị D100 khác nhau cho cùng PLC, cấu hình và điều kiện.",
      quotes: {},
      linked: true,
      confirmed_by_pair: true,
      review: [],
      stale: false,
      created_at: "2026-10-23T01:00:00Z",
      updated_at: "2026-10-23T01:00:00Z",
      a: record("record-a", "D100 bắt buộc bằng 1000 ms."),
      b: record("record-b", "D100 bắt buộc bằng 3000 ms."),
    };
    let resolved = false;
    await page.route("**/v1/knowledge-management/*/sources*", (route) =>
      route.fulfill({
        json: {
          sources: [],
          warnings: [],
          next_offset: null,
          qa_mode: "knowledge",
        },
      }),
    );
    await page.route(
      "**/v1/knowledge-management/*/conflicts**",
      async (route) => {
        if (route.request().method() === "POST") {
          expect(route.request().postDataJSON()).toEqual({
            decision: "supersede",
            note: "Đã đối chiếu hai nguồn",
            keep_id: "record-b",
          });
          resolved = true;
          await route.fulfill({ json: { ...conflict, status: "resolved" } });
        } else {
          await route.fulfill({
            json: {
              conflicts: resolved ? [] : [conflict],
              counts: resolved ? { resolved: 1 } : { open: 1 },
              enabled: true,
              offset: 0,
              limit: 20,
              next_offset: null,
            },
          });
        }
      },
    );
    await page.goto("/knowledge");
    await page.getByTestId("management-kb").selectOption("industrial_qa");
    if (width < 860) await page.getByTestId("open-menu").click();
    const knowledgeMenu = page.getByRole("button", { name: "Quản lý tri thức", exact: true });
    await expect(knowledgeMenu).toHaveAttribute("aria-expanded", "true");
    await knowledgeMenu.click();
    await expect(page.getByRole("link", { name: "Mâu thuẫn", exact: true })).toBeHidden();
    await knowledgeMenu.click();
    await expect(page.getByRole("link", { name: "Nguồn dữ liệu", exact: true })).toHaveAttribute("aria-current", "page");
    await page.getByRole("link", { name: "Mâu thuẫn", exact: true }).click();
    await expect(page).toHaveURL(/tab=conflicts/);
    if (width < 860) await expect(page.getByRole("dialog")).toBeHidden();
    await expect(
      page.getByRole("heading", { name: "Review mâu thuẫn" }),
    ).toBeVisible();
    await expect(page.getByRole("region", { name: "Bản ghi A" })).toContainText(
      "1000 ms",
    );
    await expect(page.getByRole("region", { name: "Bản ghi B" })).toContainText(
      "3000 ms",
    );
    await page
      .getByRole("textbox", { name: "Ghi chú quyết định" })
      .fill("Đã đối chiếu hai nguồn");
    await page.getByTestId("knowledge-management").evaluate((element) => {
      element.scrollTop = 0;
    });
    await page.screenshot({
      path: `/private/tmp/voice-agent-conflict-${width}.png`,
      fullPage: true,
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await page.getByRole("button", { name: "Thay thế một bản ghi…" }).click();
    const dialog = page.getByRole("dialog");
    await expect(
      dialog.getByRole("button", { name: "Lưu quyết định thay thế" }),
    ).toBeDisabled();
    await dialog.getByRole("radio", { name: /Bản ghi B/ }).check();
    await dialog
      .getByRole("button", { name: "Lưu quyết định thay thế" })
      .click();
    await expect(dialog).not.toBeVisible();
    await expect(page.getByRole("status")).toContainText(
      "Thay thế bản ghi: đã lưu quyết định.",
    );
    await expect(
      page.getByText(/Chưa có cặp nào ở trạng thái này/),
    ).toBeVisible();
  });
}
