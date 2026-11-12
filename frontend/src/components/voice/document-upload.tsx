import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { CheckCircle2, FileText, Loader2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  attachLibraryFile,
  uploadDocument,
  type StoredDocument,
} from "@/services/document-api";
import {
  ACCEPTED_EXTENSIONS,
  uploadFile,
  type LibraryFile,
} from "@/services/file-api";
import { useAuthClient } from "@/hooks/auth";
import type { AuthClient } from "@/services/auth-api";

interface UploadItem {
  file: File;
  document?: StoredDocument;
  libraryFile?: LibraryFile;
  error?: string;
}

function fileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  const unit = bytes < 1024 * 1024 ? "KiB" : "MiB";
  const value = bytes / (unit === "KiB" ? 1024 : 1024 * 1024);
  return `${value.toLocaleString("vi-VN", { maximumFractionDigits: 2 })} ${unit}`;
}

function documentLink(document: StoredDocument) {
  return `/knowledge/${encodeURIComponent(document.kb)}/sources/upload/${encodeURIComponent(document.id)}`;
}

export function DocumentUpload({
  kb,
  disabled,
  extractMode = null,
  onBusy,
  onStored,
  libraryClient,
}: {
  kb: string;
  disabled: boolean;
  /** Auto: queue conversion and extraction in this mode after each upload. */
  extractMode?: "knowledge" | "documents" | null;
  onBusy(busy: boolean): void;
  onStored?(): void;
  libraryClient?: AuthClient;
}) {
  const navigate = useNavigate();
  const [items, setItems] = useState<UploadItem[]>([]);
  const [sending, setSending] = useState(false);
  const [active, setActive] = useState<number | null>(null);
  const pendingCount = items.filter((item) => !item.document).length;
  const pending = pendingCount > 0;

  async function upload() {
    if (disabled || sending || !pending) return;
    setSending(true);
    onBusy(true);
    let stored = false;
    try {
      for (let index = 0; index < items.length; index++) {
        const item = items[index];
        if (item.document) continue;
        setActive(index);
        let libraryFile = item.libraryFile;
        try {
          if (libraryClient && !libraryFile) {
            libraryFile = await uploadFile(libraryClient, item.file);
            const savedFile = libraryFile;
            setItems((current) =>
              current.map((entry, i) =>
                i === index
                  ? { ...entry, libraryFile: savedFile, error: undefined }
                  : entry,
              ),
            );
          }
          const document =
            libraryClient && libraryFile
              ? await attachLibraryFile(
                  libraryClient,
                  kb,
                  libraryFile.id,
                  extractMode,
                )
              : await uploadDocument(kb, item.file, extractMode);
          stored = true;
          if (document.duplicate && pendingCount === 1) {
            void navigate(documentLink(document), {
              state: { notice: "File đã có trong KB" },
            });
            return;
          }
          setItems((current) =>
            current.map((entry, i) =>
              i === index ? { ...entry, document, error: undefined } : entry,
            ),
          );
        } catch (error) {
          setItems((current) =>
            current.map((entry, i) =>
              i === index
                ? {
                    ...entry,
                    error: `${libraryFile ? "File đã lưu trong Thư viện tài liệu. " : ""}${
                      error instanceof Error ? error.message : String(error)
                    }`,
                  }
                : entry,
            ),
          );
        }
      }
    } finally {
      setActive(null);
      setSending(false);
      onBusy(false);
      if (stored) onStored?.();
    }
  }

  return (
    <>
      <label className="vc-field vc-document-picker">
        <span>Chọn tài liệu</span>
        <input
          type="file"
          multiple
          accept={libraryClient ? ACCEPTED_EXTENSIONS.join(",") : undefined}
          disabled={sending}
          onChange={(event) => {
            const selected = Array.from(event.target.files ?? []);
            setItems((current) => [
              ...current,
              ...selected.map((file) => ({ file })),
            ]);
          }}
          data-testid="document-files"
          aria-describedby="document-upload-help"
        />
        <small id="document-upload-help">
          {libraryClient
            ? "PDF, XLSX, DOCX, JPEG, PNG, TXT, MD."
            : "PDF, hình ảnh, TXT, PPTX, DOCX, XLSX và các loại file khác."}{" "}
          Có thể chọn nhiều lần để thêm file vào danh sách, tối đa 100 MiB mỗi
          file.
        </small>
        <small role="status">Đang chờ tải lên: {pendingCount} file.</small>
      </label>

      <div className="vc-notice" role="note">
        <FileText size={18} />
        <p>
          {libraryClient && (
            <>
              File được lưu vào Thư viện tài liệu và liên kết với knowledge base.{" "}
            </>
          )}
          {extractMode
            ? "Auto: sau khi lưu file gốc, worker tự chuyển đổi và trích xuất. Theo dõi và duyệt kết quả trong "
            : "Tải lên chỉ lưu file gốc theo knowledge base. Chuyển đổi, trích xuất và duyệt kết quả trong "}
          <Link to="/files" className="link-button">
            Thư viện tài liệu
          </Link>
          .
        </p>
      </div>

      {!!items.length && (
        <ul className="vc-upload-list" aria-live="polite">
          {items.map((item, index) => (
            <li key={index}>
              <strong>{item.file.name}</strong>
              <small>{fileSize(item.file.size)}</small>
              {item.document ? (
                <span className="vc-upload-success">
                  <CheckCircle2 size={16} />{" "}
                  {item.document.duplicate
                    ? "File đã có trong KB"
                    : `Đã lưu vào ${item.document.kb}`}{" "}
                  · Mã: {item.document.id} ·{" "}
                  <Link
                    to={documentLink(item.document)}
                    className="link-button"
                  >
                    Xem / xử lý
                  </Link>
                  {item.document.job && (
                    <small data-testid="upload-job">
                      {" "}
                      · Đã xếp hàng chuyển đổi → trích xuất
                    </small>
                  )}
                  {item.document.job_error && (
                    <small className="vc-inline-error" data-testid="upload-job">
                      {" "}
                      · Chưa xếp được job tự động: {item.document.job_error}
                    </small>
                  )}
                </span>
              ) : sending && active === index ? (
                <span>Đang tải lên…</span>
              ) : item.error ? (
                <span className="vc-inline-error" role="alert">
                  {item.error}
                </span>
              ) : (
                <span>Chờ tải lên</span>
              )}
            </li>
          ))}
        </ul>
      )}

      <Button
        type="button"
        size="lg"
        className="vc-start-button"
        disabled={disabled || sending || !pending}
        onClick={() => void upload()}
        data-testid="document-upload"
      >
        {sending ? <Loader2 className="animate-spin" /> : <Upload />}
        {sending
          ? "Đang tải lên…"
          : items.some((item) => item.error)
            ? "Thử lại các file chưa lưu"
            : "Tải lên tài liệu"}
      </Button>
    </>
  );
}

/** Store bytes through the personal library, then attach them for KB processing. */
export function LibraryDocumentUpload(
  props: Omit<Parameters<typeof DocumentUpload>[0], "libraryClient">,
) {
  const client = useAuthClient();
  return <DocumentUpload {...props} libraryClient={client} />;
}
