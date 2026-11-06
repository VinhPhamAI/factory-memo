import { useEffect, useRef, useState } from "react";
import { Download, ExternalLink, FileText, Loader2 } from "lucide-react";
import { useAuthClient } from "@/hooks/auth";
import {
  FileApiError,
  fetchFileContent,
  fetchFilePreview,
  type LibraryFile,
} from "@/services/file-api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/** Largest original image loaded into the dialog; larger ones open separately. */
export const ORIGINAL_IMAGE_BUDGET_BYTES = 12 * 1024 * 1024;

const IMAGE_TYPES = ["image/png", "image/jpeg"];
const PDF_TYPE = "application/pdf";

type Preview =
  | { state: "loading" }
  | { state: "image"; url: string; original: boolean }
  | { state: "text"; text: string }
  | { state: "error"; message: string };

/** Explain a failed preview in plain words; 422 means no preview exists. */
function previewError(error: unknown): string {
  if (error instanceof FileApiError && error.status === 422)
    return "Tệp này không có bản xem trước. Bạn vẫn có thể tải xuống.";
  return error instanceof FileApiError
    ? error.message
    : "Không tải được bản xem trước.";
}

/**
 * Load what the dialog shows for one file and cancel it when the file changes.
 *
 * Images within the budget load their original bytes; PDFs and large images
 * use the bounded first-page preview; Office files return a text excerpt.
 * Blob URLs are revoked and late responses ignored after cancellation.
 *
 * Args:
 *   file: The file being previewed, or null when the dialog is closed.
 *
 * Returns:
 *   The current preview state, or null when no file is selected.
 */
function useLibraryPreview(file: LibraryFile | null): Preview | null {
  const client = useAuthClient();
  // Each result records its file so a previous file's preview never shows.
  const [preview, setPreview] = useState<(Preview & { for: string }) | null>(
    null,
  );
  const id = file?.id;
  const type = file?.content_type;
  const size = file?.size_bytes;

  useEffect(() => {
    if (!id) return;
    const abort = new AbortController();
    let objectUrl: string | undefined;
    const original =
      IMAGE_TYPES.includes(type ?? "") &&
      (size ?? Infinity) <= ORIGINAL_IMAGE_BUDGET_BYTES;
    const load = original
      ? fetchFileContent(client, id, abort.signal)
      : fetchFilePreview(client, id, abort.signal);
    load
      .then(async (blob) => {
        if (abort.signal.aborted) return;
        if (blob.type.startsWith("image/")) {
          objectUrl = URL.createObjectURL(blob);
          setPreview({ for: id, state: "image", url: objectUrl, original });
        } else if (blob.type.startsWith("text/plain")) {
          const text = await blob.text();
          if (!abort.signal.aborted)
            setPreview({ for: id, state: "text", text });
        } else {
          setPreview({ for: id, state: "error", message: previewError(null) });
        }
      })
      .catch((error: unknown) => {
        if (!abort.signal.aborted)
          setPreview({ for: id, state: "error", message: previewError(error) });
      });
    return () => {
      abort.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      // Drop the result so a revoked URL is never shown on reopen.
      setPreview(null);
    };
  }, [client, id, type, size]);

  if (!id) return null;
  return preview?.for === id ? preview : { state: "loading" };
}

/** Describe what part of the file the preview shows. */
function previewLabel(file: LibraryFile, preview: Preview | null): string {
  if (preview?.state === "image" && preview.original) return "Ảnh gốc";
  if (file.content_type === PDF_TYPE) return "Trang đầu";
  if (IMAGE_TYPES.includes(file.content_type)) return "Ảnh thu nhỏ";
  return "Trích đoạn";
}

/**
 * Show one library file in a dialog with its preview and original actions.
 *
 * Args:
 *   file: The selected file; null keeps the dialog closed.
 *   onClose: Called when the user dismisses the dialog.
 *   onOpenOriginal: Opens the original PDF or image in a new tab.
 *   onDownload: Saves the original file.
 */
export function LibraryFileDialog({
  file,
  onClose,
  onOpenOriginal,
  onDownload,
}: {
  file: LibraryFile | null;
  onClose: () => void;
  onOpenOriginal: (file: LibraryFile) => void;
  onDownload: (file: LibraryFile) => void;
}) {
  const preview = useLibraryPreview(file);
  // The file is cleared before the close animation ends, so keep its ID.
  const lastId = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (file) lastId.current = file.id;
  }, [file]);
  const isPdf = file?.content_type === PDF_TYPE;
  const isImage = IMAGE_TYPES.includes(file?.content_type ?? "");
  const showOpen =
    isPdf || (isImage && !(preview?.state === "image" && preview.original));

  return (
    <Dialog open={!!file} onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        className="library-file-dialog"
        data-testid="library-file-dialog"
        onCloseAutoFocus={(event) => {
          // Return focus to the file's preview button in the list.
          const opener = lastId.current
            ? document.getElementById(`library-open-${lastId.current}`)
            : null;
          if (opener) {
            event.preventDefault();
            opener.focus();
          }
        }}
      >
        {file && (
          <>
            <DialogHeader className="library-file-dialog-header">
              <DialogTitle className="library-file-dialog-title">
                {file.filename}
              </DialogTitle>
              <DialogDescription>
                {previewLabel(file, preview)}
                {!isPdf && !isImage && " · Chỉ hiển thị một phần nội dung"}
              </DialogDescription>
            </DialogHeader>
            <div
              className="library-file-dialog-body"
              aria-busy={preview?.state === "loading"}
            >
              {preview?.state === "image" ? (
                <img
                  src={preview.url}
                  alt={`${previewLabel(file, preview)} của ${file.filename}`}
                />
              ) : preview?.state === "text" ? (
                <pre data-testid="library-excerpt">{preview.text}</pre>
              ) : preview?.state === "error" ? (
                <p role="alert" className="library-file-dialog-message">
                  <FileText aria-hidden="true" />
                  {preview.message}
                </p>
              ) : (
                <p className="library-file-dialog-message">
                  <Loader2 className="animate-spin" aria-hidden="true" />
                  Đang tải bản xem trước…
                </p>
              )}
            </div>
            <div className="library-file-dialog-actions">
              {showOpen && (
                <Button variant="outline" onClick={() => onOpenOriginal(file)}>
                  <ExternalLink aria-hidden="true" />
                  {isPdf ? "Mở PDF" : "Mở ảnh gốc"}
                </Button>
              )}
              <Button onClick={() => onDownload(file)}>
                <Download aria-hidden="true" />
                Tải xuống
              </Button>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
