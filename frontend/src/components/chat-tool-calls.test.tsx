import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import type { ChatTool } from "@/services/chat-tools";
import { ChatToolCalls } from "./chat-tool-calls";

const ID = "3f2b8c1e-5d4a-4e6f-9a7b-0c1d2e3f4a5b";

const call = (extra: Partial<ChatTool>): ChatTool => ({
  id: "t1",
  name: "create_inquiry_draft",
  state: "results",
  catalog: [],
  results: [],
  ...extra,
});

function show(tool: ChatTool) {
  render(
    <MemoryRouter>
      <ChatToolCalls calls={[tool]} citations={[]} streaming={false} />
    </MemoryRouter>,
  );
}

afterEach(cleanup);

describe("ChatToolCalls inquiry tools", () => {
  it("links a created draft", () => {
    show(call({ inquiry_id: ID, title: "Lỗi servo" }));
    expect(screen.getByText("Tạo nháp yêu cầu chuyên gia")).toBeTruthy();
    expect(screen.getByText("Đã tạo nháp")).toBeTruthy();
    expect(screen.getByText("Lỗi servo")).toBeTruthy();
    expect(
      screen
        .getByRole("link", { name: "Mở nháp để chọn chuyên gia và gửi" })
        .getAttribute("href"),
    ).toBe(`/inquiries/${ID}`);
  });

  it("uses a fallback title and no link without an id", () => {
    show(call({ title: "" }));
    expect(screen.getByText("(chưa có tiêu đề)")).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("shows the needs-choice and not-draft states", () => {
    show(call({ name: "update_inquiry_draft", state: "needs_choice", inquiry_id: ID }));
    expect(screen.getByText("Cuộc chat đã có nháp")).toBeTruthy();
    expect(screen.getByRole("link").getAttribute("href")).toBe(`/inquiries/${ID}`);
    cleanup();
    show(call({ name: "update_inquiry_draft", state: "not_draft", inquiry_id: ID }));
    expect(screen.getByText("Yêu cầu đã được gửi, không sửa được")).toBeTruthy();
  });

  it("labels an updated draft", () => {
    show(call({ name: "update_inquiry_draft", inquiry_id: ID, title: "A" }));
    expect(screen.getByText("Sửa nháp yêu cầu chuyên gia")).toBeTruthy();
    expect(screen.getByText("Đã cập nhật nháp")).toBeTruthy();
  });
});
