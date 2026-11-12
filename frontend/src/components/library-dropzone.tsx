import { useEffect, useRef, useState, type DragEvent } from "react";
import { Check, CloudUpload, Loader2 } from "lucide-react";
import { MAX_FILE_BYTES } from "@/services/file-api";

/** Progress of the upload batch the page is sending, if any. */
export interface UploadProgress {
  done: number;
  total: number;
  name: string;
}

const carriesFiles = (event: { dataTransfer: DataTransfer | null }) =>
  Array.from(event.dataTransfer?.types ?? []).includes("Files");

/**
 * Compact drop target for the file library.
 *
 * Shows one of five states: idle, armed (a file is being dragged somewhere on
 * the page), over (a file is above the zone), uploading and done (a short
 * confirmation after files were stored). Clicking or pressing Enter opens the
 * file picker. While mounted it also stops the browser from opening a file
 * that is dropped outside the zone.
 *
 * Args:
 *   disabled: Reason uploads are unavailable; the zone is inert when set.
 *   progress: Current upload batch, or null when idle.
 *   storedCount: Number of files stored by the last finished batch; the
 *     confirmation shows while it is above zero.
 *   onFiles: Called with the dropped files.
 *   onBrowse: Called when the user asks for the file picker.
 */
export function LibraryDropzone({
  disabled,
  progress,
  storedCount,
  onFiles,
  onBrowse,
}: {
  disabled: boolean;
  progress: UploadProgress | null;
  storedCount: number;
  onFiles(files: File[]): void;
  onBrowse(): void;
}) {
  const [armed, setArmed] = useState(false);
  const [over, setOver] = useState(false);
  const depth = useRef(0);

  useEffect(() => {
    const enter = (event: globalThis.DragEvent) => {
      if (!carriesFiles(event)) return;
      depth.current += 1;
      setArmed(true);
    };
    const leave = (event: globalThis.DragEvent) => {
      if (!carriesFiles(event)) return;
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setArmed(false);
    };
    const hover = (event: globalThis.DragEvent) => {
      if (carriesFiles(event)) event.preventDefault();
    };
    const drop = (event: globalThis.DragEvent) => {
      if (carriesFiles(event)) event.preventDefault();
      depth.current = 0;
      setArmed(false);
      setOver(false);
    };
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragleave", leave);
    window.addEventListener("dragover", hover);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragenter", enter);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("dragover", hover);
      window.removeEventListener("drop", drop);
    };
  }, []);

  const busy = progress !== null;
  const state = disabled
    ? "disabled"
    : busy
      ? "uploading"
      : over
        ? "over"
        : storedCount > 0 && !armed
          ? "done"
          : armed
            ? "armed"
            : "idle";

  function handleDrop(event: DragEvent<HTMLButtonElement>) {
    if (!carriesFiles(event)) return;
    event.preventDefault();
    setOver(false);
    if (disabled || busy) return;
    const files = Array.from(event.dataTransfer.files);
    if (files.length) onFiles(files);
  }

  const limit = `${MAX_FILE_BYTES / 1024 / 1024} MB`;

  return (
    <button
      type="button"
      className="library-drop"
      data-state={state}
      data-testid="files-dropzone"
      aria-disabled={disabled || busy}
      aria-busy={busy}
      onClick={() => {
        if (!disabled && !busy) onBrowse();
      }}
      onDragOver={(event) => {
        if (!carriesFiles(event)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = disabled || busy ? "none" : "copy";
        setOver(true);
      }}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOver(false);
      }}
      onDrop={handleDrop}
    >
      <span className="library-drop-icon" aria-hidden="true">
        {state === "uploading" ? <Loader2 className="animate-spin" /> : state === "done" ? <Check /> : <CloudUpload />}
      </span>
      {/* Keyed so the text cross-fades whenever the state changes. */}
      <span className="library-drop-text" key={state}>
        {state === "uploading" && progress ? (
          <>
            <strong>Đang tải lên {Math.min(progress.done + 1, progress.total)}/{progress.total}</strong>
            <span title={progress.name}>{progress.name}</span>
          </>
        ) : state === "over" ? (
          <>
            <strong>Thả tài liệu để tải lên</strong>
            <span>Buông chuột để bắt đầu</span>
          </>
        ) : state === "done" ? (
          <>
            <strong>Đã tải lên {storedCount} tài liệu</strong>
            <span>Tài liệu đã vào hàng đợi xử lý</span>
          </>
        ) : state === "disabled" ? (
          <>
            <strong>Chưa thể tải lên</strong>
            <span>Cần có kho kiến thức mặc định</span>
          </>
        ) : (
          <>
            <strong>Kéo thả tài liệu vào đây</strong>
            <span>
              hoặc <u>chọn từ máy</u>
            </span>
          </>
        )}
      </span>
      <span className="library-drop-hint">PDF, DOCX, XLSX, PNG, JPEG, TXT, MD · tối đa {limit}/file</span>
      <span className="library-drop-bar" aria-hidden="true" />
    </button>
  );
}
