import { afterEach, describe, expect, it, vi } from "vitest";
import { uploadDocument } from "./document-api";

afterEach(() => vi.unstubAllGlobals());

describe("document upload errors", () => {
  const file = () =>
    new File(["original bytes"], "notes.txt", { type: "text/plain" });

  it("identifies an API missing the upload endpoint", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({ detail: "Not Found" }, { status: 404 }),
      ),
    );
    await expect(uploadDocument("general", file())).rejects.toThrow(
      "Không tìm thấy API tải tài liệu",
    );
  });

  it("identifies a missing knowledge base separately", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({ detail: "Knowledge base not found" }, { status: 404 }),
      ),
    );
    await expect(uploadDocument("missing", file())).rejects.toThrow(
      "Không tìm thấy knowledge base đã chọn.",
    );
  });

  it("handles a proxy's HTML 404 without blaming the knowledge base", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("<h1>Not Found</h1>", { status: 404 })),
    );
    await expect(uploadDocument("general", file())).rejects.toThrow(
      "Không tìm thấy API tải tài liệu",
    );
  });
});
