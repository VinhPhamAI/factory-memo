import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
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
const source = { id: "s1", kb: "knowledge_base_seed1", kind: "upload", name: "manual.pdf", file_sha256: manual.sha256, stage: "raw", review: "none", processing: { status: "queued" } };

function showFiles(handler: Handler, kbNames = ["knowledge_base_seed1"], sourceItems = [source]) {
  const authorizedFetch = vi.fn(async (url: string, init?: RequestInit) =>
    url.endsWith("/knowledge-agent/knowledge-bases")
      ? Response.json({ knowledge_bases: kbNames.map((name) => ({ name, title: name, description: "" })) })
      : url.includes("/sources?offset=") ? Response.json({ sources: sourceItems, next_offset: null, warnings: [], qa_mode: "knowledge" }) : handler(url, init),
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
      <MemoryRouter><FilesPage /></MemoryRouter>
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
    expect(screen.queryByRole("button", { name: "Làm mới" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByText("Nhận PDF, XLSX, DOCX, JPEG, PNG; tối đa 100 MiB mỗi file.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload Files" })).toBeInTheDocument();
  });

  it("lists files with type, size and actions", async () => {
    showFiles(() => Response.json({ files: [manual] }));
    const row = await screen.findByTestId("library-file");
    expect(row).toHaveTextContent("manual.pdf");
    expect(row).toHaveTextContent("PDF · 2.0 KB");
    expect(
      within(row).getByRole("button", { name: "Tùy chọn manual.pdf" }),
    ).toBeInTheDocument();
  });

  it("filters library cards by filename", async () => {
    showFiles(() => Response.json({ files: [manual, { ...manual, id: "file-2", filename: "pump.xlsx" }] }));
    expect(await screen.findAllByTestId("library-file")).toHaveLength(2);
    fireEvent.change(screen.getByRole("searchbox", { name: "Search library" }), { target: { value: "PUMP" } });
    expect(screen.getAllByTestId("library-file")).toHaveLength(1);
    expect(screen.getByTestId("library-file")).toHaveTextContent("pump.xlsx");
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
    const fetch = showFiles((url, init) => {
      if (url.endsWith("/v1/documents/knowledge_base_seed1/files")) {
        expect(JSON.parse(init?.body as string)).toEqual({ file_id: "file-2", extract_mode: "knowledge" });
        return Response.json({ id: "source-1", kb: "knowledge_base_seed1", job: { id: "job-1", status: "queued" } }, { status: 201 });
      }
      if (init?.method === "POST") {
        const file = (init.body as FormData).get("file") as File;
        return file.name === "new.pdf"
          ? Response.json(uploaded, { status: 201 })
          : new Response(null, { status: 415 });
      }
      return Response.json({ files: [] });
    });
    await screen.findByTestId("files-empty");
    await waitFor(() => expect(screen.getByTestId("files-upload")).toBeEnabled());

    pick(
      new File(["%PDF-"], "new.pdf"),
      new File(["MZ"], "renamed.pdf"),
      new File(["text"], "notes.txt"),
    );

    await waitFor(() => expect(screen.getAllByTestId("toast")).toHaveLength(3));
    const [ok, renamed, notes] = screen.getAllByTestId("toast");
    expect(ok).toHaveTextContent("Đã tải lên và đưa “new.pdf” vào hàng đợi xử lý.");
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
    ).toHaveLength(3);
  });

  it("keeps the uploaded file and reports a queue failure", async () => {
    showFiles((url, init) => {
      if (url.endsWith("/v1/documents/knowledge_base_seed1/files"))
        return Response.json({ id: "source-1", kb: "knowledge_base_seed1", job_error: "Worker unavailable" }, { status: 201 });
      if (init?.method === "POST") return Response.json(manual, { status: 201 });
      return Response.json({ files: [] });
    });
    await waitFor(() => expect(screen.getByTestId("files-upload")).toBeEnabled());
    pick(new File(["%PDF-"], "manual.pdf"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Worker unavailable");
    expect(screen.getByTestId("library-file")).toHaveTextContent("manual.pdf");
  });

  it("does not upload without a knowledge base", async () => {
    const fetch = showFiles(() => Response.json({ files: [] }), ["another_kb"]);
    expect(await screen.findByRole("alert")).toHaveTextContent("knowledge_base_seed1");
    expect(screen.getByTestId("files-upload")).toBeDisabled();
    pick(new File(["%PDF-"], "manual.pdf"));
    expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("reports an existing source without claiming a new job was queued", async () => {
    showFiles((url, init) => {
      if (url.endsWith("/v1/documents/knowledge_base_seed1/files"))
        return Response.json({ id: "existing", kb: "knowledge_base_seed1", duplicate: true }, { status: 200 });
      if (init?.method === "POST") return Response.json(manual, { status: 201 });
      return Response.json({ files: [] });
    });
    await waitFor(() => expect(screen.getByTestId("files-upload")).toBeEnabled());
    pick(new File(["%PDF-"], "manual.pdf"));
    expect(await screen.findByRole("alert")).toHaveTextContent("File đã có trong knowledge base");
    expect(screen.queryByRole("button", { name: "Đưa vào xử lý" })).toBeNull();
  });

  it("opens knowledge state from the persisted source after loading the page", async () => {
    showFiles(() => Response.json({ files: [manual] }));
    await screen.findByTestId("library-file");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Tùy chọn manual.pdf" }));
    await user.click(await screen.findByRole("menuitem", { name: "Knowledge State" }));
    expect(await screen.findByText("Đang chờ xử lý")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Xem kết quả và chi tiết" })).toHaveAttribute("href", "/knowledge/knowledge_base_seed1/sources/upload/s1");
    expect(screen.queryByRole("button", { name: "Đưa vào xử lý" })).not.toBeInTheDocument();
  });

  it("selects files and removes their knowledge source without deleting the library file", async () => {
    const fetch = showFiles((_url, init) => init?.method === "DELETE" ? new Response(null, { status: 204 }) : Response.json({ files: [manual] }));
    await screen.findByTestId("library-file");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Chọn manual.pdf" }));
    expect(screen.getByText("Đã chọn 1 file")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove from knowledge base" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("File vẫn còn trong Library");
    await user.click(screen.getByTestId("files-confirm-delete"));
    await waitFor(() => expect(fetch.mock.calls.some(([url, init]) => url.endsWith("/sources/upload/s1") && init?.method === "DELETE")).toBe(true));
    expect(screen.getByTestId("library-file")).toBeInTheDocument();
  });

  it("reports a running job and keeps the file when source removal is refused", async () => {
    showFiles((_url, init) => init?.method === "DELETE" ? new Response(null, { status: 409 }) : Response.json({ files: [manual] }));
    await screen.findByTestId("library-file");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Tùy chọn manual.pdf" }));
    const remove = await screen.findByRole("menuitem", { name: "Remove from knowledge base" });
    await waitFor(() => expect(remove).not.toHaveAttribute("data-disabled"));
    await user.click(remove);
    await user.click(await screen.findByTestId("files-confirm-delete"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Nguồn đang được xử lý");
    expect(screen.getByTestId("library-file")).toBeInTheDocument();
  });

});

describe("formatSize", () => {
  it("uses B, KB and MB", () => {
    expect(formatSize(512)).toBe("512 B");
    expect(formatSize(1536)).toBe("1.5 KB");
    expect(formatSize(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});
