import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CapturePage } from "./capture";

const kbs = {
  knowledge_bases: [{ name: "general", description: "General knowledge" }],
};

function showCapture() {
  const router = createMemoryRouter(
    [{ path: "/capture", element: <CapturePage /> }],
    {
      initialEntries: ["/capture"],
    },
  );
  render(<RouterProvider router={router} />);
  return router;
}

describe("document upload tab", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("replaces typed conversation fields, uploads original files and retries only failed files", async () => {
    let failed = false;
    const uploads: string[] = [];
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/knowledge-bases")) return Response.json(kbs);
      if (url.endsWith("/conversations"))
        return Response.json({ conversations: [] });
      if (url === "/v1/documents/general/uploads") {
        const form = init?.body as FormData;
        const file = form.get("file") as File;
        expect(init?.method).toBe("POST");
        expect(form.get("name")).toBeNull();
        expect(form.get("dialogue")).toBeNull();
        uploads.push(file.name);
        if (file.name === "slides.pptx" && !failed) {
          failed = true;
          return Response.json({}, { status: 503 });
        }
        return Response.json({
          id: file.name,
          filename: file.name,
          kb: "general",
          status: "stored",
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const router = showCapture();
    await screen.findByRole("option", { name: "general" });
    expect(screen.getByText("Tên cuộc hội thoại")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("mode-documents"));
    await waitFor(() =>
      expect(screen.getByTestId("capture-kb")).not.toBeDisabled(),
    );
    expect(screen.queryByText("Tên cuộc hội thoại")).not.toBeInTheDocument();
    expect(screen.queryByText("Hội thoại nhiều người")).not.toBeInTheDocument();
    expect(screen.queryByTestId("capture-clean")).not.toBeInTheDocument();
    expect(screen.queryByTestId("capture-text")).not.toBeInTheDocument();
    expect(screen.getByTestId("document-upload")).toBeDisabled();

    fireEvent.change(screen.getByTestId("document-files"), {
      target: {
        files: [
          new File(["pdf bytes"], "manual.pdf", { type: "application/pdf" }),
        ],
      },
    });
    fireEvent.change(screen.getByTestId("document-files"), {
      target: { files: [new File(["slides bytes"], "slides.pptx")] },
    });
    expect(screen.getByText("manual.pdf")).toBeInTheDocument();
    expect(screen.getByText("slides.pptx")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Đang chờ tải lên: 2 file.",
    );
    // Closing the picker without a selection must also preserve the queue.
    fireEvent.change(screen.getByTestId("document-files"), {
      target: { files: [] },
    });
    expect(screen.getByRole("status")).toHaveTextContent(
      "Đang chờ tải lên: 2 file.",
    );
    fireEvent.click(screen.getByTestId("document-upload"));
    await screen.findByRole("alert");
    expect(screen.getByText(/Đã lưu vào general/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("document-upload"));
    await waitFor(() =>
      expect(screen.getAllByText(/Đã lưu vào general/)).toHaveLength(2),
    );
    expect(uploads).toEqual(["manual.pdf", "slides.pptx", "slides.pptx"]);
    expect(router.state.location.pathname).toBe("/capture");
    expect(screen.getByTestId("document-upload")).toBeDisabled();
    expect(fetchMock.mock.calls.every(([url]) => !url.endsWith("/texts"))).toBe(
      true,
    );

    // Adding a later batch keeps saved results and does not resend saved files.
    fireEvent.change(screen.getByTestId("document-files"), {
      target: { files: [new File(["notes bytes"], "notes.txt")] },
    });
    expect(screen.getAllByText(/Đã lưu vào general/)).toHaveLength(2);
    expect(screen.getByRole("status")).toHaveTextContent(
      "Đang chờ tải lên: 1 file.",
    );
    fireEvent.click(screen.getByTestId("document-upload"));
    await waitFor(() =>
      expect(screen.getAllByText(/Đã lưu vào general/)).toHaveLength(3),
    );
    expect(uploads).toEqual([
      "manual.pdf",
      "slides.pptx",
      "slides.pptx",
      "notes.txt",
    ]);

    fireEvent.click(screen.getByTestId("mode-voice"));
    expect(screen.getByText("Tên cuộc hội thoại")).toBeInTheDocument();
  });

  it("rejects empty documents without sending them", async () => {
    localStorage.setItem("voice-capture:mode", "text");
    const fetchMock = vi.fn(async () => Response.json(kbs));
    vi.stubGlobal("fetch", fetchMock);
    showCapture();
    await waitFor(() =>
      expect(screen.getByTestId("capture-kb")).not.toBeDisabled(),
    );
    fireEvent.change(screen.getByTestId("document-files"), {
      target: { files: [new File([], "empty.txt")] },
    });
    fireEvent.click(screen.getByTestId("document-upload"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Tài liệu trống",
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
