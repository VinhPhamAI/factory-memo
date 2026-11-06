import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/hooks/auth";
import type { AuthClient } from "@/services/auth-api";
import { CapturePage } from "./capture";
import { FilesPage } from "./files";

const kbs = {
  knowledge_bases: [{ name: "general", description: "General knowledge" }],
};

function showCapture() {
  const client = {
    restore: async () => {},
    refresh: async () => false,
    authorizedFetch: (url: string, init?: RequestInit) => fetch(url, init),
  } as unknown as AuthClient;
  const router = createMemoryRouter(
    [
      { path: "/capture", element: <CapturePage /> },
      { path: "/files", element: <FilesPage /> },
    ],
    { initialEntries: ["/capture"] },
  );
  render(
    <AuthProvider client={client}>
      <RouterProvider router={router} />
    </AuthProvider>,
  );
  return router;
}

const pick = (...files: File[]) =>
  fireEvent.change(screen.getByTestId("document-files"), {
    target: { files },
  });

describe("document upload tab", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("uses the personal file API, retries only failed files and shows uploads in the library", async () => {
    let failed = false;
    const uploads: string[] = [];
    const stored: object[] = [];
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/knowledge-bases")) return Response.json(kbs);
      if (url === "/v1/documents/general/files") {
        const body = JSON.parse(init!.body as string);
        expect(body.extract_mode).toBeNull();
        return Response.json(
          { id: body.file_id, kb: "general", status: "stored" },
          { status: 201 },
        );
      }
      if (url === "/v1/files") {
        if (init?.method !== "POST") return Response.json({ files: stored });
        const form = init.body as FormData;
        const file = form.get("file") as File;
        expect(Array.from(form.keys())).toEqual(["file"]);
        uploads.push(file.name);
        if (file.name === "photo.png" && !failed) {
          failed = true;
          return Response.json({}, { status: 503 });
        }
        const result = {
          id: file.name,
          filename: file.name,
          size_bytes: file.size,
          content_type: file.type,
          sha256: "a".repeat(64),
          created_at: "2026-11-05T00:00:00Z",
        };
        stored.push(result);
        return Response.json(result, { status: 201 });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const router = showCapture();
    await screen.findByRole("option", { name: "general" });
    fireEvent.click(screen.getByTestId("mode-documents"));
    expect(screen.queryByText("Tên cuộc hội thoại")).not.toBeInTheDocument();
    expect(screen.queryByTestId("capture-clean")).not.toBeInTheDocument();
    expect(screen.getByTestId("capture-kb")).toBeInTheDocument();
    expect(screen.getByTestId("processing-store")).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await waitFor(() =>
      expect(screen.getByTestId("capture-kb")).not.toBeDisabled(),
    );
    expect(screen.getByTestId("document-upload")).toBeDisabled();
    expect(screen.getByTestId("document-files")).toHaveAttribute(
      "accept",
      ".pdf,.xlsx,.docx,.jpg,.jpeg,.png",
    );

    pick(new File(["pdf bytes"], "manual.pdf", { type: "application/pdf" }));
    pick(new File(["image bytes"], "photo.png", { type: "image/png" }));
    pick();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Đang chờ tải lên: 2 file.",
    );
    fireEvent.click(screen.getByTestId("document-upload"));
    await screen.findByRole("alert");
    expect(screen.getAllByText(/Đã lưu vào general/)).toHaveLength(1);
    fireEvent.click(screen.getByTestId("document-upload"));
    await waitFor(() =>
      expect(screen.getAllByText(/Đã lưu vào general/)).toHaveLength(2),
    );
    expect(uploads).toEqual(["manual.pdf", "photo.png", "photo.png"]);
    expect(screen.getByTestId("document-upload")).toBeDisabled();

    pick(new File(["more bytes"], "more.pdf", { type: "application/pdf" }));
    fireEvent.click(screen.getByTestId("document-upload"));
    await waitFor(() =>
      expect(screen.getAllByText(/Đã lưu vào general/)).toHaveLength(3),
    );
    expect(uploads).toEqual([
      "manual.pdf",
      "photo.png",
      "photo.png",
      "more.pdf",
    ]);
    fireEvent.click(
      screen.getByRole("link", { name: /^Thư viện tài liệu$/ }),
    );
    expect(await screen.findAllByTestId("library-file")).toHaveLength(3);
    expect(router.state.location.pathname).toBe("/files");
  });

  it("rejects empty, unsupported and oversized files using library validation", async () => {
    localStorage.setItem("voice-capture:mode", "documents");
    const fetchMock = vi.fn(async () => Response.json(kbs));
    vi.stubGlobal("fetch", fetchMock);
    showCapture();
    await waitFor(() =>
      expect(screen.getByTestId("capture-kb")).not.toBeDisabled(),
    );
    const huge = new File(["bytes"], "large.pdf");
    Object.defineProperty(huge, "size", { value: 100 * 1024 * 1024 + 1 });
    pick(new File([], "empty.pdf"), new File(["text"], "notes.txt"), huge);
    fireEvent.click(screen.getByTestId("document-upload"));
    await waitFor(() => expect(screen.getAllByRole("alert")).toHaveLength(3));
    expect(
      screen.getAllByRole("alert").map((element) => element.textContent),
    ).toEqual([
      expect.stringContaining("File trống"),
      expect.stringContaining("Chỉ nhận PDF"),
      expect.stringContaining("File quá lớn"),
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("retains Auto extraction and retries attachment without uploading saved bytes again", async () => {
    localStorage.setItem("voice-capture:mode", "text");
    localStorage.setItem("voice-capture:auto", "true");
    localStorage.setItem("voice-capture:extract-mode", "documents");
    let links = 0;
    let uploads = 0;
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/knowledge-bases")) return Response.json(kbs);
      if (url === "/v1/files") {
        uploads++;
        expect(Array.from((init?.body as FormData).keys())).toEqual(["file"]);
        return Response.json(
          { id: "file1", filename: "manual.pdf" },
          { status: 201 },
        );
      }
      expect(url).toBe("/v1/documents/general/files");
      expect(JSON.parse(init!.body as string)).toEqual({
        file_id: "file1",
        extract_mode: "documents",
      });
      links++;
      if (links === 1) return Response.json({}, { status: 503 });
      return Response.json(
        {
          id: "source1",
          kb: "general",
          status: "stored",
          job: { id: "job1", stage: "convert", status: "queued" },
        },
        { status: 201 },
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    showCapture();
    await waitFor(() =>
      expect(screen.getByTestId("capture-kb")).not.toBeDisabled(),
    );
    expect(screen.getByTestId("processing-auto")).toHaveAttribute(
      "aria-checked",
      "true",
    );
    pick(new File(["pdf"], "manual.pdf"));
    fireEvent.click(screen.getByTestId("document-upload"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "File đã lưu trong Thư viện tài liệu",
    );
    fireEvent.click(screen.getByTestId("document-upload"));
    expect(await screen.findByTestId("upload-job")).toHaveTextContent(
      "Đã xếp hàng chuyển đổi → trích xuất",
    );
    expect(uploads).toBe(1);
    expect(links).toBe(2);
    fireEvent.click(screen.getByTestId("mode-voice"));
    await screen.findByRole("option", { name: "general" });
    expect(screen.getByTestId("processing-auto")).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByTestId("capture-extract-mode")).toHaveValue("documents");
    expect(screen.getByTestId("capture-clean")).toBeInTheDocument();
  });
});
