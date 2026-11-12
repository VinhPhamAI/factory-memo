import { afterEach, describe, expect, it, vi } from "vitest";
import {
  checkFile,
  deleteFile,
  downloadFile,
  FileApiError,
  listFiles,
  MAX_FILE_BYTES,
  uploadFile,
  type LibraryFile,
} from "./file-api";

const stored: LibraryFile = {
  id: "0190-file",
  filename: "Hướng dẫn.pdf",
  content_type: "application/pdf",
  size_bytes: 5,
  sha256: "a".repeat(64),
  created_at: "2026-11-03T08:00:00Z",
};

/** A client whose requests answer from `responses` in order. */
function fakeClient(responses: (() => Response)[], refreshes = true) {
  // Typed parameters let tests read the recorded url and init.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const authorizedFetch = vi.fn(async (_url: string, _init?: RequestInit) => {
    const next = responses.shift();
    if (!next) throw new Error("Unexpected request");
    return next();
  });
  const refresh = vi.fn(async () => refreshes);
  return { authorizedFetch, refresh };
}

const pdf = (name = "manual.pdf", size = 5) =>
  new File([new Uint8Array(size)], name, { type: "application/pdf" });

async function rejection(promise: Promise<unknown>): Promise<FileApiError> {
  const error = await promise.then(
    () => null,
    (reason: unknown) => reason,
  );
  expect(error).toBeInstanceOf(FileApiError);
  return error as FileApiError;
}

afterEach(() => vi.unstubAllGlobals());

describe("file library client", () => {
  it("lists files from /v1/files", async () => {
    const client = fakeClient([() => Response.json({ files: [stored] })]);
    expect(await listFiles(client)).toEqual([stored]);
    expect(client.authorizedFetch).toHaveBeenCalledWith("/v1/files", {});
  });

  it("uploads one file in the multipart field `file`", async () => {
    const client = fakeClient([() => Response.json(stored, { status: 201 })]);
    expect(await uploadFile(client, pdf())).toEqual(stored);
    const [url, init] = client.authorizedFetch.mock.calls[0];
    expect(url).toBe("/v1/files");
    expect(init?.method).toBe("POST");
    expect(((init?.body as FormData).get("file") as File).name).toBe(
      "manual.pdf",
    );
  });

  it("deletes by encoded id", async () => {
    const client = fakeClient([() => new Response(null, { status: 204 })]);
    await deleteFile(client, "a/b");
    expect(client.authorizedFetch).toHaveBeenCalledWith("/v1/files/a%2Fb", {
      method: "DELETE",
    });
  });

  it("refreshes once after a 401 and retries once", async () => {
    const client = fakeClient([
      () => new Response(null, { status: 401 }),
      () => Response.json({ files: [] }),
    ]);
    expect(await listFiles(client)).toEqual([]);
    expect(client.refresh).toHaveBeenCalledTimes(1);
    expect(client.authorizedFetch).toHaveBeenCalledTimes(2);
  });

  it("reports an expired session when the refresh fails", async () => {
    const client = fakeClient(
      [() => new Response(null, { status: 401 })],
      false,
    );
    const error = await rejection(listFiles(client));
    expect(error.status).toBe(401);
    expect(error.message).toContain("Phiên đăng nhập đã hết hạn");
    expect(client.authorizedFetch).toHaveBeenCalledTimes(1);
  });

  it("maps server errors and network failures to messages", async () => {
    const cases: [number, string][] = [
      [400, "Hãy tải từng file một"],
      [404, "File không còn tồn tại."],
      [411, "không gửi kích thước file"],
      [413, "File quá lớn"],
      [415, "Chỉ nhận PDF, XLSX, DOCX, JPEG, PNG"],
      [503, "Máy chủ tạm thời không xử lý được"],
      [500, "HTTP 500"],
    ];
    for (const [status, message] of cases) {
      const client = fakeClient([() => new Response(null, { status })]);
      const error = await rejection(deleteFile(client, stored.id));
      expect(error.status).toBe(status);
      expect(error.message).toContain(message);
    }
    const offline = {
      authorizedFetch: vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
      refresh: vi.fn(async () => true),
    };
    const error = await rejection(listFiles(offline));
    expect(error.status).toBe(0);
    expect(error.message).toContain("Không kết nối được máy chủ");
  });

  it("refuses unsupported, empty and oversized files before sending", async () => {
    expect(checkFile(pdf("MANUAL.PDF"))).toBeNull();
    expect(checkFile(pdf("photo.jpeg"))).toBeNull();
    expect(checkFile(pdf("notes.TXT"))).toBeNull();
    expect(checkFile(pdf("README.md"))).toBeNull();
    expect(checkFile(pdf("notes.csv"))?.status).toBe(415);
    expect(checkFile(pdf("program.pdf.exe"))?.status).toBe(415);
    expect(checkFile(pdf("empty.pdf", 0))?.status).toBe(422);
    const big = pdf("big.pdf");
    Object.defineProperty(big, "size", { value: MAX_FILE_BYTES + 1 });
    expect(checkFile(big)?.status).toBe(413);

    const client = fakeClient([]);
    expect((await rejection(uploadFile(client, pdf("notes.csv")))).status).toBe(
      415,
    );
    expect(client.authorizedFetch).not.toHaveBeenCalled();
  });

  it("downloads through a blob link with the original filename", async () => {
    const createObjectURL = vi.fn(() => "blob:file");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        expect(this.download).toBe("Hướng dẫn.pdf");
        expect(this.href).toBe("blob:file");
      });
    vi.useFakeTimers();
    const client = fakeClient([() => new Response("bytes")]);

    await downloadFile(client, stored);
    expect(client.authorizedFetch).toHaveBeenCalledWith(
      "/v1/files/0190-file/content",
      {},
    );
    expect(click).toHaveBeenCalledTimes(1);
    // The URL stays valid while the browser starts saving the file.
    vi.advanceTimersByTime(59_000);
    expect(revokeObjectURL).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1_000);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:file");
    click.mockRestore();
  });
});
