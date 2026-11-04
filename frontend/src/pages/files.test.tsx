import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/hooks/auth";
import type { AuthClient } from "@/services/auth-api";
import type { LibraryFile } from "@/services/file-api";
import { FilesPage, formatSize } from "./files";

const manual: LibraryFile = {
  id: "file-1",
  filename: "manual.pdf",
  content_type: "application/pdf",
  size_bytes: 2048,
  sha256: "a".repeat(64),
  created_at: "2026-11-03T08:00:00Z",
};

type Handler = (url: string, init?: RequestInit) => Response;

/** Render the page with a client whose requests are answered by `handler`. */
function showFiles(handler: Handler) {
  const authorizedFetch = vi.fn(async (url: string, init?: RequestInit) =>
    handler(url, init),
  );
  const client = {
    state: { status: "signed-in" },
    restore: async () => {},
    refresh: async () => false,
    subscribe: () => () => {},
    authorizedFetch,
  } as unknown as AuthClient;
  render(
    <AuthProvider client={client}>
      <FilesPage />
    </AuthProvider>,
  );
  return authorizedFetch;
}

const pick = (...files: File[]) =>
  fireEvent.change(screen.getByTestId("files-input"), {
    target: { files },
  });

describe("file library page", () => {
  it("shows an upload prompt when the library is empty", async () => {
    showFiles(() => Response.json({ files: [] }));
    expect(await screen.findByTestId("files-empty")).toHaveTextContent(
      "Chưa có file nào",
    );
  });

  it("lists files with type, size and actions", async () => {
    showFiles(() => Response.json({ files: [manual] }));
    const row = await screen.findByTestId("library-file");
    expect(row).toHaveTextContent("manual.pdf");
    expect(row).toHaveTextContent("PDF · 2.0 KB");
    expect(
      within(row).getByRole("button", { name: "Tải về manual.pdf" }),
    ).toBeInTheDocument();
  });

  it("shows a load error", async () => {
    showFiles(() => new Response(null, { status: 503 }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Máy chủ tạm thời không xử lý được",
    );
  });

  it("uploads files one by one and reports each result", async () => {
    const uploaded: LibraryFile = {
      ...manual,
      id: "file-2",
      filename: "new.pdf",
    };
    const fetch = showFiles((_url, init) => {
      if (init?.method === "POST") {
        const file = (init.body as FormData).get("file") as File;
        return file.name === "new.pdf"
          ? Response.json(uploaded, { status: 201 })
          : new Response(null, { status: 415 });
      }
      return Response.json({ files: [] });
    });
    await screen.findByTestId("files-empty");

    pick(
      new File(["%PDF-"], "new.pdf"),
      new File(["MZ"], "renamed.pdf"),
      new File(["text"], "notes.txt"),
    );

    await waitFor(() => expect(screen.getAllByTestId("toast")).toHaveLength(3));
    const [ok, renamed, notes] = screen.getAllByTestId("toast");
    expect(ok).toHaveTextContent("Đã tải lên “new.pdf”.");
    expect(ok).toHaveAttribute("role", "status");
    expect(renamed).toHaveTextContent("“renamed.pdf”: Chỉ nhận PDF");
    expect(renamed).toHaveAttribute("role", "alert");
    expect(notes).toHaveTextContent("“notes.txt”: Chỉ nhận PDF");
    expect(screen.getByTestId("library-file")).toHaveTextContent("new.pdf");
    // Results are popups, not a list in the page.
    expect(screen.queryByTestId("files-results")).not.toBeInTheDocument();
    // notes.txt is refused in the browser and never sent.
    expect(
      fetch.mock.calls.filter(([, init]) => init?.method === "POST"),
    ).toHaveLength(2);
  });

  it("deletes after confirmation and keeps the file when cancelled", async () => {
    showFiles((_url, init) =>
      init?.method === "DELETE"
        ? new Response(null, { status: 204 })
        : Response.json({ files: [manual] }),
    );
    const row = await screen.findByTestId("library-file");

    fireEvent.click(
      within(row).getByRole("button", { name: "Xóa manual.pdf" }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Hủy" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(screen.getByTestId("library-file")).toBeInTheDocument();

    fireEvent.click(
      within(row).getByRole("button", { name: "Xóa manual.pdf" }),
    );
    fireEvent.click(await screen.findByTestId("files-confirm-delete"));
    expect(await screen.findByTestId("files-empty")).toBeInTheDocument();
    expect(screen.getByTestId("toast")).toHaveTextContent(
      "Đã xóa “manual.pdf”.",
    );
  });

  it("shows a row error when download fails and a popup when delete fails", async () => {
    showFiles((url, init) => {
      if (init?.method === "DELETE") return new Response(null, { status: 500 });
      if (url.endsWith("/content")) return new Response(null, { status: 503 });
      return Response.json({ files: [manual] });
    });
    const row = await screen.findByTestId("library-file");

    fireEvent.click(
      within(row).getByRole("button", { name: "Tải về manual.pdf" }),
    );
    expect(await within(row).findByRole("alert")).toHaveTextContent(
      "Máy chủ tạm thời không xử lý được",
    );

    fireEvent.click(
      within(row).getByRole("button", { name: "Xóa manual.pdf" }),
    );
    fireEvent.click(await screen.findByTestId("files-confirm-delete"));
    expect(await screen.findByTestId("toast")).toHaveTextContent(
      "“manual.pdf”: Thao tác thất bại (HTTP 500).",
    );
    expect(screen.getByTestId("library-file")).toBeInTheDocument();
  });

  it("drops a row whose file is already gone and says so", async () => {
    showFiles((_url, init) =>
      init?.method === "DELETE"
        ? new Response(null, { status: 404 })
        : Response.json({ files: [manual] }),
    );
    const row = await screen.findByTestId("library-file");

    fireEvent.click(
      within(row).getByRole("button", { name: "Xóa manual.pdf" }),
    );
    fireEvent.click(await screen.findByTestId("files-confirm-delete"));
    expect(await screen.findByTestId("toast")).toHaveTextContent(
      "“manual.pdf”: File không còn tồn tại.",
    );
    expect(screen.queryByTestId("library-file")).not.toBeInTheDocument();
  });
});

describe("formatSize", () => {
  it("uses B, KB and MB", () => {
    expect(formatSize(512)).toBe("512 B");
    expect(formatSize(1536)).toBe("1.5 KB");
    expect(formatSize(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});
