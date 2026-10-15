import { expect, test, type Page } from "@playwright/test";

const bases = [
  { name: "maintenance", description: "Tra cứu các ca bảo trì" },
  { name: "industrial_qa", description: "Tri thức chuyên gia và tài liệu" },
];
const sse = (type: string, data = {}) =>
  `event: ${type}\ndata: ${JSON.stringify({ version: 1, type, data })}\n\n`;

async function mockBases(page: Page) {
  await page.route("**/v1/knowledge-agent/knowledge-bases", (route) =>
    route.fulfill({ json: { knowledge_bases: bases } }),
  );
}

test("case sources render structured rows, pending outcomes and exact turn quotes; legacy claims still open as cards", async ({
  page,
}) => {
  await mockBases(page);
  const card = {
    context: "Line 5, machining cell A, MR-JE-AX205; cabinet temp around 42°C.",
    observation:
      "AL. 17.4 occurs two or three times a shift, only at high spindle load.",
    reasoning: "The nearby welder is suspected, not confirmed.",
    action:
      "Planned checks: inspect 24V routing.\nReport back before ordering parts.",
    outcome: "Pending: report back before ordering any parts.",
    evidence: [
      { turn_id: "T03", quote: "Report back before ordering any parts." },
    ],
  };
  const claim = `${card.context}\nObservation: ${card.observation}\nReasoning: ${card.reasoning}\nAction: ${card.action}\nOutcome: ${card.outcome}`;
  await page.route("**/v1/knowledge-agent/*/stream", (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body:
        sse("answer.delta", {
          text: "Ca hiện tại [K:rich], ca đã lưu [K:legacy].",
        }) +
        sse("answer.citations", {
          citations: [
            {
              knowledge_id: "rich",
              subject: "MR-JE-AX205",
              claim: "Fallback plain text",
              type: "case",
              status: "draft",
              evidence_id: "conversation-source",
              scope: {
                machine_id: "MR-JE-AX205",
                line_id: "Line 5",
                condition: "High spindle load",
              },
              case_card: card,
            },
            {
              knowledge_id: "legacy",
              subject: "Case đã lưu",
              claim,
              type: "case",
            },
          ],
        }) +
        sse("run.completed"),
    }),
  );
  await page.goto("/ask");
  await page.getByTestId("chat-input").fill("Hãy xem ca MR-JE-AX205");
  await page.getByTestId("chat-send").click();
  await expect(page.getByTestId("chat-answer")).toHaveAttribute(
    "data-status",
    "complete",
  );
  await page.getByTestId("source-card-1").click();
  await expect(page.getByTestId("case-citation-card")).toBeVisible();
  await expect(page.getByTestId("case-row-reasoning")).toContainText(
    "suspected, not confirmed",
  );
  await expect(page.getByTestId("case-row-action")).toContainText(card.action);
  await expect(page.getByTestId("case-row-outcome")).toContainText(
    card.outcome,
  );
  await expect(page.getByTestId("case-row-condition")).toContainText(
    "High spindle load",
  );
  await expect(page.getByTestId("case-review-status")).toHaveText(
    "Nháp — chờ duyệt",
  );
  await expect(page.getByTestId("case-citation-card")).not.toContainText(
    "Fallback plain text",
  );
  await page.getByTestId("case-ref-1").click();
  await expect(page.getByTestId("ref-quote")).toContainText(
    card.evidence[0].quote,
  );
  await expect(page.getByTestId("ref-quote")).toContainText("Lượt T03");
  await page.screenshot({
    path: "test-results/case-card-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByTestId("citation-marker").filter({ hasText: /^1$/ }).click();
  await expect(
    page.getByRole("dialog").getByTestId("case-citation-card"),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/case-card-mobile.png",
    fullPage: true,
  });
  await page.keyboard.press("Escape");
  await page.getByTestId("citation-marker").filter({ hasText: /^2$/ }).click();
  await expect(
    page.getByRole("dialog").getByTestId("case-row-outcome"),
  ).toContainText(card.outcome);
  await expect(
    page.getByRole("dialog").getByTestId("case-review-status"),
  ).toHaveText("Chưa có trạng thái duyệt");
});

test("live QA selects a KB, streams real-contract citations, retains thread and isolates KB histories", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await mockBases(page);
  const calls: { url: string; body: { query: string; thread_id: string } }[] =
    [];
  await page.route("**/v1/knowledge-agent/*/stream", async (route) => {
    calls.push({
      url: route.request().url(),
      body: route.request().postDataJSON(),
    });
    await route.fulfill({
      contentType: "text/event-stream",
      body:
        sse("run.started") +
        sse("answer.delta", { text: "Dòng tạm thời" }) +
        sse("answer.reset") +
        sse("answer.delta", {
          text: "Cần kiểm tra thêm [K:CASE-1]. Xem manual [D:manual-71].",
        }) +
        sse("answer.citations", {
          citations: [
            {
              knowledge_id: "case-1",
              subject: "Ca bảo trì",
              claim: "Chưa xác nhận nguyên nhân.",
              evidence_id: "conversation-1",
              type: "case",
              stated_by: "Expert",
            },
            { doc_id: "manual-71", title: "Manual thiết bị" },
          ],
        }) +
        sse("run.completed"),
    });
  });
  await page.goto("/");
  await expect(page).toHaveURL(/\/ask$/);
  await expect(page.getByLabel("Knowledge base", { exact: true })).toHaveValue(
    "maintenance",
  );
  await expect(page.getByTestId("nav-record")).toHaveCount(0);
  await expect(page.getByTestId("upload-open")).toHaveCount(0);
  await page.getByTestId("chat-input").fill("Hãy tìm thông tin về máy khác");
  await page.getByTestId("chat-send").click();
  await expect(page.getByTestId("chat-answer")).toHaveAttribute(
    "data-status",
    "complete",
  );
  await expect(page.getByTestId("chat-answer")).not.toContainText(
    "Dòng tạm thời",
  );
  await expect(page.getByTestId("chat-answer")).not.toContainText(
    "MR-JE-AX167",
  );
  expect(calls[0].url).toContain("/maintenance/stream");
  expect(Object.keys(calls[0].body).sort()).toEqual(["query", "thread_id"]);
  await expect(page.getByTestId("chat-answer")).not.toContainText("K:CASE-1");
  await expect(page.getByTestId("chat-answer")).not.toContainText(
    "D:manual-71",
  );
  await expect(page.getByTestId("citation-marker")).toHaveText(["1", "2"]);
  await expect(page.getByTestId("source-card-1")).toContainText("Ca bảo trì");
  await expect(
    page.getByTestId("source-card-1").locator(".citation-index"),
  ).toHaveText("1");
  await page.getByTestId("citation-marker").filter({ hasText: /^1$/ }).click();
  await expect(page.getByTestId("evidence-api-detail")).toContainText(
    "Chưa xác nhận nguyên nhân.",
  );
  await expect(page.getByTestId("evidence-api-detail")).not.toContainText(
    "conversation-1",
  );
  await expect(page.getByTestId("evidence-api-detail")).not.toContainText(
    "K:case-1",
  );
  await page.getByTestId("citation-marker").filter({ hasText: /^2$/ }).click();
  await expect(page.getByTestId("evidence-api-detail")).toContainText(
    "Chưa có nội dung xem trước",
  );

  await page.getByTestId("chat-input").fill("Còn trường hợp khác?");
  await page.getByTestId("chat-send").click();
  await expect(page.getByTestId("chat-answer").last()).toHaveAttribute(
    "data-status",
    "complete",
  );
  expect(calls[1].body.thread_id).toBe(calls[0].body.thread_id);
  const firstThread = calls[0].body.thread_id;
  await page.reload();
  await expect(page.getByTestId("chat-answer")).toHaveCount(2);
  await expect(page.getByTestId("kb-select")).toHaveValue("maintenance");

  await page.getByTestId("kb-select").selectOption("industrial_qa");
  await expect(page.getByTestId("chat-answer")).toHaveCount(0);
  await page.getByTestId("chat-input").fill("Tìm tài liệu liên quan");
  await page.getByTestId("chat-send").click();
  await expect(page.getByTestId("chat-answer")).toHaveAttribute(
    "data-status",
    "complete",
  );
  expect(calls[2].url).toContain("/industrial_qa/stream");
  expect(calls[2].body.thread_id).not.toBe(firstThread);
  await page.getByRole("button", { name: "Hỏi đáp mới", exact: true }).click();
  await expect(
    page.getByLabel("Cuộc hỏi đáp", { exact: true }).locator("option"),
  ).toHaveCount(2);
  await page.getByTestId("kb-select").selectOption("maintenance");
  await page
    .getByLabel("Cuộc hỏi đáp", { exact: true })
    .selectOption(firstThread);
  await expect(page.getByTestId("chat-answer")).toHaveCount(2);
  await page.screenshot({
    path: "test-results/qa-desktop.png",
    fullPage: true,
  });

  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .getByTestId("citation-marker")
    .filter({ hasText: /^2$/ })
    .first()
    .click();
  await expect(page.getByRole("dialog")).toContainText("Manual thiết bị");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({ path: "test-results/qa-mobile.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("KB connection failures and empty lists block sending and can be retried", async ({
  page,
}) => {
  let phase: "failure" | "empty" | "ready" = "failure";
  await page.route("**/v1/knowledge-agent/knowledge-bases", (route) => {
    return route.fulfill(
      phase === "failure"
        ? { status: 503 }
        : { json: { knowledge_bases: phase === "empty" ? [] : bases } },
    );
  });
  await page.goto("/ask");
  await expect(page.getByRole("alert")).toContainText(
    "Không tải được knowledge base",
  );
  await page.getByTestId("chat-input").fill("Question");
  await expect(page.getByTestId("chat-send")).toBeDisabled();
  phase = "empty";
  await page.getByRole("button", { name: "Thử lại", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("chưa có knowledge base");
  await expect(page.getByTestId("chat-send")).toBeDisabled();
  phase = "ready";
  await page.getByRole("button", { name: "Thử lại", exact: true }).click();
  await expect(page.getByTestId("chat-send")).toBeEnabled();
});

test("stream EOF and run.failed remain failed; stop aborts a pending answer", async ({
  page,
}) => {
  await mockBases(page);
  let attempt = 0;
  await page.route("**/v1/knowledge-agent/*/stream", async (route) => {
    attempt++;
    if (attempt === 3) {
      // Hold the response until after the user aborts the request.
      await new Promise((resolve) => setTimeout(resolve, 1500));
      await route
        .fulfill({
          contentType: "text/event-stream",
          body: sse("run.completed"),
        })
        .catch(() => {});
      return;
    }
    await route.fulfill({
      contentType: "text/event-stream",
      body:
        attempt === 1
          ? sse("answer.delta", { text: "Nội dung chưa hoàn tất" })
          : sse("run.failed", { message: "Agent timeout" }),
    });
  });
  await page.goto("/ask");
  for (const expected of ["bị ngắt", "Agent timeout"]) {
    await page.getByTestId("chat-input").fill("Question");
    await page.getByTestId("chat-send").click();
    await expect(page.getByTestId("chat-answer").last()).toHaveAttribute(
      "data-status",
      "failed",
    );
    await expect(page.getByRole("alert")).toContainText(expected);
  }
  await page.getByTestId("chat-input").fill("Stop question");
  await page.getByTestId("chat-send").click();
  await page.getByTestId("chat-stop").click();
  await expect(page.getByTestId("chat-answer").last()).toHaveAttribute(
    "data-status",
    "stopped",
  );
  await expect(page.getByTestId("kb-select")).toBeEnabled();
});
