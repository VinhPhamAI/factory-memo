import { describe, expect, it, vi } from "vitest";
import {
  allowedActions,
  InquiriesApi,
  InquiryError,
  type InquiryAuth,
} from "./inquiries-api";

function api(responses: Response[], refresh = false) {
  const authorizedFetch = vi.fn(async () => responses.shift()!);
  const auth: InquiryAuth = {
    authorizedFetch,
    refresh: vi.fn(async () => refresh),
  };
  return { client: new InquiriesApi(auth, ""), authorizedFetch, auth };
}

const fail = (status: number, detail?: unknown) =>
  Response.json({ detail }, { status });

describe("InquiriesApi errors", () => {
  it.each([
    [404, undefined, "Không tìm thấy yêu cầu này."],
    [
      409,
      { code: "invalid_transition", message: "x" },
      "Trạng thái đã thay đổi",
    ],
    [
      422,
      { code: "ineligible_assignee", message: "x" },
      "Chuyên gia này không thể nhận yêu cầu.",
    ],
    [422, { code: "content_required", message: "x" }, "Hãy nhập nội dung."],
    [
      502,
      { code: "draft_failed", message: "x" },
      "Trợ lý không soạn được yêu cầu. Hãy thử lại.",
    ],
    [
      503,
      "Inquiries are not configured",
      "Dịch vụ tạm thời không khả dụng. Hãy thử lại sau.",
    ],
  ])("maps %s %j", async (status, detail, message) => {
    const { client } = api([fail(status, detail)]);
    await expect(client.get("a")).rejects.toMatchObject({
      message,
      status,
    });
  });

  it("reports offline as status 0", async () => {
    const auth: InquiryAuth = {
      authorizedFetch: vi.fn(async () => {
        throw new TypeError("down");
      }),
      refresh: async () => false,
    };
    await expect(new InquiriesApi(auth, "").list("sent")).rejects.toEqual(
      expect.objectContaining({ status: 0 }),
    );
  });

  it("refreshes once on 401 and repeats", async () => {
    const { client, authorizedFetch } = api(
      [new Response(null, { status: 401 }), Response.json({ experts: [] })],
      true,
    );
    expect(await client.listExperts()).toEqual([]);
    expect(authorizedFetch).toHaveBeenCalledTimes(2);
  });

  it("exposes the error code", async () => {
    const { client } = api([
      fail(409, { code: "invalid_transition", message: "" }),
    ]);
    const error = await client.submit("a", "b").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(InquiryError);
    expect((error as InquiryError).code).toBe("invalid_transition");
  });
});

describe("allowedActions", () => {
  const base = { requested_by: "req", assigned_to: "exp" };
  it("lets the assignee act on open", () => {
    expect(allowedActions({ ...base, status: "open" }, "exp")).toEqual([
      "accept",
      "edit",
      "deny",
      "question",
    ]);
  });
  it("lets the requester reply on needs_info", () => {
    expect(allowedActions({ ...base, status: "needs_info" }, "req")).toEqual([
      "reply",
    ]);
  });
  it.each([
    ["open", "req"],
    ["needs_info", "exp"],
    ["draft", "req"],
    ["draft", "exp"],
    ["answered", "exp"],
    ["closed", "req"],
    ["open", "other"],
  ])("allows nothing on %s for %s", (status, user) => {
    expect(allowedActions({ ...base, status }, user)).toEqual([]);
  });
});
