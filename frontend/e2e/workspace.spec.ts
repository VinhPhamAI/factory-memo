import { test, expect, type Page } from "@playwright/test";

// Simulated delays are divided by this factor so the suite runs quickly.
const FAST = "?speed=40";
const QUESTION =
  "MR-JE-AX167 trục Z kêu rít khi chạy nhanh, không có alarm. Do vòng bi hay do máy? Cần làm gì?";

function trackErrors(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  return errors;
}
const noPageOverflow = (page: Page) =>
  page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth,
  );

test("5-step flow: record → OCR → analyse → approve → ask with sources", async ({
  page,
}) => {
  const errors = trackErrors(page);
  await page.setViewportSize({ width: 1920, height: 1080 });

  // 1. Ghi âm
  await page.goto(`/record${FAST}`);
  await expect(page).toHaveTitle(/Factory Memo/);
  await expect(page.locator(".desktop-sidebar .brand")).toContainText(
    "Factory Memo",
  );
  await expect(page.getByTestId("record-state")).toHaveAttribute(
    "data-state",
    "idle",
  );
  await page.getByTestId("record-start").click();
  await expect(page.getByTestId("speaker-junior_01")).toHaveAttribute(
    "data-active",
    "true",
  );
  await expect(page.getByTestId("record-turn-count")).toHaveText("14/14 lượt", {
    timeout: 30000,
  });
  await expect(page.getByTestId("turn-T10")).toContainText(
    "Always disconnect the motor power cable and wait 15 minutes",
  );
  await page.getByTestId("record-finish").click();
  await expect(page.getByTestId("record-done")).toContainText("Nháp");
  expect(await noPageOverflow(page)).toBe(true);

  // 2. Tài liệu + OCR
  await page.getByTestId("nav-sources").click();
  await page.getByTestId("upload-open").click();
  await page.getByTestId("sample-doc-doc-fr-l11-1007").click();
  await expect(page.getByTestId("ocr-step-upload")).toHaveAttribute(
    "data-state",
    "done",
  );
  await expect(page.getByTestId("ocr-done")).toBeVisible();
  await expect(page.getByTestId("ocr-text")).toContainText(
    "Grinding noise during Z-axis rapid traverse",
  );
  await expect(page.getByTestId("ocr-field").first()).toBeVisible();
  await expect(page.getByTestId("ocr-view")).toContainText("MR-JE-AX167");
  await page.getByTestId("ocr-save").click();
  await expect(page.getByTestId("save-notice")).toContainText(
    "Phiếu báo lỗi FR-L11-1007",
  );
  await expect(page.locator(".source-row")).toHaveCount(6);
  await expect(page.getByTestId("source-row-doc-fr-l11-1007")).toContainText(
    "AI OCR",
  );

  // 3. Phân tích AI
  await page.getByTestId("sources-analyze-cta").click();
  await page.getByTestId("analyze-start").click();
  await expect(page.getByTestId("analysis-question-1")).not.toHaveAttribute(
    "data-state",
    "pending",
  );
  await expect(page.getByTestId("analysis-done")).toBeVisible({
    timeout: 45000,
  });
  for (let n = 1; n <= 7; n++)
    await expect(page.getByTestId(`analysis-question-${n}`)).toHaveAttribute(
      "data-state",
      "done",
    );
  await expect(page.getByTestId("analysis-verify")).toContainText(
    "8/8 khớp bản ghi gốc",
  );
  await expect(page.getByTestId("analysis-scope")).toContainText("wear");

  // 4. Knowledge card
  await page.getByTestId("analyze-submit-review").click();
  await expect(page.getByTestId("card-status")).toHaveAttribute(
    "data-status",
    "draft",
  );
  await expect(page.getByTestId("card-ruled-out")).toContainText(
    "Encoder failure",
  );
  await page.getByTestId("card-ref-check-T10").click();
  await expect(page.getByTestId("ref-quote")).toContainText(
    "Always disconnect the motor power cable",
  );
  await expect(page.getByTestId("review-approve")).toBeDisabled();
  for (const n of [1, 2, 3])
    await page.getByTestId(`review-check-${n}`).check();
  await page.getByTestId("review-approve").click();
  // "card-saving" lasts ~50 ms at this speed, so only the end state is asserted.
  await expect(page.getByTestId("card-approved")).toContainText(
    "knowledge_items · id f2bd9589",
  );
  await expect(page.getByTestId("card-status")).toHaveText("Đã duyệt");
  await page.reload();
  await expect(page.getByTestId("card-status")).toHaveAttribute(
    "data-status",
    "approved",
  );

  // 5. Hỏi đáp: only the chat feature, no step shell around it.
  await page.getByTestId("card-ask-cta").click();
  const chatRoot = page.getByTestId("chat-focus-root");
  await expect(chatRoot).toContainText("Factory Memo");
  await expect(page.locator(".desktop-sidebar")).toHaveCount(0);
  await expect(page.locator(".workspace-breadcrumb")).toHaveCount(0);
  await expect(page.getByText("Mô phỏng")).toHaveCount(0);
  await expect(page.getByText("BƯỚC 5 · HỎI ĐÁP")).toHaveCount(0);
  await page.getByTestId("chat-input").fill(QUESTION);
  await page.getByTestId("chat-send").click();
  // The "searching" state lasts ~30 ms at this speed; the answer keeps the scope chip.
  await expect(page.getByTestId("chat-answer")).toContainText(
    "Phạm vi: MR-JE-AX167",
  );
  await expect(page.getByTestId("chat-answer")).toHaveAttribute(
    "data-status",
    "complete",
    { timeout: 20000 },
  );
  await expect(page.getByTestId("chat-answer")).toContainText(
    "xưởng Binh Duong",
  );
  await expect(page.getByTestId("source-card-1")).toContainText("Đã duyệt");
  await expect(page.getByTestId("source-card-2")).toContainText("Trang 71");
  await expect(page.getByTestId("source-card-3")).toContainText("Mẫu");
  await page
    .locator("[data-testid=citation-marker][data-citation='1']")
    .first()
    .click();
  const detail = page.getByTestId("evidence-card-detail");
  await expect(detail.getByTestId("card-row-check")).toHaveAttribute(
    "data-highlighted",
    "true",
  );
  await detail.getByTestId("card-ref-check-T10").click();
  await detail.getByTestId("ref-open-source").click();
  await expect(
    page.locator("[data-testid=evidence-source-detail] [data-testid=turn-T10]"),
  ).toHaveAttribute("data-cited", "true");
  expect(await noPageOverflow(page)).toBe(true);
  expect(errors).toEqual([]);
});

test("recording pauses and blocks accidental departure", async ({ page }) => {
  await page.goto("/record?speed=10");
  await page.getByTestId("record-start").click();
  await expect(page.getByTestId("turn-T02")).toBeVisible({ timeout: 20000 });
  await page.getByTestId("record-pause").click();
  await expect(page.getByTestId("record-state")).toHaveAttribute(
    "data-state",
    "paused",
  );
  const text = await page.getByTestId("live-transcript").innerText();
  await page.waitForTimeout(800);
  expect(await page.getByTestId("live-transcript").innerText()).toBe(text);
  await page.getByTestId("nav-ask").click();
  await expect(
    page.getByRole("heading", { name: "Bản ghi âm vẫn đang mở" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Kết thúc và lưu" }).click();
  await expect(page.getByTestId("record-done")).toBeVisible();
});

test("a real text upload goes through OCR and keeps the detected machine", async ({
  page,
}) => {
  await page.goto(`/sources${FAST}`);
  await page.getByTestId("upload-open").click();
  await page.getByTestId("upload-file-input").setInputFiles({
    name: "shift-note.txt",
    mimeType: "text/plain",
    buffer: Buffer.from(
      "Shift note 2026-10-11\nMR-JE-AX167 Line 11 quiet today.",
    ),
  });
  await page.getByTestId("upload-start").click();
  await expect(page.getByTestId("ocr-done")).toBeVisible();
  await page.getByTestId("ocr-save").click();
  await expect(page.locator(".source-row")).toHaveCount(7);
  await page.reload();
  await expect(
    page.locator(".source-row", { hasText: "shift-note" }),
  ).toContainText("MR-JE-AX167");
  await page.getByTestId("upload-open").click();
  await page.getByTestId("upload-file-input").setInputFiles({
    name: "notes.exe",
    mimeType: "application/octet-stream",
    buffer: Buffer.from("not a document"),
  });
  await expect(page.getByRole("alert")).toHaveText(/PDF, TXT, MD/);
  await expect(page.getByTestId("upload-start")).toBeDisabled();
});

test("chat works before approval, abstains off-topic, and reset clears it", async ({
  page,
}) => {
  await page.goto(`/ask${FAST}`);
  await page.getByTestId("chat-input").fill(QUESTION);
  await page.getByTestId("chat-send").click();
  await expect(page.getByTestId("chat-answer")).toHaveAttribute(
    "data-status",
    "complete",
  );
  await expect(page.getByTestId("source-card-1")).toContainText("Nháp");
  await page.getByTestId("chat-input").fill("Giá một con servo bao nhiêu?");
  await page.getByTestId("chat-send").click();
  await expect(page.getByText(/Chưa tìm thấy đủ thông tin/)).toBeVisible();
  // The chat screen has no step shell; reset lives in the shell of steps 1–4.
  await page.goto("/sources");
  await expect(page.locator(".history-item")).toHaveCount(1);
  await page.getByTestId("workspace-options").click();
  await page.getByTestId("reset-demo").click();
  await page.getByTestId("reset-confirm").click();
  await expect(page.getByTestId("record-start")).toBeVisible();
  await expect(page.locator(".history-item")).toHaveCount(0);
});

test("every step fits 1920, 1440 and 390 px without page overflow", async ({
  page,
}) => {
  const errors = trackErrors(page);
  for (const [width, height] of [
    [1920, 1080],
    [1440, 900],
    [390, 900],
  ]) {
    await page.setViewportSize({ width, height });
    for (const route of ["/record", "/sources", "/analyze", "/card", "/ask"]) {
      await page.goto(route);
      await expect(page.locator(".workspace-outlet > *")).toBeVisible();
      expect(await noPageOverflow(page), `${route} at ${width}px`).toBe(true);
    }
  }
  expect(errors).toEqual([]);
});
