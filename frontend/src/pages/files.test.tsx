import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
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
    url.endsWith("/v1/knowledge-bases")
      ? Response.json({ knowledge_bases: kbNames.map((name) => ({ name, title: name, description: "", is_default: name === "knowledge_base_seed1" })) })
      : url.includes("/sources?offset=") ? Response.json({ sources: sourceItems.filter((item) => url.includes(`/knowledge-management/${item.kb}/`)), next_offset: null, warnings: [], qa_mode: "knowledge" }) : handler(url, init),
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

const docx: LibraryFile = {
  ...manual,
  id: "file-2",
  filename: "huong-dan.docx",
  content_type:
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  sha256: "b".repeat(64),
};
const photo = (id: string, size: number): LibraryFile => ({
  ...manual,
  id,
  filename: `${id}.png`,
  content_type: "image/png",
  size_bytes: size,
  sha256: id.padEnd(64, "c"),
});
// jsdom Blobs do not cross into fetch Responses, so set the type by header.
const pngResponse = () =>
  new Response("png", { headers: { "content-type": "image/png" } });

describe("library file preview", () => {
  afterEach(() => vi.restoreAllMocks());

  /** Answer file list, preview and original requests and record their paths. */
  function serveLibrary(items: LibraryFile[], preview: (id: string) => Response) {
    const requested: string[] = [];
    // jsdom lacks object URLs; install restorable stubs.
    URL.createObjectURL ??= () => "";
    URL.revokeObjectURL ??= () => {};
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:preview");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    showFiles((url) => {
      requested.push(url);
      const id = url.match(/\/v1\/files\/([^/]+)\//)?.[1];
      if (url.endsWith("/preview") && id) return preview(id);
      if (url.endsWith("/content")) return pngResponse();
      return Response.json({ files: items });
    });
    return requested;
  }

  it("labels an Office excerpt and returns focus to the filename", async () => {
    serveLibrary([docx], () =>
      new Response("Bước 1. Ngắt nguồn.", {
        headers: { "content-type": "text/plain; charset=utf-8" },
      }),
    );
    const user = userEvent.setup();
    const opener = await screen.findByRole("button", {
      name: "Xem trước huong-dan.docx",
    });
    await user.click(opener);
    const dialog = await screen.findByTestId("library-file-dialog");
    expect(dialog).toHaveTextContent("Trích đoạn");
    expect(await within(dialog).findByTestId("library-excerpt")).toHaveTextContent(
      "Bước 1. Ngắt nguồn.",
    );
    expect(within(dialog).getByRole("button", { name: "Tải xuống" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Mở PDF" })).toBeNull();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(opener).toHaveFocus());
  });

  it("shows the first PDF page with open and download actions", async () => {
    const requested = serveLibrary([manual], () => pngResponse());
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "Xem trước manual.pdf" }),
    );
    const dialog = await screen.findByTestId("library-file-dialog");
    expect(await within(dialog).findByRole("img")).toHaveAttribute(
      "alt",
      "Trang đầu của manual.pdf",
    );
    expect(within(dialog).getByRole("button", { name: "Mở PDF" })).toBeInTheDocument();
    expect(requested.some((url) => url.endsWith("/file-1/content"))).toBe(false);
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview"),
    );
  });

  it("loads small originals and keeps large images on the bounded preview", async () => {
    const requested = serveLibrary(
      [photo("small", 2048), photo("large", 40 * 1024 * 1024)],
      () => pngResponse(),
    );
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Xem trước small.png" }));
    let dialog = await screen.findByTestId("library-file-dialog");
    expect(await within(dialog).findByRole("img")).toHaveAttribute("alt", "Ảnh gốc của small.png");
    expect(requested.some((url) => url.endsWith("/small/content"))).toBe(true);
    expect(within(dialog).queryByRole("button", { name: "Mở ảnh gốc" })).toBeNull();
    await user.keyboard("{Escape}");

    await user.click(screen.getByRole("button", { name: "Xem trước large.png" }));
    dialog = await screen.findByTestId("library-file-dialog");
    expect(await within(dialog).findByRole("img")).toHaveAttribute("alt", "Ảnh thu nhỏ của large.png");
    expect(requested.some((url) => url.endsWith("/large/content"))).toBe(false);
    expect(within(dialog).getByRole("button", { name: "Mở ảnh gốc" })).toBeInTheDocument();
  });

  it("explains when a file has no preview", async () => {
    serveLibrary([docx], () => new Response(null, { status: 422 }));
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "Xem trước huong-dan.docx" }),
    );
    expect(
      await within(await screen.findByTestId("library-file-dialog")).findByRole("alert"),
    ).toHaveTextContent("Tệp này không có bản xem trước");
  });
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
    expect(screen.getByRole("button", { name: "Tải tài liệu lên" })).toBeInTheDocument();
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
    fireEvent.change(screen.getByRole("searchbox", { name: "Tìm tài liệu" }), { target: { value: "PUMP" } });
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
    expect(await screen.findByRole("alert")).toHaveTextContent("Chưa có kho kiến thức mặc định");
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
    expect(await screen.findByRole("alert")).toHaveTextContent("File đã có trong kho kiến thức");
    expect(screen.queryByRole("button", { name: "Đưa vào xử lý" })).toBeNull();
  });

  it("opens knowledge state from the persisted source after loading the page", async () => {
    showFiles(() => Response.json({ files: [manual] }));
    await screen.findByTestId("library-file");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Tùy chọn manual.pdf" }));
    await user.click(await screen.findByRole("menuitem", { name: "Trạng thái xử lý" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Đang chờ xử lý")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Xem kết quả và chi tiết" })).toHaveAttribute("href", "/knowledge/knowledge_base_seed1/sources/upload/s1");
    expect(screen.queryByRole("button", { name: "Đưa vào xử lý" })).not.toBeInTheDocument();
  });

  it("links straight to the source detail from the file card and its menu", async () => {
    showFiles(() => Response.json({ files: [manual] }));
    const card = await screen.findByTestId("library-file");
    const href = "/knowledge/knowledge_base_seed1/sources/upload/s1";
    expect(await within(card).findByText("Đang chờ xử lý")).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Xem chi tiết manual.pdf" })).toHaveAttribute("href", href);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Tùy chọn manual.pdf" }));
    expect(await screen.findByRole("menuitem", { name: "Mở chi tiết tri thức" })).toHaveAttribute("href", href);
  });

  it("keeps an older KB source visible after the Library default changes", async () => {
    const prior = { ...source, kb: "another_kb" };
    showFiles(() => Response.json({ files: [manual] }), ["knowledge_base_seed1", "another_kb"], [prior]);
    await screen.findByTestId("library-file");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Tùy chọn manual.pdf" }));
    await user.click(await screen.findByRole("menuitem", { name: "Trạng thái xử lý" }));
    expect(await screen.findByText(/Kho another_kb/)).toBeInTheDocument();
  });

  it("selects files and removes their knowledge source without deleting the library file", async () => {
    const fetch = showFiles((_url, init) => init?.method === "DELETE" ? new Response(null, { status: 204 }) : Response.json({ files: [manual] }));
    await screen.findByTestId("library-file");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Chọn manual.pdf" }));
    expect(screen.getByText("Đã chọn 1 file")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Gỡ khỏi kho kiến thức" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("File vẫn còn trong Thư viện tài liệu");
    await user.click(screen.getByTestId("files-confirm-delete"));
    await waitFor(() => expect(fetch.mock.calls.some(([url, init]) => url.endsWith("/sources/upload/s1") && init?.method === "DELETE")).toBe(true));
    expect(screen.getByTestId("library-file")).toBeInTheDocument();
  });

  it("reports a running job and keeps the file when source removal is refused", async () => {
    showFiles((_url, init) => init?.method === "DELETE" ? new Response(null, { status: 409 }) : Response.json({ files: [manual] }));
    await screen.findByTestId("library-file");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Tùy chọn manual.pdf" }));
    const remove = await screen.findByRole("menuitem", { name: "Gỡ khỏi kho kiến thức" });
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
